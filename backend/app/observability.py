"""Error reporting, with scrubbing configured BEFORE it can ever be switched on.

`MARK4.md` makes the order a gate: "Sentry, with PII scrubbing enabled *before*
it is switched on." The reason is that the first event is the one you cannot
un-send. A product whose pitch is "nobody sees what they aren't allowed to see"
cannot ship an error reporter that mails message bodies and document text to a
third party.

So this module is written and tested while SENTRY_DSN is unset. Setting the DSN
is the only remaining step, and by then the scrubbing already has tests.

THREE THINGS THAT LOOK RIGHT AND ARE NOT — all three were found in review of the
obvious configuration, not in production:

1. `LoggingIntegration(level=None)` does NOT mean "logs become breadcrumbs
   only". It DISABLES breadcrumb capture entirely, so events arrive with no
   preceding context and are much harder to act on.

2. `server_name=None` does not suppress the hostname. It is the default, and the
   SDK then falls back to `socket.gethostname()`. The field has to be removed in
   `before_send`.

3. A scrubbing test that only asserts "the secret is gone" passes trivially for
   a configuration that sends nothing useful at all. Then the first real
   incident produces an unactionable event, somebody loosens the config to see
   more, and the scrubbing goes with it. The test here asserts BOTH: secrets are
   gone AND the exception type, module, line number and request id survive.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

# Header and body keys whose values never leave this process.
_SENSITIVE_KEYS = frozenset({
    "authorization", "cookie", "set-cookie", "x-api-key", "api_key", "apikey",
    "password", "passwd", "secret", "token", "access_token", "refresh_token",
    "setup_token", "session", "openrouter_api_key", "supabase_service_role_key",
    "database_url", "voyage_api_key", "news_api_key", "sentry_dsn",
})

# Fields that are user CONTENT rather than credentials. Just as disqualifying:
# a message body or document chunk in an error report is the exact leak the
# product exists to prevent.
_CONTENT_KEYS = frozenset({
    "message", "text", "content", "prompt", "query", "answer", "transcript",
    "chunk", "chunks", "document", "documents", "note", "body", "email",
})

_REDACTED = "[redacted]"

# Values that look like credentials wherever they appear, including inside a
# string that no key name would have caught.
_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9\-_]{16,}"),                 # OpenAI / OpenRouter
    re.compile(r"sbp_[A-Za-z0-9]{16,}"),                   # Supabase
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+"),  # JWT
    re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s\"']+"),  # DSNs with creds
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),                # email addresses
)


def _scrub_string(s: str) -> str:
    for pat in _PATTERNS:
        s = pat.sub(_REDACTED, s)
    return s


def scrub(obj: Any, _depth: int = 0) -> Any:
    """Recursively redact credentials and user content.

    Depth-bounded: Sentry events nest, and an unbounded walk over a cyclic or
    pathological structure would hang the error path — the one place that must
    never itself fail.
    """
    if _depth > 12:
        return _REDACTED
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in _SENSITIVE_KEYS or lk in _CONTENT_KEYS:
                out[k] = _REDACTED
            else:
                out[k] = scrub(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [scrub(v, _depth + 1) for v in obj][:50]
    if isinstance(obj, str):
        return _scrub_string(obj)
    return obj


def before_send(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Last gate before an event leaves the process."""
    # Gotcha 2: server_name defaults to socket.gethostname(). Remove it rather
    # than setting it to None, which is what the default already is.
    event.pop("server_name", None)

    # Never send a request body or query string. Neither is ever worth the risk:
    # this is a product about who may read what.
    req = event.get("request")
    if isinstance(req, dict):
        req.pop("data", None)
        req.pop("cookies", None)
        qs = req.get("query_string")
        if qs:
            req["query_string"] = _REDACTED
        headers = req.get("headers")
        if isinstance(headers, dict):
            req["headers"] = {
                k: (_REDACTED if str(k).lower() in _SENSITIVE_KEYS else _scrub_string(str(v)))
                for k, v in headers.items()
            }

    # Identify the account, never the person. An id is enough to correlate; an
    # email is a data subject.
    user = event.get("user")
    if isinstance(user, dict):
        event["user"] = {"id": user.get("id")} if user.get("id") else {}

    for key in ("extra", "contexts", "tags", "breadcrumbs"):
        if key in event:
            event[key] = scrub(event[key])

    # Exception VALUES can contain the offending string. Types, modules and line
    # numbers are what make an event actionable, and they carry no user data.
    for exc in (event.get("exception", {}) or {}).get("values", []) or []:
        if isinstance(exc, dict) and exc.get("value"):
            exc["value"] = _scrub_string(str(exc["value"]))[:500]

    if isinstance(event.get("message"), str):
        event["message"] = _scrub_string(event["message"])

    return event


def init() -> bool:
    """Start Sentry if a DSN is configured. Returns whether it started.

    No DSN is the normal state and not an error — scrubbing is written and
    tested first, on purpose.
    """
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except Exception as e:
        print(f"[sentry] DSN set but sentry-sdk is unavailable: {e}", flush=True)
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("ENVIRONMENT", "production"),
        before_send=before_send,
        # Gotcha 1: level=None DISABLES breadcrumbs. INFO keeps the trail that
        # makes an event diagnosable; event_level=None stops log records from
        # becoming events in their own right.
        integrations=[LoggingIntegration(level=logging.INFO, event_level=None)],
        # Bodies are never attached. There is no sampling rate that makes
        # shipping a user's message text acceptable.
        max_request_body_size="never",
        send_default_pii=False,
        traces_sample_rate=0.0,
        release=os.environ.get("RAILWAY_GIT_COMMIT_SHA") or None,
    )
    print("[sentry] enabled with scrubbing", flush=True)
    return True

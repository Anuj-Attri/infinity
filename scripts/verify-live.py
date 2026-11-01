#!/usr/bin/env python3
"""Prove the LIVE deployment works. One command, no browser.

    python scripts/verify-live.py --url https://your-instance.example.com
    VERIFY_URL=https://your-instance.example.com python scripts/verify-live.py

Why this exists as a committed tool rather than a thing that gets retyped:
"is it up?" was asked many times during the first deploy and answered wrongly
more than once, because each ad-hoc check tested a different subset. `/health`
returning 200 says almost nothing — it is the ONE route that is exempt from the
access door, so it answers even when every other path is dark.

The order below is deliberate. The CLOSED assertions run FIRST. A script that
only walks the happy path passes identically against a wide-open service, which
is the failure that matters most here.

Reads ACCESS_TOKEN / SETUP_TOKEN / INFINITY_MASTER_PASSWORD from the environment,
falling back to the gitignored .env at the repo root. Never prints a secret; the
last check asserts the server did not print one either.

Exit code is 0 only if every check passed, so this is CI-safe.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import secrets
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = ""   # pass --url or set VERIFY_URL
REPO = pathlib.Path(__file__).resolve().parents[1]
UA = "infinity-verify-live/1.0"


def load_env() -> dict[str, str]:
    """Process environment wins; .env fills the gaps."""
    import os

    out: dict[str, str] = {}
    env_file = REPO / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                out[k.strip()] = v.strip()
    for k in ("ACCESS_TOKEN", "SETUP_TOKEN", "INFINITY_MASTER_PASSWORD", "VERIFY_URL"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


class Checker:
    def __init__(self, base: str, env: dict[str, str]) -> None:
        self.base = base.rstrip("/")
        self.env = env
        self.passed = 0
        self.failed: list[str] = []

    def req(self, path, *, method="GET", body=None, door=False, bearer=None, accept="application/json"):
        headers = {"User-Agent": UA, "Accept": accept}
        if door:
            headers["X-Access-Token"] = self.env["ACCESS_TOKEN"]
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        # A TRANSPORT failure is not a refusal, and reporting them identically
        # is how this script cries wolf. Pushing to main redeploys, and for a
        # few seconds the container is not accepting connections — which came
        # back as `/ serves the SPA: got None` alongside three more, looking
        # exactly like the app was broken. Two reruns seconds later: 24/24.
        #
        # So: retry ONLY the case where nothing answered, and only briefly. An
        # HTTP status is an answer and is never retried — a 403 must stay a 403,
        # or the door checks would paper over a real regression by waiting.
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    return r.status, r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode("utf-8", "replace")
            except Exception as e:  # network/TLS/DNS — nothing answered at all
                last = f"{type(e).__name__}: {e}"
                if attempt < 2:
                    print(f"        (no answer from {path} — {last}; retrying, "
                          f"a redeploy looks like this)")
                    time.sleep(5)
        return None, last or "unreachable"

    def check(self, label, got, want) -> bool:
        ok = got == want
        if ok:
            self.passed += 1
        else:
            self.failed.append(f"{label} (got {got!r}, wanted {want!r})")
        print(f"  {'PASS' if ok else 'FAIL'}  {label:<52} {got!r}")
        return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    args = ap.parse_args()

    env = load_env()
    args.url = args.url or env.get("VERIFY_URL", "")
    if not args.url:
        print("no deployment to check: pass --url https://your-instance or set VERIFY_URL")
        return 2
    missing = [k for k in ("ACCESS_TOKEN", "SETUP_TOKEN") if not env.get(k)]
    if missing:
        print(f"missing {', '.join(missing)} — set them in the environment or .env")
        return 2

    c = Checker(args.url, env)
    print(f"verifying {c.base}\n")

    # --- 1. the edge is not eating traffic ---------------------------------
    # Railway's Under Attack Mode answers 429 "rate limited" to non-browser
    # clients for EVERY path, /health included, and the app never sees it.
    # Distinguish that from an app problem before doing anything else.
    print("1. the edge is passing traffic through")
    code, body = c.req("/health")
    if code == 429 and "rate limited" in body:
        print("  FAIL  the EDGE is blocking, not the app.")
        print("        Under Attack Mode is almost certainly on. Fix:")
        print("          setServiceUnderAttackMode(input:{serviceId,environmentId,enabled:false})")
        print("        Then confirm underAttackModeUntil is null — 0 means ON.")
        return 1
    c.check("/health answers", code, 200)

    # --- 2. the door is shut ------------------------------------------------
    # With no token, no API path exists. Not 401, not 403 — 404, so the surface
    # cannot be enumerated and a stranger cannot tell there is anything here.
    print("\n2. the door is shut to anyone without the token")
    for path in ("/auth/status", "/admin/diagnostics", "/vault/stats", "/skills"):
        code, _ = c.req(path)
        c.check(f"no token: {path}", code, 404)

    # "/" is the deliberate exception, and asserting 404 on it was wrong.
    #
    # It used to return a bare JSON 404, which made the deployment unopenable by
    # its own owner — there was nowhere to type the key. It now sends a person
    # to the door. So the property to check is not the status code; it is that a
    # stranger does not get the APP. Checking the code alone would pass equally
    # well against a service that had started serving the SPA to everyone.
    code, body = c.req("/", accept="text/html")
    c.check("no token: / lands on the door, not the app", '<div id="root">' not in body, True)
    c.check("and the door has somewhere to type a key", 'id="k"' in body, True)
    c.check("and the door does not leak the key", (env.get("ACCESS_TOKEN", "\0") not in body), True)

    # A wrong key must not open anything, and must not confirm to a stranger
    # that there is something here worth attacking.
    bad = urllib.request.Request(c.base + "/", headers={"User-Agent": UA, "X-Access-Token": "wrong-" + secrets.token_hex(4)})
    try:
        with urllib.request.urlopen(bad, timeout=90) as r:
            code, bad_body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code, bad_body = e.code, ""
    except Exception as e:
        code, bad_body = f"{type(e).__name__}", ""
    # Both halves, because "the app is not in this response" passes trivially
    # against an EMPTY response — and an empty response is what a network
    # failure looks like. Asserting the door is present is what stops this
    # check from going green for the wrong reason.
    c.check("a wrong token still lands on the door", 'id="k"' in bad_body, True)
    c.check("a wrong token does not open the app", '<div id="root">' not in bad_body, True)

    # --- 3. the door opens for the real key --------------------------------
    print("\n3. the real token opens it")
    code, body = c.req("/", door=True, accept="text/html")
    c.check("/ serves the SPA", code, 200)
    c.check("and it is really the app", '<div id="root">' in body, True)

    code, body = c.req("/icon.svg", door=True, accept="image/svg+xml")
    c.check("/icon.svg is served", code, 200)

    code, body = c.req("/auth/status", door=True)
    c.check("/auth/status", code, 200)
    status = json.loads(body) if code == 200 else {}

    # --- 4. identity --------------------------------------------------------
    print("\n4. claim and sign in")
    pw = env.get("INFINITY_MASTER_PASSWORD")
    if not pw:
        print("  SKIP  no INFINITY_MASTER_PASSWORD; cannot test sign-in")
        return 1 if c.failed else 0

    if not status.get("password_set"):
        code, body = c.req("/auth/setup", method="POST", door=True,
                           body={"password": pw, "setup_token": env["SETUP_TOKEN"]})
        c.check("setup claims the instance", code, 200)
    else:
        print("  ....  already claimed; skipping setup")

    code, body = c.req("/auth/login", method="POST", door=True,
                       body={"password": pw, "device_name": "verify-live"})
    c.check("sign in", code, 200)
    token = json.loads(body).get("token") if code == 200 else None

    # --- 5. what the operator console reports ------------------------------
    print("\n5. what the deployment says about itself")
    if not token:
        c.failed.append("no session; skipped diagnostics")
        print("  FAIL  no session; skipped diagnostics")
    else:
        code, body = c.req("/admin/diagnostics", door=True, bearer=token)
        c.check("diagnostics", code, 200)
        if code == 200:
            d = json.loads(body)
            host = d["service"].get("hosting", {})
            print(f"        region={host.get('region')} env={host.get('environment')} "
                  f"proxy={host.get('behind_proxy')}")
            c.check("region is pinned to the US", str(host.get("region", "")).startswith("us-"), True)
            c.check("proxy is detected", host.get("behind_proxy"), True)

            cfg = {x["name"]: x["set"] for x in d["config"]}
            for key in ("ACCESS_TOKEN", "SETUP_TOKEN", "OPENROUTER_API_KEY", "DATA_DIR"):
                c.check(f"{key} is set", cfg.get(key), True)

            # DATA_DIR set is not the same as state SURVIVING. Ask persistence.
            persistence = next((s for s in d["subsystems"] if s["id"] == "persistence"), {})
            c.check("storage is persistent, not ephemeral", persistence.get("status"), "ok")

            # The console reports ON secrets. It must never report them.
            leaked = [k for k in ("ACCESS_TOKEN", "SETUP_TOKEN", "OPENROUTER_API_KEY",
                                  "NEWS_API_KEY", "INFINITY_MASTER_PASSWORD")
                      if env.get(k) and env[k] in body]
            c.check("no secret VALUE appears in diagnostics", leaked, [])

    print(f"\npassed {c.passed}, failed {len(c.failed)}")
    for f in c.failed:
        print("   FAILED:", f)
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())

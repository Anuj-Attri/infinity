"""The consent gate — the firewall between the model and anything that acts.

Ported from Mark 3's `test_mcp_gate.py`. The gate code itself came across
verbatim (AGENTS.md: "ports verbatim and is never softened") but its tests did
not, so the one invariant the repo says most loudly about itself was the one
with nothing holding it. A future edit that widened the read-name pattern,
dropped the internal-transport short-circuit, or flipped the fail-closed default
would have shipped green.

Two of Mark 3's eleven tests drove `mcp_shell`, which is deliberately gone —
there is no local machine to run commands on. The remaining nine are the gate
itself, and the fixture is repointed at `mcp_self`, the internal hand that
survived. It is a strictly better subject anyway: `mcp_self` is `internal`
transport, and internal is the case that must be gated no matter what a tool
claims about itself.

WHAT THIS PROTECTS. A browsed page or a poisoned document can put text in front
of the model. If that text can talk the model into calling a tool that writes,
buys, sends, or clicks, then the page is driving the user's account. The gate's
answer is that risk is decided by US from the tool's transport and name — never
by the server's own `readOnlyHint`, which is an assertion by the same party we
are trying to constrain.
"""

from __future__ import annotations

import asyncio

import pytest

from app import mcp_client, mcp_self

HTTP_REC = {"transport": "http"}
STDIO_REC = {"transport": "stdio"}
INTERNAL_REC = {"transport": "internal"}


# ── classify_risk ────────────────────────────────────────────────────────────

def test_internal_transport_is_always_gated():
    """Even when the tool insists it is read-only.

    Internal hands change the app's own state, so the annotation is irrelevant.
    """
    tool = {"name": "show_panel", "annotations": {"readOnlyHint": True}}  # lying annotation
    assert mcp_client.classify_risk(INTERNAL_REC, tool) == "gated"


def test_read_only_annotation_allows_auto_run():
    tool = {"name": "browser_snapshot", "annotations": {"readOnlyHint": True}}
    assert mcp_client.classify_risk(STDIO_REC, tool) == "read"


def test_gated_keyword_overrides_read_only_annotation():
    """A server cannot smuggle a destructive tool past the gate by lying.

    The verbs in the name win over the annotation, because the annotation is
    supplied by the party being constrained.
    """
    for name in ("write_file", "delete_page", "place_order", "buy_now",
                 "send_email", "browser_click", "run_script", "edit_file"):
        tool = {"name": name, "annotations": {"readOnlyHint": True}}
        assert mcp_client.classify_risk(STDIO_REC, tool) == "gated", name


def test_unannotated_unknown_tool_fails_closed_to_gated():
    """Unknown means gated. The default is the whole design."""
    assert mcp_client.classify_risk(HTTP_REC, {"name": "do_something"}) == "gated"


def test_read_prefix_fallback_when_no_annotations():
    for name in ("read_text_file", "list_directory", "get_file_info", "search_files"):
        assert mcp_client.classify_risk(STDIO_REC, {"name": name}) == "read", name


# ── call_tool enforcement ────────────────────────────────────────────────────

@pytest.fixture()
def fake_registry(monkeypatch):
    """One trusted internal hand, with mcp_self's real tool list."""
    rec = {"name": "Self", "transport": "internal", "module": "self", "trusted": True}
    monkeypatch.setattr(mcp_client, "_load", lambda: {"hand-self": rec})
    monkeypatch.setattr(mcp_client, "is_enabled", lambda: True)
    monkeypatch.setattr(mcp_client, "record_audit", lambda *a, **k: None)
    mcp_client._TOOLS_CACHE["hand-self"] = list(mcp_self.get_tools())
    yield
    mcp_client._TOOLS_CACHE.pop("hand-self", None)


def test_gated_tool_without_approval_raises(fake_registry):
    """The load-bearing one: no approval, no execution."""
    with pytest.raises(mcp_client.ApprovalRequired):
        asyncio.run(mcp_client.call_tool("hand-self", "show_panel", {"name": "vault"}))


def test_gated_tool_with_approval_executes(fake_registry):
    res = asyncio.run(mcp_client.call_tool("hand-self", "show_panel",
                                           {"name": "vault"}, approved=True))
    assert res["ok"] is True
    assert "vault" in res["text"]


def test_unlisted_tool_is_blocked_even_with_approval(fake_registry):
    """Approval covers the tool that was shown to the user, not any tool."""
    with pytest.raises(mcp_client.MCPError):
        asyncio.run(mcp_client.call_tool("hand-self", "not_a_tool", {}, approved=True))


def test_untrusted_server_is_blocked(monkeypatch):
    """Trust is a separate gate from approval, and it comes first."""
    rec = {"name": "X", "transport": "internal", "module": "self", "trusted": False}
    monkeypatch.setattr(mcp_client, "_load", lambda: {"x": rec})
    monkeypatch.setattr(mcp_client, "is_enabled", lambda: True)
    monkeypatch.setattr(mcp_client, "record_audit", lambda *a, **k: None)
    with pytest.raises(mcp_client.MCPError):
        asyncio.run(mcp_client.call_tool("x", "show_panel", {"name": "vault"}, approved=True))


# ── the hand itself ──────────────────────────────────────────────────────────

def test_the_self_hand_rejects_an_unknown_panel():
    """Replaces Mark 3's two shell tests, which needed the dropped hand.

    Same shape: the hand validates its own arguments rather than trusting them.
    """
    res = asyncio.run(mcp_self.call("show_panel", {"name": "blueprint"}))
    assert res["isError"] is True
    assert "unknown panel" in res["content"][0]["text"]


def test_the_self_hand_rejects_an_unknown_tool():
    res = asyncio.run(mcp_self.call("definitely_not_a_tool", {}))
    assert res["isError"] is True

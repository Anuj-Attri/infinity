"""Remote HTTP is the ONLY MCP transport now, so every MCP call is a request to
a URL the user chose.

Without a check, "connect an MCP server" is a primitive for making our backend
fetch anything reachable from inside our own infrastructure — the platform's
private network, a sibling service, or the link-local metadata endpoint that
hands out cloud credentials. The user does not even have to be malicious; a
copy-pasted URL is enough.
"""

from __future__ import annotations

import pytest

from app import mcp_client


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "http://127.0.0.1:8000/health",               # our own backend
    "http://localhost:5432/",                     # a sibling service
    "http://10.0.0.5/internal",                   # RFC1918
    "http://100.64.0.1/",                         # CGNAT / tailnet
    "http://[::1]:8000/",                         # IPv6 loopback
    "http://192.168.1.1/admin",                   # home router
])
def test_rpc_refuses_to_call_inward(url: str) -> None:
    import asyncio
    with pytest.raises(mcp_client.MCPError) as ei:
        asyncio.run(mcp_client._rpc(url, None, "tools/list", {}))
    assert "refused" in str(ei.value).lower(), f"{url} was not refused"


def test_connect_refuses_an_inward_url() -> None:
    import asyncio
    with pytest.raises(mcp_client.MCPError):
        asyncio.run(mcp_client.connect("evil", "http://169.254.169.254/", None))


def test_non_http_schemes_are_still_rejected_by_connect() -> None:
    import asyncio
    for url in ("file:///etc/passwd", "gopher://x/"):
        with pytest.raises(mcp_client.MCPError):
            asyncio.run(mcp_client.connect("evil", url, None))

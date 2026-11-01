"""Every panel the model is told about must actually open.

`PANELS` is not a passive list. It is injected into the system prompt and into
`show_panel`'s inputSchema enum, so the model is told, every single turn, that
each of these is something it can open for the user.

`"blueprint"` was in that list with no tile behind it. BlueprintTile is on Mark
4's dropped list, so the spawn fell through the client's `if (!builtIn) return;`
— and the caller was still told `"blueprint panel opened"`. The user approves a
consent prompt, waits, nothing appears, and the model reports success. That is
worse than the capability simply not existing: it spends the user's trust and
teaches the model a false fact about its own tools.

This test crosses the backend/frontend boundary on purpose. The list lives in
Python and the things it promises live in TypeScript, so neither side can check
it alone — which is exactly why the mismatch survived. The porting rule states
the rule; this enforces it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GRID_TSX = REPO / "frontend" / "src" / "components" / "TileGrid.tsx"
APP_TSX = REPO / "frontend" / "src" / "App.tsx"


def tile_ids() -> set[str]:
    """Tile ids the client can actually spawn.

    TILE_REGISTRY is NOT the right source — that is the "+" menu, and several
    real tiles (docs, mission, subagent) are spawnable without appearing in it.
    Using it here made this test claim `docs` was broken when it works fine.
    The spawnable set is TILE_DEFS plus the multi-instance kinds, which is what
    TileGrid's spawn() actually consults.
    """
    src = GRID_TSX.read_text(encoding="utf-8")
    single = set(re.findall(r'\bi:\s*"([a-z0-9_-]+)"', src))

    start = src.index("MULTI_INSTANCE_DEFAULTS")
    end = src.index("\n};", start)
    multi = set(re.findall(r'^\s*([a-z0-9_-]+):\s*\{', src[start:end], re.MULTILINE))

    ids = single | multi
    assert len(ids) >= 15, f"TileGrid parse looks wrong, only found {sorted(ids)}"
    return ids


def aliases() -> dict[str, str]:
    """Panel names App.tsx rewrites to a different tile before spawning."""
    src = APP_TSX.read_text(encoding="utf-8")
    start = src.index("PANEL_TO_TILE")
    end = src.index("};", start)
    found = dict(re.findall(r'([a-z]+):\s*"([a-z0-9_-]+)"', src[start:end]))
    assert found, "could not parse PANEL_TO_TILE — fix the parse, not this assert"
    return found


def test_every_advertised_panel_opens_something():
    from app.mcp_self import PANELS

    tiles = tile_ids()
    alias = aliases()
    # The client handles these itself instead of spawning a tile.
    handled_directly = {"model"}

    broken = []
    for panel in PANELS:
        if panel in handled_directly:
            continue
        target = alias.get(panel, panel)
        if target not in tiles:
            broken.append(f"{panel!r} -> {target!r}")

    assert not broken, (
        "PANELS advertises panels with no tile behind them: " + ", ".join(broken) +
        "\nThe model is told about these every turn and will offer them. Either "
        "add the tile or remove the name — do not leave a promise the app cannot "
        "keep, because the client reports success either way."
    )


def test_blueprint_specifically_is_gone():
    """The one that actually shipped broken."""
    from app.mcp_self import PANELS

    assert "blueprint" not in PANELS, (
        "BlueprintTile was dropped; advertising it makes the "
        "model offer a panel that silently never opens."
    )


def test_the_parsers_still_see_the_frontend():
    """If these files move, the test above passes vacuously. Fail loudly first."""
    assert GRID_TSX.exists(), f"{GRID_TSX} is gone — the check above is blind"
    assert APP_TSX.exists(), f"{APP_TSX} is gone — the check above is blind"
    assert len(tile_ids()) >= 15, "TileGrid parse looks wrong"


@pytest.mark.parametrize("panel", ["usage", "vault", "notes", "chat"])
def test_known_good_panels_resolve(panel):
    """Guards the resolver itself: if this ever fails, the parse broke, not PANELS."""
    from app.mcp_self import PANELS

    assert panel in PANELS
    assert aliases().get(panel, panel) in tile_ids()

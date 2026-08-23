"""A tool grant belongs to whoever gave it.

`nova_permissions.json` held one flat `{tool: level}` map for the whole
deployment. One account clicking "always allow" on `run_workflow` would have
authorised every other account's turns — the consent gate answering on behalf of
someone who never consented.

That is worse than an ordinary data leak in one specific way: it is invisible
from both sides. The account who granted it sees nothing unusual. The account
who benefits sees a tool call that simply did not stop to ask, which is
indistinguishable from one they approved themselves. Nothing in the UI, the
audit line, or the model's output marks it as borrowed consent.

AGENTS.md calls the MCP consent gate the firewall between the model and anything
that acts, and says it ports verbatim and is never softened. A shared grant store
softens it without touching a line of gate code.
"""

from __future__ import annotations

import importlib
import json

import pytest

A = "acct-alice"
B = "acct-bob"


@pytest.fixture
def main(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MARK4_DEV_ACCOUNT", raising=False)
    import app.paths, app.auth, app.main
    importlib.reload(app.paths)
    importlib.reload(app.auth)
    return importlib.reload(app.main)


def test_a_grant_is_stored_under_the_account_that_gave_it(main, tmp_path):
    main._save_permissions({"spawn_tile": "always"}, account_id=A)
    raw = json.loads((tmp_path / "nova_permissions.json").read_text(encoding="utf-8"))
    assert raw == {A: {"spawn_tile": "always"}}


def test_one_account_s_grant_does_not_authorise_another(main):
    """The whole point. Borrowed consent is indistinguishable from real consent
    at the moment the tool runs, so it has to be impossible rather than caught."""
    main._save_permissions({"run_workflow": "always"}, account_id=A)
    assert main._load_permissions(account_id=A) == {"run_workflow": "always"}
    assert main._load_permissions(account_id=B) == {}, (
        "another account inherited an always-allow grant it never gave"
    )


def test_revoking_yours_does_not_revoke_theirs(main):
    """The mirror image, and just as bad: a revocation that silently disarms
    somebody else's approval would make the gate ask again for no visible
    reason, and people learn to click through prompts that seem arbitrary."""
    main._save_permissions({"spawn_tile": "always"}, account_id=A)
    main._save_permissions({"spawn_tile": "always"}, account_id=B)
    main._save_permissions({}, account_id=A)
    assert main._load_permissions(account_id=A) == {}
    assert main._load_permissions(account_id=B) == {"spawn_tile": "always"}


def test_an_unknown_account_starts_with_nothing(main):
    """Deny by default. A missing entry must not read as "no restrictions"."""
    assert main._load_permissions(account_id="never-seen-before") == {}


def test_pre_ownership_grants_are_folded_under_the_single_tenant(main, tmp_path):
    """The old flat shape, migrated once on first read.

    Honest only while there IS one tenant — later, deciding whose clicks these
    were would be a guess, and a guess about consent is exactly the thing this
    file must never make.
    """
    from app.accounts import SINGLE_TENANT
    (tmp_path / "nova_permissions.json").write_text(
        json.dumps({"spawn_tile": "always", "run_workflow": "always"}), encoding="utf-8")

    got = main._load_permissions(account_id=SINGLE_TENANT.id)
    assert got == {"spawn_tile": "always", "run_workflow": "always"}

    # And nobody else inherited them.
    assert main._load_permissions(account_id=B) == {}

    raw = json.loads((tmp_path / "nova_permissions.json").read_text(encoding="utf-8"))
    assert set(raw) == {SINGLE_TENANT.id}, "the file was not rewritten in the owned shape"


def test_a_corrupt_file_denies_rather_than_crashes(main, tmp_path):
    """Fail closed: an unreadable grant store means NO grants, not an error the
    caller might paper over — and certainly not everything allowed."""
    (tmp_path / "nova_permissions.json").write_text("{not json", encoding="utf-8")
    assert main._load_permissions(account_id=A) == {}


def test_the_routes_use_the_caller_s_account(main):
    """The store being per-account is worthless if the routes ignore it."""
    import inspect
    for name in ("nova_permissions_get", "nova_permissions_set", "nova_permissions_delete"):
        src = inspect.getsource(getattr(main, name))
        assert "_account_id()" in src, f"{name} does not resolve the caller's account"

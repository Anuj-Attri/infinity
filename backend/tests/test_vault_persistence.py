"""The vault's notes and its index must live on the same disk.

This exists because of a specific near-miss. `VAULT_DIR` was computed from the
repo root while `_DB_PATH` went through `data_path()`. On a desktop that is the
same disk and nothing is wrong. In a container it means the notes sat on `/srv`
— wiped by every redeploy — while the index sat on the mounted volume.

The consequence is not "the vault looks empty". `scan()` reconciles the index
against the directory, so after one deploy it would find zero files, conclude
every note had been deleted, and DELETE the rows — including `chunks.text`,
which by then held the only surviving copy of the note bodies. The boot banner
and the operator console would both still report storage as persistent, because
`DATA_DIR` really was set. Silent, total, unrecoverable.

Two independent defences, and this file holds both:

1. `VAULT_DIR` goes through `data_path()`, like every other piece of state.
2. `scan()` refuses to mass-delete when it scanned nothing at all, because an
   empty directory and a missing disk are indistinguishable from inside — and
   only one of those readings is survivable.

Defence 2 matters even though defence 1 fixes today's bug: the next wrong path
is not required to announce itself.
"""


from __future__ import annotations

import asyncio
import importlib

import pytest

ACCT = "00000000-0000-0000-0000-00000000000f"   # SINGLE_TENANT


@pytest.fixture
def vault(monkeypatch, tmp_path):
    """A vault module whose state lives entirely under a throwaway DATA_DIR."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VAULT_DIR", raising=False)
    import app.paths
    importlib.reload(app.paths)
    import app.vault
    return importlib.reload(app.vault)


def write_note(v, name: str, body: str) -> None:
    p = v.vault_dir(ACCT) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# {name}\n\n{body}\n", encoding="utf-8")


def scan(v, **kw):
    kw.setdefault("account_id", ACCT)
    return asyncio.run(v.scan(**kw))


def count_nodes(v) -> int:
    return v._db().execute("SELECT COUNT(*) FROM nodes").fetchone()[0]


def test_notes_live_under_the_data_dir_not_the_repo(vault, tmp_path):
    """The whole bug in one assertion."""
    assert vault.vault_dir(ACCT) == tmp_path / "vault" / ACCT, (
        "the vault must come from data_path() AND be per-account. Computing it "
        "from the repo root puts notes on the container's own disk while the "
        "index is on the volume; sharing one directory means two accounts "
        "writing the same relative path write the same file."
    )
    # And the index must be on that same disk, or they can still diverge.
    assert vault._DB_PATH.parent == tmp_path


def test_an_explicit_VAULT_DIR_still_wins(monkeypatch, tmp_path):
    """An operator must be able to point this at a different mount."""
    elsewhere = tmp_path / "somewhere-else"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VAULT_DIR", str(elsewhere))
    import app.paths, app.vault
    importlib.reload(app.paths)
    v = importlib.reload(app.vault)
    assert v.vault_dir(ACCT) == elsewhere / ACCT


def test_a_real_deletion_is_still_reconciled(vault):
    """The guard must not be so broad that it stops the feature working."""
    write_note(vault, "one.md", "first note")
    write_note(vault, "two.md", "second note")
    assert scan(vault, force=True)["indexed"] == 2
    assert count_nodes(vault) == 2

    (vault.vault_dir(ACCT) / "one.md").unlink()
    out = scan(vault, force=True)
    assert out["removed"] == 1, "deleting one note of two must still reconcile"
    assert count_nodes(vault) == 1


def test_an_empty_directory_does_not_wipe_the_index(vault, capsys):
    """A missing volume must not be read as "the user deleted everything"."""
    write_note(vault, "keep.md", "irreplaceable")
    write_note(vault, "keep2.md", "also irreplaceable")
    assert scan(vault, force=True)["indexed"] == 2

    # Exactly what a redeploy onto a wrong/unmounted path looks like: the index
    # is intact and the directory is gone.
    for p in vault.vault_dir(ACCT).rglob("*.md"):
        p.unlink()

    out = scan(vault, force=True)
    assert out["removed"] == 0, "an empty scan must not delete the whole index"
    assert out.get("protected") == 2
    assert count_nodes(vault) == 2, "the notes were destroyed"
    assert "REFUSING" in capsys.readouterr().out, "the refusal must be loud"


def test_the_chunk_text_survives_an_empty_scan(vault):
    """chunks.text is the last copy of the body once the file is gone."""
    write_note(vault, "precious.md", "the only copy of this sentence")
    scan(vault, force=True)
    (vault.vault_dir(ACCT) / "precious.md").unlink()

    scan(vault, force=True)
    rows = vault._db().execute("SELECT text FROM chunks").fetchall()
    assert any("the only copy of this sentence" in r[0] for r in rows), (
        "the note body was destroyed by a scan of an empty directory"
    )


def test_recovery_clears_the_protection(vault):
    """Once the disk is back, normal reconciliation resumes."""
    write_note(vault, "a.md", "one")
    write_note(vault, "b.md", "two")
    scan(vault, force=True)
    for p in vault.vault_dir(ACCT).rglob("*.md"):
        p.unlink()
    assert scan(vault, force=True)["removed"] == 0      # protected

    # The volume comes back with only one of the two notes on it.
    write_note(vault, "a.md", "one")
    out = scan(vault, force=True)
    assert out["removed"] == 1, "b.md is genuinely gone and should reconcile now"
    assert count_nodes(vault) == 1

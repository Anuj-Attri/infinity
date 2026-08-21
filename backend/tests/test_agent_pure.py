"""Pure-logic tests: mission graph contracts + context budget.
Run: pytest backend/tests/test_agent_pure.py -q   (no app, no network)"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.schemas import (  # noqa: E402
    MissionSpec, NodeSpec, OutputContract,
)
from app import context_budget as cb  # noqa: E402


def _spec(nodes):
    return MissionSpec(goal="test goal", nodes=nodes)


def test_topo_layers_parallel_and_sequential():
    spec = _spec([
        NodeSpec(id="a", task="t"),
        NodeSpec(id="b", task="t"),
        NodeSpec(id="c", task="t", depends_on=["a", "b"]),
    ])
    assert spec.topo_layers() == [["a", "b"], ["c"]]


def test_cycle_fails_closed():
    with pytest.raises(ValueError, match="cycle"):
        _spec([
            NodeSpec(id="a", task="t", depends_on=["b"]),
            NodeSpec(id="b", task="t", depends_on=["a"]),
        ]).topo_layers()


def test_unknown_dep_rejected():
    with pytest.raises(ValueError, match="unknown"):
        _spec([NodeSpec(id="a", task="t", depends_on=["ghost"])])


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        _spec([NodeSpec(id="a", task="t"), NodeSpec(id="a", task="t2")])


def test_contract_checks():
    c = OutputContract(min_chars=10, must_mention=["NVDA"])
    assert c.check("") == ["empty output"]
    assert any("too short" in p for p in c.check("hi"))
    assert any("missing required mention" in p for p in c.check("long enough text here"))
    assert c.check("NVDA moved 3% on volume today.") == []


def test_contract_json_keys():
    c = OutputContract(format="json", min_chars=2, json_required_keys=["x"])
    assert c.check('{"x": 1}') == []
    assert any("missing key" in p for p in c.check('{"y": 1}'))
    assert any("not valid JSON" in p for p in c.check("nope"))
    # fenced JSON accepted
    assert c.check('```json\n{"x": 1}\n```') == []


def _msgs(n_history: int, chars: int = 400):
    head = [{"role": "system", "content": "PERSONA " + "p" * 200}]
    hist = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": f"turn{i} " + "x" * chars} for i in range(n_history)]
    tail = [{"role": "user", "content": "the current question"}]
    return head + hist + tail


def test_budget_noop_when_under_limit():
    msgs = _msgs(4)
    out, dropped = cb.apply_budget(msgs, limit_tokens=100_000, head_count=1)
    assert out == msgs and dropped == 0


def test_budget_drops_oldest_first_and_marks():
    msgs = _msgs(20, chars=800)
    out, dropped = cb.apply_budget(msgs, limit_tokens=1200, head_count=1)
    assert dropped > 0
    assert out[0]["content"].startswith("PERSONA")          # head pinned
    assert out[-1]["content"] == "the current question"     # tail pinned
    assert "compacted" in out[1]["content"]                  # marker present
    # newest history survives
    kept_turns = [m for m in out if m["content"].startswith("turn")]
    assert kept_turns and kept_turns[-1]["content"].startswith(f"turn{19}")


def test_budget_deterministic():
    msgs = _msgs(12, chars=600)
    a = cb.apply_budget(msgs, limit_tokens=1500, head_count=1)
    b = cb.apply_budget(msgs, limit_tokens=1500, head_count=1)
    assert a == b


def test_budget_summary_in_marker():
    msgs = _msgs(20, chars=800)
    out, dropped = cb.apply_budget(
        msgs, limit_tokens=1200, head_count=1, summary="key facts here"
    )
    assert dropped > 0
    marker = out[1]["content"]
    assert "key facts here" in marker

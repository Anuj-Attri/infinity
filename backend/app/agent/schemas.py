"""Typed contracts for the agent runtime. Pure: stdlib + pydantic only,
so these models are unit-testable without the app or any provider.

The wrapper thesis: every non-deterministic step (an LLM call) is boxed by
a deterministic contract — validated input, validated output, bounded
retries, explicit state. The DAG is data; the executor is dumb and honest.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Tier = Literal["fast", "smart"]
NodeStatus = Literal[
    "pending", "running", "verifying", "retrying", "done", "failed", "skipped"
]
MissionStatus = Literal[
    "awaiting_approval", "running", "synthesizing", "done", "failed", "stopped"
]

_ID_RE = re.compile(r"^[a-zA-Z][\w-]{0,39}$")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class OutputContract(BaseModel):
    """Deterministic acceptance criteria for a node's output. Checked in
    code BEFORE any model-judged verification. Everything optional —
    an empty contract accepts any non-empty output."""

    format: Literal["markdown", "json", "text"] = "markdown"
    min_chars: int = 80
    max_chars: int = 24_000
    must_mention: list[str] = Field(default_factory=list)  # case-insensitive
    json_required_keys: list[str] = Field(default_factory=list)  # format=json only

    def check(self, output: str) -> list[str]:
        """Return a list of violations (empty = pass). Never raises."""
        problems: list[str] = []
        text = (output or "").strip()
        if not text:
            return ["empty output"]
        if len(text) < self.min_chars:
            problems.append(f"too short ({len(text)} < {self.min_chars} chars)")
        if len(text) > self.max_chars:
            problems.append(f"too long ({len(text)} > {self.max_chars} chars)")
        low = text.lower()
        for term in self.must_mention:
            if term and term.lower() not in low:
                problems.append(f"missing required mention: {term!r}")
        if self.format == "json":
            import json as _json

            try:
                obj = _json.loads(_strip_fence(text))
            except Exception:
                problems.append("not valid JSON")
            else:
                if isinstance(obj, dict):
                    for k in self.json_required_keys:
                        if k not in obj:
                            problems.append(f"JSON missing key: {k!r}")
                elif self.json_required_keys:
                    problems.append("JSON is not an object")
        return problems


def _strip_fence(s: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", s, re.DOTALL)
    return m.group(1) if m else s


class NodeSpec(BaseModel):
    """One sub-task. `depends_on` gives the DAG its edges; nodes with
    satisfied dependencies run concurrently."""

    id: str
    role: str = "agent"
    task: str
    tier: Tier = "fast"
    est_tokens: int = 1500
    depends_on: list[str] = Field(default_factory=list)
    contract: OutputContract = Field(default_factory=OutputContract)

    @field_validator("id")
    @classmethod
    def _id_ok(cls, v: str) -> str:
        if not _ID_RE.match(v or ""):
            raise ValueError("node id must be alnum/underscore/dash, <=40 chars")
        return v

    @field_validator("task")
    @classmethod
    def _task_ok(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("empty task")
        return v[:4000]


class MissionSpec(BaseModel):
    """The approved plan. Created from the /mission/plan output (plus any
    user edits in the tile) and validated here before anything runs."""

    id: str = Field(default_factory=lambda: new_id("mi"))
    title: str = "Mission"
    goal: str
    model: str | None = None       # chat model for the synthesis turn
    nodes: list[NodeSpec] = Field(default_factory=list)
    max_concurrency: int = 3
    max_attempts: int = 3          # 1 try + up to 2 critique-retries per node

    @field_validator("goal")
    @classmethod
    def _goal_ok(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("empty goal")
        return v[:4000]

    @field_validator("nodes")
    @classmethod
    def _nodes_ok(cls, nodes: list[NodeSpec]) -> list[NodeSpec]:
        if not nodes:
            raise ValueError("mission has no nodes")
        if len(nodes) > 12:
            raise ValueError("mission too large (max 12 nodes)")
        ids = [n.id for n in nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate node ids")
        known = set(ids)
        for n in nodes:
            for d in n.depends_on:
                if d not in known:
                    raise ValueError(f"node {n.id!r} depends on unknown {d!r}")
                if d == n.id:
                    raise ValueError(f"node {n.id!r} depends on itself")
        return nodes

    def topo_layers(self) -> list[list[str]]:
        """Kahn's algorithm → layers of node ids that may run concurrently.
        Raises ValueError on a cycle (fail-closed before any dispatch)."""
        indeg: dict[str, int] = {n.id: len(n.depends_on) for n in self.nodes}
        children: dict[str, list[str]] = {n.id: [] for n in self.nodes}
        for n in self.nodes:
            for d in n.depends_on:
                children[d].append(n.id)
        layers: list[list[str]] = []
        ready = sorted([i for i, k in indeg.items() if k == 0])
        seen = 0
        while ready:
            layers.append(ready)
            nxt: list[str] = []
            for i in ready:
                seen += 1
                for c in children[i]:
                    indeg[c] -= 1
                    if indeg[c] == 0:
                        nxt.append(c)
            ready = sorted(nxt)
        if seen != len(self.nodes):
            raise ValueError("dependency cycle in mission graph")
        return layers


class Verdict(BaseModel):
    ok: bool
    reasons: list[str] = Field(default_factory=list)
    source: Literal["contract", "evaluator", "evaluator-unavailable"] = "contract"


class NodeState(BaseModel):
    id: str
    status: NodeStatus = "pending"
    attempts: int = 0
    output: str = ""              # full output (ledger; not sent cross-node)
    distilled: str = ""           # condensed summary (what synthesis sees)
    verdict: Verdict | None = None
    error: str = ""
    model: str = ""
    started_at: float | None = None
    finished_at: float | None = None

    def brief(self) -> dict[str, Any]:
        """Wire shape for the tile — full output included only when done,
        so live updates stay light."""
        return {
            "id": self.id,
            "status": self.status,
            "attempts": self.attempts,
            "error": self.error,
            "model": self.model,
            "distilled": self.distilled if self.status == "done" else "",
            "output_chars": len(self.output),
        }


class MissionState(BaseModel):
    id: str
    # WHOSE mission. Carried on the state rather than passed alongside it,
    # because _emit() persists on every transition and threading an account
    # through all of those would be a lot of plumbing to express one fact that
    # never changes for the life of the mission.
    account_id: str = ""
    title: str
    goal: str
    status: MissionStatus = "awaiting_approval"
    spec: MissionSpec
    nodes: dict[str, NodeState] = Field(default_factory=dict)
    synthesis: str = ""
    error: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    @classmethod
    def from_spec(cls, spec: MissionSpec, *, account_id: str = "") -> "MissionState":
        return cls(
            id=spec.id,
            account_id=account_id,
            title=spec.title,
            goal=spec.goal,
            spec=spec,
            nodes={n.id: NodeState(id=n.id) for n in spec.nodes},
        )

    def touch(self) -> None:
        self.updated_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        """Wire shape for GET /mission/{id} and the event stream."""
        return {
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            "status": self.status,
            "nodes": [self.nodes[n.id].brief() for n in self.spec.nodes],
            "synthesis": self.synthesis,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

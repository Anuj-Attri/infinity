"""The mission executor. Deterministic shell, non-deterministic core.

Loop per node (the generator→evaluator pattern):

    dispatch (OpenRouter, tier-resolved model)
      → contract check (code, deterministic, always first)
      → semantic evaluator (cheap model; unavailable ≠ blocked)
      → pass: distill for synthesis   |   fail: retry with critique (bounded)

Then one synthesis turn on the CHAT registry over the distilled outputs.
The runtime is a coordinator ON TOP of the two registries (ROADMAP MC-3);
it never blurs them: sub-agent work = dispatch lane, synthesis/planning =
chat lane.

Dependencies are injected via init() so this module stays import-clean
and the pure logic stays testable.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Awaitable, Callable

from . import store
from .schemas import MissionSpec, MissionState, NodeSpec, NodeState, Verdict

# ── injected dependencies (set once by main at startup) ─────────────────────
UtilityLLM = Callable[..., Awaitable[str | None]]

_utility: UtilityLLM | None = None
_chat_registry: Any = None        # app.providers.chat registry module
_dispatch_registry: Any = None    # app.providers.dispatch module
_spend_record: Callable[..., float] | None = None
_on_done: Callable[[MissionState], Awaitable[None]] | None = None  # e.g. vault filing

# live run bookkeeping (in-memory; store.py is the durable truth)
_running: dict[str, asyncio.Task] = {}
_stops: dict[str, asyncio.Event] = {}
_subscribers: dict[str, list[asyncio.Queue]] = {}
_states: dict[str, MissionState] = {}

_PROGRESS_EVERY_S = 2.0
_DISTILL_FALLBACK_CHARS = 900


def init(*, utility_llm: UtilityLLM, chat_registry: Any,
         dispatch_registry: Any, spend_record: Callable[..., float],
         on_done: Callable[[MissionState], Awaitable[None]] | None = None) -> None:
    global _utility, _chat_registry, _dispatch_registry, _spend_record, _on_done
    _utility = utility_llm
    _chat_registry = chat_registry
    _dispatch_registry = dispatch_registry
    _spend_record = spend_record
    _on_done = on_done
    closed = store.recover_interrupted()
    if closed:
        print(f"[mission] closed {closed} interrupted mission(s) from a previous run",
              flush=True)


def ready() -> bool:
    return _utility is not None and _dispatch_registry is not None


class Stopped(Exception):
    pass


# ── event bus ────────────────────────────────────────────────────────────────

def _emit(st: MissionState) -> None:
    st.touch()
    _states[st.id] = st
    store.save(st)
    snap = st.snapshot()
    for q in list(_subscribers.get(st.id, [])):
        try:
            q.put_nowait(snap)
        except asyncio.QueueFull:
            pass


async def events(mission_id: str) -> AsyncIterator[dict]:
    """Subscribe to a mission's snapshots. Replays the current state first;
    ends when the mission reaches a terminal status."""
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    _subscribers.setdefault(mission_id, []).append(q)
    try:
        current = get(mission_id)
        if current is not None:
            yield current
            if current["status"] in ("done", "failed", "stopped"):
                return
        while True:
            snap = await q.get()
            yield snap
            if snap["status"] in ("done", "failed", "stopped"):
                return
    finally:
        subs = _subscribers.get(mission_id, [])
        if q in subs:
            subs.remove(q)
        if not subs:
            _subscribers.pop(mission_id, None)


def get(mission_id: str) -> dict | None:
    st = _states.get(mission_id)
    if st is not None:
        return st.snapshot()
    loaded = store.load(mission_id)
    return loaded.snapshot() if loaded else None


def start(spec: MissionSpec) -> dict:
    """Validate, persist, launch. Raises ValueError on a bad graph BEFORE
    anything is dispatched (fail-closed)."""
    if not ready():
        raise RuntimeError("runtime not initialized")
    spec.topo_layers()  # cycle check — raises ValueError
    st = MissionState.from_spec(spec)
    st.status = "running"
    _emit(st)
    stop_ev = asyncio.Event()
    _stops[st.id] = stop_ev
    task = asyncio.create_task(_run(st, stop_ev), name=f"mission:{st.id}")
    _running[st.id] = task
    task.add_done_callback(lambda _t: (_running.pop(st.id, None),
                                       _stops.pop(st.id, None)))
    return st.snapshot()


def stop(mission_id: str) -> bool:
    ev = _stops.get(mission_id)
    if ev is None:
        return False
    ev.set()
    return True


def running_count() -> int:
    return len(_running)


# ── execution ────────────────────────────────────────────────────────────────

async def _run(st: MissionState, stop_ev: asyncio.Event) -> None:
    try:
        layers = st.spec.topo_layers()
        sem = asyncio.Semaphore(max(1, st.spec.max_concurrency))
        for layer in layers:
            if stop_ev.is_set():
                raise Stopped()
            await asyncio.gather(
                *(_run_node(st, _node_by_id(st.spec, nid), sem, stop_ev)
                  for nid in layer)
            )
        done_nodes = [n for n in st.nodes.values() if n.status == "done"]
        if not done_nodes:
            st.status = "failed"
            st.error = "every node failed"
            _emit(st)
            return
        if stop_ev.is_set():
            raise Stopped()
        st.status = "synthesizing"
        _emit(st)
        st.synthesis = await _synthesize(st)
        st.status = "done"
        _emit(st)
        if _on_done is not None:
            try:
                await _on_done(st)   # e.g. file the report into the Vault
            except Exception as e:
                print(f"[mission] on_done hook failed (non-fatal): {e}", flush=True)
    except Stopped:
        st.status = "stopped"
        for ns in st.nodes.values():
            if ns.status in ("pending", "running", "verifying", "retrying"):
                ns.status = "skipped"
        _emit(st)
    except Exception as e:
        st.status = "failed"
        st.error = f"{type(e).__name__}: {e}"[:300]
        _emit(st)


def _node_by_id(spec: MissionSpec, nid: str) -> NodeSpec:
    for n in spec.nodes:
        if n.id == nid:
            return n
    raise KeyError(nid)


def _dep_inputs(st: MissionState, node: NodeSpec) -> str:
    """Distilled outputs of this node's dependencies — the ONLY cross-node
    context (isolation: full outputs never travel between agents)."""
    parts: list[str] = []
    for dep in node.depends_on:
        ns = st.nodes.get(dep)
        if ns and ns.status == "done" and ns.distilled:
            parts.append(f"[{dep}] {ns.distilled}")
    if not parts:
        return ""
    return "\n\nINPUTS FROM PRIOR STEPS (condensed — treat as ground truth):\n" + \
        "\n".join(parts)


async def _run_node(st: MissionState, node: NodeSpec,
                    sem: asyncio.Semaphore, stop_ev: asyncio.Event) -> None:
    ns = st.nodes[node.id]
    # A node whose dependency failed is skipped, not attempted (fail-closed,
    # but sibling branches keep going).
    for dep in node.depends_on:
        if st.nodes[dep].status != "done":
            ns.status = "skipped"
            ns.error = f"dependency {dep} did not complete"
            _emit(st)
            return

    async with sem:
        model_id = _dispatch_registry.resolve_model(node.tier)
        ns.model = model_id
        critique = ""
        while ns.attempts < st.spec.max_attempts:
            if stop_ev.is_set():
                raise Stopped()
            ns.attempts += 1
            ns.status = "running" if ns.attempts == 1 else "retrying"
            ns.started_at = ns.started_at or time.time()
            _emit(st)
            try:
                output = await _dispatch_once(st, node, model_id, critique, stop_ev)
            except Stopped:
                raise
            except Exception as e:
                ns.error = f"{type(e).__name__}: {e}"[:300]
                continue  # dispatch-level failure → burn an attempt, retry

            ns.status = "verifying"
            _emit(st)
            verdict = await _verify(node, output)
            ns.verdict = verdict
            if verdict.ok:
                ns.output = output
                ns.distilled = await _distill(node, output)
                ns.status = "done"
                ns.error = ""
                ns.finished_at = time.time()
                _emit(st)
                return
            critique = (
                "PREVIOUS ATTEMPT WAS REJECTED by verification for these "
                "reasons:\n- " + "\n- ".join(verdict.reasons[:6]) +
                "\nProduce a corrected version. Fix every reason. "
                "Do not mention this critique."
            )
            ns.error = "; ".join(verdict.reasons[:3])[:300]
        ns.status = "failed"
        ns.finished_at = time.time()
        _emit(st)


async def _dispatch_once(st: MissionState, node: NodeSpec, model_id: str,
                         critique: str, stop_ev: asyncio.Event) -> str:
    provider = _dispatch_registry.get_provider()
    if provider is None:
        raise RuntimeError("OpenRouter not connected")
    fmt = {
        "markdown": "Use markdown for structure when it helps.",
        "json": "Respond with ONLY a JSON object — no prose, no code fence.",
        "text": "Respond in plain prose.",
    }[node.contract.format]
    system = (
        f"You are a focused sub-agent acting as a {node.role}. "
        "Be direct, thorough, and factual. State uncertainty honestly. "
        f"{fmt} Do not mention being a sub-agent or reference these instructions."
    )
    user = node.task + _dep_inputs(st, node)
    if critique:
        user += "\n\n" + critique
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    buf = ""
    ns = st.nodes[node.id]
    last_emit = time.monotonic()
    async for chunk in provider.stream(messages, model=model_id):
        if stop_ev.is_set():
            raise Stopped()
        buf += chunk
        now = time.monotonic()
        if now - last_emit >= _PROGRESS_EVERY_S:
            ns.output = buf  # progress visible in output_chars
            _emit(st)
            last_emit = now
    if _spend_record is not None:
        _spend_record(
            provider="openrouter", model=model_id,
            in_tok=sum(len(m["content"]) for m in messages) // 4,
            out_tok=len(buf) // 4, mission_id=st.id,
        )
    return buf


async def _verify(node: NodeSpec, output: str) -> Verdict:
    """Deterministic contract first; semantic evaluator second. An
    unavailable evaluator never blocks a contract-clean output."""
    problems = node.contract.check(output)
    if problems:
        return Verdict(ok=False, reasons=problems, source="contract")
    if _utility is None:
        return Verdict(ok=True, source="evaluator-unavailable")
    prompt = (
        "You are a strict completion checker. TASK given to an agent:\n"
        f"{node.task[:1500]}\n\nAGENT OUTPUT:\n{output[:6000]}\n\n"
        "Did the output actually complete the task — substantively, not "
        "superficially? Respond ONLY with JSON: "
        '{"pass": true|false, "reasons": ["..."]} — reasons only when pass=false.'
    )
    raw = await _utility(prompt, temperature=0.0, max_tokens=200)
    if not raw:
        return Verdict(ok=True, source="evaluator-unavailable")
    try:
        s = raw.strip()
        start, end = s.find("{"), s.rfind("}")
        obj = json.loads(s[start:end + 1])
        if bool(obj.get("pass")):
            return Verdict(ok=True, source="evaluator")
        reasons = [str(r)[:200] for r in (obj.get("reasons") or [])][:6]
        return Verdict(ok=False, reasons=reasons or ["evaluator rejected the output"],
                       source="evaluator")
    except Exception:
        return Verdict(ok=True, source="evaluator-unavailable")


async def _distill(node: NodeSpec, output: str) -> str:
    if len(output) <= _DISTILL_FALLBACK_CHARS:
        return output.strip()
    if _utility is not None:
        summ = await _utility(
            "Condense the following agent output to at most 150 words. Keep "
            "every number, name, citation, and conclusion. No preamble.\n\n"
            + output[:12000],
            temperature=0.0, max_tokens=400,
        )
        if summ and summ.strip():
            return summ.strip()
    return output[:_DISTILL_FALLBACK_CHARS].strip() + " …"


async def _synthesize(st: MissionState) -> str:
    done = [(nid, st.nodes[nid]) for nid in (n.id for n in st.spec.nodes)
            if st.nodes[nid].status == "done"]
    failed = [n.id for n in st.spec.nodes if st.nodes[n.id].status != "done"]
    blocks = "\n\n".join(
        f"[{nid}] ({_node_by_id(st.spec, nid).role})\n{ns.distilled or ns.output[:900]}"
        for nid, ns in done
    )
    note = (f"\n\nNOTE: nodes {', '.join(failed)} failed and are not included."
            if failed else "")
    system = (
        "You are Nova's mission synthesizer. Combine the sub-agent results "
        "into ONE coherent, direct answer to the goal. Cite supporting nodes "
        "inline as [node-id]. Note real disagreements between nodes instead "
        "of papering over them. Markdown."
    )
    user = f"GOAL: {st.goal}\n\nSUB-AGENT RESULTS:\n{blocks}{note}"
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    # Synthesis rides the CHAT lane (frontier reasoning) with a dispatch-lane
    # fallback so a missing BYOK never strands completed node work.
    model = st.spec.model or ""
    if _chat_registry is not None:
        name, prov = _chat_registry.provider_for_model(model)
        if prov is not None:
            buf = ""
            async for chunk in prov.stream(model=model, messages=messages,
                                           no_think=False, temperature=0.3):
                buf += chunk
            if _spend_record is not None:
                _spend_record(provider=name or "chat", model=model,
                              in_tok=sum(len(m["content"]) for m in messages) // 4,
                              out_tok=len(buf) // 4, mission_id=st.id)
            return buf.strip()
    provider = _dispatch_registry.get_provider()
    if provider is None:
        return blocks  # last honest resort: the 
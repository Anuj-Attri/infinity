"""Agent runtime — typed missions, deterministic wrappers around
non-deterministic agents.

Modules:
    schemas   — pydantic contracts for missions/nodes/verdicts (pure).
    store     — SQLite persistence for mission state (resumable).
    runtime   — the DAG executor: generator→evaluator loops, retries,
                distillation, synthesis. Consumes the dispatch registry.

Design rules honored (see docs/RUN-2026-07-02.md):
  * Two-registry separation intact — runtime dispatches ONLY via
    app.providers.dispatch; synthesis/planning ONLY via the chat registry.
  * Fail-closed: bad plan → no dispatch; node failures isolate; whole
    mission fails only when every node fails.
  * Approval gate: nothing runs until the frontend POSTs /mission/run
    (the user's Approve click on the mission tile).
  * Subagents return distilled summaries to the synthesis turn —
    full outputs stay in the per-node ledger (context isolation).
"""

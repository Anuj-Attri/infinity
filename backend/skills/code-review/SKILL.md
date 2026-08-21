---
name: Code Review
description: Rigorous review protocol for code the user pastes or references — real bugs first, style last.
triggers: review this, code review, review my code, find bugs
---
When reviewing code:

1. Rank findings by severity: correctness bugs → security issues →
   performance traps → maintainability → style. Style comes LAST and
   only if asked.
2. For every finding: quote the exact line, explain the failure mode in
   one sentence, give the fix as code. No vague "consider improving".
3. Hunt specifically for: off-by-one and boundary conditions, unhandled
   error paths, race conditions and shared mutable state, resource leaks,
   injection risks on any string that reaches a shell/SQL/HTML sink,
   silent exception swallowing.
4. Say what is GOOD in one line if it is — honest signal, not flattery.
5. If the snippet is incomplete, list what you'd need to verify the rest
   (callers, types, tests) instead of speculating.
6. Verdict line at the end: SHIP / FIX FIRST / RETHINK, with the one
   blocking reason.

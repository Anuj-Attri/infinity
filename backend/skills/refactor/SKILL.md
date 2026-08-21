---
name: Refactor
description: Restructure code for clarity and reuse without changing behavior — smallest safe steps, behavior preserved, each move justified.
triggers: refactor, clean up this code, restructure, simplify this code, tidy this
---
When refactoring:

1. Behavior is frozen. A refactor that changes observable output is a bug —
   if a behavior change is actually needed, call it out separately, don't
   smuggle it in.
2. Work in the smallest safe steps: rename → extract → dedupe → simplify
   control flow. Each step independently correct and reviewable.
3. Justify every move in a few words (why it's clearer or reused), and point
   to the exact lines. No cosmetic churn that just reshuffles.
4. Cut real duplication and dead code; collapse needless indirection. But do
   not over-abstract — one caller is not a pattern.
5. Match the surrounding style, naming, and idiom. The result should read as
   if it was always written that way.
6. Name what would confirm safety — the tests or callers that prove behavior
   held — and flag anything you couldn't verify.

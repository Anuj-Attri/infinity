# Skills

One folder per skill, one `SKILL.md` inside. Frontmatter + instructions.

```
---
name: Daily Brief
description: One-paragraph morning briefing from the user's live tiles.
triggers: brief, briefing, morning
---
<instructions Nova follows while this skill is active>
```

- `triggers` — comma-separated keywords; word-boundary match fires the
  skill deterministically.
- No trigger hit → semantic match against `description` (local embeddings,
  cosine ≥ 0.62).
- At most 2 skills inject per turn, token-budgeted. The chat `done` event
  lists what fired.
- Edit a SKILL.md and the next turn picks it up (mtime watch). Toggle
  skills in the Skills tile.

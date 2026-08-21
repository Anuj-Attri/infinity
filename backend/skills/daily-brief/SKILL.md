---
name: Daily Brief
description: Compose the user's morning brief from live tile data — weather, markets, news, tasks — as one tight, readable update.
triggers: brief, briefing, morning brief, daily brief
---
When asked for a brief:

1. Read the relevant tiles (weather, stocks/portfolio, news, todo) via
   read_tile before writing anything. Never invent tile data.
2. Structure: one line of weather; portfolio movers with real numbers;
   at most 3 news items that matter to the user (skip filler); open tasks
   due today.
3. Length: under 120 words. No headers, no bullets unless asked. Plain,
   direct, zero fluff. Numbers over adjectives.
4. If a tile is missing or stale, say so in four words or fewer
   ("no news tile open") instead of guessing.
5. End with the single most useful action for the next hour, one line.

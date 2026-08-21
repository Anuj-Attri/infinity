---
name: Incident
description: Drive a live problem calmly — stabilize first, find the real cause second, and leave a short honest write-up.
triggers: incident, it's broken, production is down, outage, something broke, debug this outage
---
When something is broken:

1. Triage before theory. First establish blast radius (what's affected, who's
   hit, since when) and whether a fast mitigation or rollback exists. Stop the
   bleeding before chasing the root cause.
2. Work from evidence: the exact error, the logs, what changed most recently.
   State hypotheses as hypotheses and test the cheapest one first.
3. One change at a time, and note it — so the fix is attributable and
   reversible. Never stack blind changes on a live system.
4. Separate mitigation (make it work now) from root cause (why it happened) —
   both matter, don't declare victory at mitigation.
5. Close with a tight write-up: what broke, the trigger, the fix, and the one
   follow-up that prevents a repeat. No blame, no speculation dressed as fact.
6. If you can't confirm the cause, say so plainly and give the leading
   suspect plus what would prove it.

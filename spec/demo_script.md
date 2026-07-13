# Demo Script — Harry, the Manager Assistant (~8 minutes)

How to actually deliver the demo. Written before Steps 4-10 land; refine at
Step 10 when the seed exists (exact names/numbers below then get locked).

## Setup (before anyone is watching)

1. Fresh seed: `python -m app.seed_demo` → all checklist items ✓.
   Seed leaves the sim clock at **Thu 2026-07-16 10:00 IST**.
2. Two browser windows side by side: LEFT = simulator (`/`), RIGHT =
   dashboard (`/dashboard`, on `#/portfolio`). Chat dock collapsed.
3. `GEMINI_API_KEY` live (send one throwaway chat message to warm it up,
   then clear chat history). Server running, no console errors.
4. Know your escape hatches (bottom of this file).

Cast you'll narrate: Shivam (you, the manager), Alice (backend lead), Bob
(product), Carol (frontend). Projects: **Phoenix** (payments revamp,
troubled) and **Atlas** (analytics, healthy).

---

## Beat 1 — "This is my team, seen through Harry" (0:00–1:00)

RIGHT window, portfolio view.

- Frame it: "Harry is an assistant that sits on my team's Slack and email.
  I don't feed him status reports — he builds this himself from what people
  already say."
- Point at: two projects, health dots, task counts, last activity. Phoenix
  is already yellow — don't explain why yet, that's Beat 4's reveal.
- One sentence on the left window: "This simulator stands in for real Slack
  and Outlook — same message shapes, wire-identical."

## Beat 2 — A signal arrives (1:00–2:00)

LEFT window: impersonate Carol, send in #phoenix something like
"Staging checkout flow is green again, retry fix deployed."

- Show the message land like normal Slack.
- Narrate: "Every message gets cheap real-time extraction — who claims
  what, with what confidence. The expensive thinking happens in batches —
  we call it the dream cycle."
- Trigger the dream cycle (simulator button or `POST /api/kb/dream`).

## Beat 3 — THE TRUST MOMENT: compiled truth + hover citation (2:00–3:30)

RIGHT window: open Phoenix (`#/project/project:phoenix`).

- The compiled truth now mentions Carol's fix. Read one sentence aloud.
- **Hover a `[T#]` chip** → the popover shows Carol's ORIGINAL message.
  Pause here. "Every sentence Harry writes is cited down to the exact Slack
  message or email. If you don't trust the summary, you check the source.
  This is the difference between an assistant and a hallucination."
- Scroll the timeline briefly: two weeks of evidence, append-only.

## Beat 4 — The deadlock (3:30–5:00)

Still on Phoenix — the conflicts strip.

- "Two days ago Bob said he SENT the schema doc. Alice says she NEVER got
  it. Both messages, verbatim, side by side." Hover both claims → sources
  (Bob's in Slack, Alice's in Outlook — cross-channel catch, say so).
- "Harry flagged this as high severity but did NOT decide who's right —
  he never resolves conflicts, he surfaces them. He's already asked both;
  meanwhile the review task sits blocked and overdue — which is why
  Phoenix is yellow." (Show Harry's pings in the simulator DMs, LEFT.)

## Beat 5 — Time travel: +3 days (5:00–6:30)

LEFT window, sim-clock widget: **advance +3 days** (lands Sun → clock will
show quiet-hours behavior naturally; if the demo feels tight, +2 days).

- Narrate while it processes: "Harry runs on simulated time — three days
  pass in one click, and every scheduled behavior fires in order."
- Show the fallout: LEFT — follow-up re-pings from Harry, then an
  escalation DM to me: Bob's gone quiet twice. RIGHT — refresh portfolio:
  Phoenix degraded to red, reasons listed ("review overdue 4d, open
  high-severity conflict, no activity 3d").
- "I did nothing. Time passed, and Harry kept the pressure on."

## Beat 6 — Quiet hours + morning brief (6:30–7:15)

- Point at Carol's 11 PM staging bug from the seed: "Carol reported this at
  23:05. Harry wanted to act — but he doesn't ping people at night." Show
  the held item (`outbound queue` view in simulator).
- Jump the clock to next 09:00 → held ping releases, and the **morning
  brief** lands: open the briefs inbox (RIGHT) — overnight items, conflicts,
  health, today's due tasks, all in three tight sections.

## Beat 7 — Ask Harry anything (7:15–8:00)

Open the chat dock (RIGHT).

- Ask: **"What's blocking Phoenix and what should I do today?"** — cited
  answer; expand the "used N tools" trace ("you can audit how he got
  there").
- Ask: **"Prep me for the go/no-go tomorrow."** — pre-meeting-style brief:
  truth, the unresolved conflict, who owes what.
- Close: "One manager, two projects here — but this is every Slack channel
  and inbox, compiled into something I can trust, that acts while I sleep."

---

## Recovery / escape hatches

- **LLM call slow/fails mid-demo**: the KB is already built — Beats 3, 4, 6
  read from the DB and work offline. Only Beat 2's fresh synthesis and
  Beat 7's chat need live Gemini. If chat stalls, fall back to showing the
  morning brief and the entity page ("this is the same answer, precomputed").
- **Dream cycle produced odd truth text**: you seeded and rehearsed —
  re-seed and re-check before going live; never dream-cycle NEW content
  live except Beat 2 (rehearse that exact message; it's additive and safe).
- **Clock confusion**: `GET /api/time` is the source of truth; the
  dashboard top bar follows within 5s. If views look stale, hard-refresh —
  state is server-side, nothing is lost.
- **Wrong window fumble**: keep simulator LEFT / dashboard RIGHT the whole
  time; never rearrange mid-demo.

## Timing discipline

8 minutes is tight. Beats 3 and 4 are the demo — protect them. If running
long, compress Beat 6 (show the brief, skip the queue view) and drop the
second chat question, never the hover citation.

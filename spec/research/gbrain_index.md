# gbrain Research Index

Explored 2026-07-12 from https://github.com/garrytan/gbrain (v0.41.x, MIT).
Purpose: identify ideas/patterns to borrow for Harry's knowledge base and agent.
This is an index — re-clone the repo if a deep dive into a specific file is needed.

## What gbrain is

Garry Tan's "agent brain": TypeScript/Bun, Postgres (or PGLite) + pgvector,
exposed via CLI + MCP. Production scale: ~146K pages, 24K people. The
intelligence lives in ~43 markdown "skills" (fat markdown, thin harness) that an
agent (OpenClaw/Hermes/Claude Code) reads and follows; the binary only does
storage, search, and graph mechanics. A nightly "dream cycle" cron does
autonomous maintenance.

**Scale check:** gbrain is built for 100K+ pages, multi-brain federation,
embeddings, MCP, OAuth. Harry needs none of that infrastructure — we take the
knowledge model and operating patterns, not the stack.

## Core knowledge model (docs/GBRAIN_V0.md, docs/guides/compiled-truth.md)

Every entity is a **page** with two zones:

- **Compiled truth** (top): the current best synthesis. Always REWRITTEN when
  evidence changes — never appended to. Sectioned (for a person: Executive
  Summary / State / What They Believe / What They're Building / Assessment /
  Trajectory / Relationship / Contact).
- **Timeline** (bottom): append-only evidence trail. Entries are immutable;
  corrections are new entries. Every entry carries a `[Source: who, channel,
  date time tz]` citation.

Rules: every compiled-truth claim must trace to timeline entries; search weights
compiled truth above timeline; a page whose compiled truth is older than its
latest timeline entry is flagged "stale".

This is the single most transferable idea: **project pages for Harry = compiled
truth (current status synthesis) + timeline (append-only event log from
messages/mails/meetings)**. The manager reads 1 paragraph, the proof sits below.

## Two-layer memory: takes vs facts (docs/takes-vs-facts.md)

- **facts** (hot): extracted per-message in real time by a cheap model. Kinds:
  `event, preference, commitment, belief, fact`. Single-holder (the owner).
- **takes** (cold): extracted from pages by LLM analysis. Multi-holder ("WHO
  believes WHAT"): `holder`, `claim`, `kind (fact|take|bet|hunch)`, `weight
  0-1`, `since/until_date`, `superseded_by`, `active`. Never conflate the two;
  a nightly consolidate phase promotes facts → takes one-way.
- Extraction prompt learnings (production data): holder ≠ subject; atomic
  claims (split compounds); self-reported ≠ verified (weight 0.75, not 1.0);
  weights in 0.05 increments; "so what" test to skip trivia.

For Harry: the `holder` + `weight` + `superseded_by` idea matters for status
claims — "A says he's blocked on B" is a claim held by A, not ground truth.
That's exactly the deadlock-detection substrate Shivam wants.

## Self-wiring graph

Every page write extracts `[[wiki-link]]` / entity refs with **zero LLM
calls** and writes typed edges (`attended`, `works_at`, `mentions`, ...).
**Iron law of back-linking**: every mention creates a link FROM the entity page
TO the source; the graph is bidirectional or it's broken. Multi-hop traversal
via recursive CTE. Graph gave +31 points P@5 over vector-only RAG in their
evals. Harry's version: `message/task/meeting → project` and `message → person`
edges in SQLite, plain FK tables.

## Deterministic collectors (docs/guides/deterministic-collectors.md)

**Code for data, LLMs for judgment.** Collector scripts (no LLM) pull from
APIs, generate links/IDs/metadata, regex-classify noise, track seen-state with
atomic writes, and emit a pre-formatted digest. The LLM only reads the digest
and adds classification/commentary/drafts. Reason: LLMs probabilistically drop
formatting rules (~90% reliability = visible failures daily); code never does.
→ Our simulator→connector→`unified_messages` pipeline already follows this;
keep ALL parsing/dedup/link-generation deterministic, feed the agent digests.

## The brain-agent loop (docs/guides/brain-agent-loop.md)

Per inbound signal: **DETECT entities (async) → READ brain first → RESPOND with
context → WRITE updates (timeline + compiled truth) → SYNC index.** Two
invariants: every read improves the response; every write improves future
reads. Read BEFORE responding; external lookups are fallback, never primary.

## Entity detection (docs/guides/entity-detection.md)

A cheap async subagent scans EVERY message: (1) original ideas first, exact
phrasing preserved; (2) entities — search brain, append timeline if known,
create page only if notable (notability filter: skip passing mentions,
metaphors, one-offs); (3) mandatory back-links; (4) sync. Dedup by searching
before creating ("Pedro" vs "Pedro Franceschi").

## Meeting ingestion (docs/guides/meeting-ingestion.md)

Full diarized transcript, NEVER the AI summary (summaries hallucinate
"it was agreed..."). Meeting page = agent's own analysis above the bar
(surprises, real decisions, what was left unresolved) + transcript below.
**Entity propagation is mandatory and the step most agents skip**: every
attendee AND mentioned person/company page gets a timeline entry; action items
extracted with owners; bidirectional links. Relevant to Harry's MoM feature.

## Executive assistant pattern (docs/guides/executive-assistant.md)

1. **Email triage**: search the sender in the brain BEFORE reading the body;
   priority from relationship + open threads; unknown sender with no page ≈
   noise.
2. **Meeting prep**: per attendee — compiled truth, last interaction, open
   threads. ("Last time you discussed X; he was concerned about Y.")
   → Harry's pre-meeting briefs.
3. **Post-inbox brain updates**: every processed email with new info appends
   timeline entries to sender + mentioned entities. This is where the brain
   compounds.
4. **Scheduling nudges**: "no contact in 6 weeks", "open thread about X" —
   → Harry's follow-up engine is exactly this, keyed on tasks/status age.

## Source attribution (docs/guides/source-attribution.md)

Every fact — compiled truth included — carries `[Source: who, channel/context,
date time tz]`. Conflicts are never silently resolved: note both claims with
both citations. Source priority for conflicts: user direct > primary
(meetings/emails) > enrichment APIs > web > social. For Harry: source = the
`unified_messages.id` / platform_msg_id, so every KB claim is click-traceable
to the originating Slack msg/mail.

## Contradiction detection (docs/contradictions.md)

Nightly probe: sample retrieval pairs → date pre-filter (skip pairs >30d
apart) → cached LLM judge (Haiku, ~$0.0006/call) → severity rubric
(low=naming, medium=stale values, high=identity/structural) → report with
paste-ready resolution commands. **Never auto-applies** — output is data, the
operator decides. Temporal verdicts (supersession vs contradiction) stop it
crying wolf on legitimate change-over-time. → Harry's deadlock/conflict
detection: same shape, applied to status claims ("A blocked on B" vs "B blocked
on A"), surfaced to the manager rather than auto-resolved.

## Autonomous maintenance (docs/guides/cron-schedule.md)

Reference cadence: email/social collectors every 30 min; meeting sync 3x/day;
morning briefing daily; brain health weekly; **dream cycle nightly** (entity
sweep over the day's messages → create/enrich/update pages; fix citations;
consolidate facts→takes; sync). **Quiet-hours gate is mandatory on every
notification job** — held messages fold into the morning briefing (one 3 AM
ping and the user disables the system). Timezone inferred from calendar when
traveling.

## Retrieval (README, docs/architecture/RETRIEVAL.md)

Hybrid: pgvector HNSW + BM25 + reciprocal-rank fusion + source-tier boost +
reranker; multi-query expansion via Haiku; 4-layer dedup; stale alerts in
results. `search` (raw pages) vs `think` (synthesized answer + citations +
**gap analysis** — "the brain hasn't heard about X since April"). Gap analysis
is their killer differentiator and cheap to imitate: compare compiled-truth
freshness vs now. For Harry v1: SQLite FTS5 + structured queries; embeddings
optional later.

## Schema reference (src/core/migrate.ts)

- `pages(id, slug UNIQUE, type, title, compiled_truth, frontmatter JSONB,
  search_vector, created_at, updated_at)`
- `timeline_entries(id, page_id FK, date, source, summary, detail)`
- `links(id, from_page_id, to_page_id, link_type, context)`
- `takes(id, page_id, row_num, claim, kind CHECK(fact|take|bet|hunch), holder,
  weight REAL 0-1, since_date, until_date, source, superseded_by, active,
  resolved_* ..., UNIQUE(page_id, row_num))`
- `facts(id, source_id, entity_slug, fact, kind CHECK(event|preference|
  commitment|belief|fact), ... consolidated_at, consolidated_into)`
- plus `content_chunks` (embeddings), `tags`, `page_versions`, `raw_data`
  (original API payloads for re-processing), `ingest_log`.

## What we deliberately DON'T take

MCP server/OAuth, schema packs, skillpacks/registry, Minions job queue,
embeddings + rerankers (initially), multi-brain federation/RLS, eval framework,
markdown-repo-as-system-of-record (our system of record is SQLite; pages can be
markdown TEXT columns), Bun/TypeScript anything. Their "fat markdown skills"
philosophy conflicts partially with our TDD/deterministic-code preference — we
put workflows in code + prompts, not agent-interpreted markdown.

## Direct idea → Harry mapping

| gbrain idea | Harry feature |
|---|---|
| Compiled truth + timeline pages | Project/person pages; manager reads synthesis, timeline is proof |
| Takes with holder+weight | Status claims per teammate → deadlock/conflict reasoning |
| Deterministic collectors | Already our simulator→connector design; extend to digests for the agent |
| Entity detection per message | Message→project/task/person classification step in KB pipeline |
| EA pattern (triage/prep/nudges) | Follow-up engine, pre-meeting briefs, sender context |
| Meeting ingestion + propagation | MoM feature: transcript → meeting page → all entity timelines |
| Contradiction probe (never auto-apply) | Deadlock detector: surface to manager, don't auto-resolve |
| Dream cycle | Nightly Harry job: sweep day's unprocessed messages, update KB, prep briefs |
| Quiet hours + held notifications | Harry's ping etiquette (IST work hours) |
| Gap analysis in answers | "No update on Project X for 9 days" — proactive staleness alerts |
| Source citation per claim | Every KB claim links to unified_messages row |

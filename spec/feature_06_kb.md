# Feature 06: Knowledge-Base Schema (Entities, Timeline, Claims, Conflicts) & Query APIs

This specification defines how the Manager Assistant Agent (Harry) implements its structured knowledge base (KB) to serve as the unified storage layer ("brain") for representing project statuses, person profiles, meeting events, cited timelines, active claims, and discovered conflicts.

---

## 1. Motivation

Harry needs to keep track of project realities across multiple channels (Slack, Outlook, and manual inputs). Following the `gbrain` architecture pattern, we do not let an LLM directly generate or structure arbitrary text documents or standalone databases. Instead, we represent project reality through a highly structured, queryable substrate composed of:
1.  **Entities**: Representation of subjects like projects, people, or meeting events.
2.  **Timeline Entries**: An append-only historical log of cited evidence (which links back to original messages/records).
3.  **Attributed Claims**: Claims asserted by individuals, tagged with confidence (weight), source messages, and a "superseded_by" lineage chain.
4.  **Conflicts**: Open or resolved pairs of contradicting claims that have been identified by the system.

By implementing this DB substrate deterministically, we:
*   Form a bedrock of verifiable facts (provenance) where every high-level synthesis can be audited down to individual emails or chat logs.
*   Model conflict detection mathematically through claims that directly contradict each other.
*   Keep the system's reasoning transparent and deterministic.

---

## 2. Citation-Marker Convention

High-level summary texts (such as an Entity's `compiled_truth` summary) will contain inline citation markers like `[T17]`, where `17` represents the primary key (`id`) of a `TimelineEntry`.
The frontend dashboard (designed in Step 7) will resolve these markers:
*   On hover over `[T17]`, fetch `TimelineEntry` with `id = 17`.
*   Retrieve the underlying `source_message_id` from the entry to display the exact original Slack DM or email that supports the statement.

---

## 3. Database Schema

We add four tables under the shared base.

### A. `entities` Table
Represents high-level topics or targets of interest.
*   `id`: Integer, Primary Key, autoincrement.
*   `slug`: String(150), Unique, indexed (e.g., `"project:phoenix"`, `"person:U_ALICE"`, `"meeting:42"`).
*   `type`: String(20) (allowed values: `"project"`, `"person"`, `"meeting"`).
*   `name`: String(255) (display name).
*   `ref_id`: String(100), Optional (loose pointer to `projects.id` or `team_members.id`).
*   `compiled_truth`: Text, Optional (the synthesis summary text).
*   `truth_updated_at`: DateTime, Optional.
*   `created_at`: DateTime (sim IST default).
*   `updated_at`: DateTime (sim IST default, update hook).

### B. `timeline_entries` Table
An append-only timeline of facts/evidence.
*   `id`: Integer, Primary Key, autoincrement.
*   `entity_id`: Integer, ForeignKey (`entities.id`).
*   `happened_at`: DateTime (sim IST when the event happened).
*   `summary`: String(500) (one-line summary description).
*   `detail`: Text, Optional (detailed notes or content).
*   `source_message_id`: Integer, ForeignKey (`unified_messages.id`), Optional.
*   `created_at`: DateTime (sim IST default).

### C. `attributed_claims` Table
Claims asserted by individuals.
*   `id`: Integer, Primary Key, autoincrement.
*   `entity_id`: Integer, ForeignKey (`entities.id`).
*   `claim`: Text (e.g., `"Bob sent the schema doc to Alice on Jul 10"`).
*   `kind`: String(20) (allowed values: `"fact"`, `"status"`, `"commitment"`, `"blocker"`, `"opinion"`).
*   `holder`: String(100) (the asserter's `team_members.id` like `"U_BOB"`).
*   `weight`: Float (confidence score between `0.0` and `1.0`).
*   `source_message_id`: Integer, ForeignKey (`unified_messages.id`), Optional.
*   `claimed_at`: DateTime (sim IST when the claim was made).
*   `superseded_by`: Integer, ForeignKey (`attributed_claims.id`), Optional.
*   `active`: Boolean, default `True`.
*   `created_at`: DateTime (sim IST default).

### D. `conflicts` Table
Discovered contradictions between claims.
*   `id`: Integer, Primary Key, autoincrement.
*   `entity_id`: Integer, ForeignKey (`entities.id`).
*   `claim_a_id`: Integer, ForeignKey (`attributed_claims.id`).
*   `claim_b_id`: Integer, ForeignKey (`attributed_claims.id`).
*   `severity`: String(10) (allowed: `"low"`, `"medium"`, `"high"`).
*   `description`: Text (explanation of the contradiction).
*   `status`: String(10), default `"open"` (allowed: `"open"`, `"resolved"`, `"dismissed"`).
*   `detected_at`: DateTime (sim IST).
*   `resolved_at`: DateTime, Optional (sim IST).
*   `resolution_note`: Text, Optional.

---

## 4. Deterministic Entity Collectors & Backfills

Entity records are generated deterministically by system code rather than dynamically inferred by LLMs:
*   Creating a Project via `POST /api/projects` auto-creates an Entity with slug `"project:<slugified-name>"`.
*   Creating a TeamMember via `POST /api/team` auto-creates an Entity with slug `"person:<id>"`.
*   A `slugify()` function translates display names to lowercase and spaces/special characters to hyphens.
*   `init_db()` is expanded with a backfill check to guarantee that pre-existing projects and team members have matching Entity records.

---

## 5. API Routes (Router prefix `/api/kb`)

### 1. `GET /api/kb/entities?type=project`
List matching entities. Rejects empty lists but returns essential metadata (excluding heavy `compiled_truth`).
*   **Response**:
    ```json
    [
      {
        "id": 1,
        "slug": "project:phoenix",
        "type": "project",
        "name": "Project Phoenix",
        "ref_id": "1",
        "truth_updated_at": "2026-07-12T12:00:00"
      }
    ]
    ```

### 2. `POST /api/kb/entities`
Manually create an entity.
*   **Request**:
    ```json
    {
      "slug": "meeting:42",
      "type": "meeting",
      "name": "Kickoff Sync",
      "ref_id": null
    }
    ```
*   **Error handlers**: HTTP 409 on duplicate slug; HTTP 422 on type mismatch.

### 3. `GET /api/kb/entities/{slug}`
Returns the comprehensive details for a single page.
*   **Response**:
    ```json
    {
      "entity": {
        "id": 1,
        "slug": "project:phoenix",
        "type": "project",
        "name": "Project Phoenix",
        "ref_id": "1",
        "created_at": "2026-07-12T12:00:00",
        "updated_at": "2026-07-12T12:00:00"
      },
      "compiled_truth": "Summary text [T1].",
      "truth_updated_at": "2026-07-12T12:05:00",
      "timeline": [
        {
          "id": 1,
          "entity_id": 1,
          "happened_at": "2026-07-12T12:05:00",
          "summary": "Project initialized",
          "detail": "...",
          "source_message_id": 5
        }
      ],
      "claims": [
        {
          "id": 10,
          "entity_id": 1,
          "claim": "Alice has finished the schema",
          "kind": "fact",
          "holder": "U_ALICE",
          "weight": 1.0,
          "source_message_id": 5,
          "claimed_at": "2026-07-12T12:05:00",
          "superseded_by": null,
          "active": true
        }
      ],
      "conflicts": []
    }
    ```

### 4. `POST /api/kb/entities/{slug}/timeline`
Append a timeline entry.
*   **Request**:
    ```json
    {
      "happened_at": "2026-07-12T12:00:00",
      "summary": "Completed DB setup",
      "detail": "Alice finished writing standard SQLAlchemy migrations.",
      "source_message_id": 3
    }
    ```

### 5. `GET /api/kb/entities/{slug}/timeline?limit=50`
Get timeline entries (newest first).

### 6. `POST /api/kb/entities/{slug}/claims`
Create a claim. Rejects if `kind` is invalid or `weight` is not in `[0.0, 1.0]`.

### 7. `GET /api/kb/entities/{slug}/claims?active=true&holder=U_BOB`
Lists active or historical claims.

### 8. `POST /api/kb/claims/{id}/supersede`
Supersede claim `{id}` with a new claim structure. Marks old claim `active = False` and connects `superseded_by = B.id`. Rejects with HTTP 409 if the old claim was already superseded.

### 9. `POST /api/kb/conflicts`
Create a conflict between two claims. Both claims must belong to the specified entity (or return HTTP 422).

### 10. `GET /api/kb/conflicts?status=open&entity=project:phoenix`
Query conflicts list.

### 11. `PATCH /api/kb/conflicts/{id}`
Resolve or dismiss a conflict. Staps `resolved_at` using sim IST time.
*   **Request**:
    ```json
    {
      "status": "resolved",
      "resolution_note": "Bob verified the email was actually delivered."
    }
    ```

### 12. `GET /api/kb/search?q=schema`
Case-insensitive `LIKE` search across:
*   Entity names,
*   Compiled truths,
*   Claim texts, and
*   Timeline summaries.
Returns grouped results: `{"entities": [], "claims": [], "timeline": []}`.

---

## 6. Test Plan

We implement `tests/test_kb.py` to validate:
*   Entity validations (uniqueness and type constraints).
*   Automatic entity generation during project and member additions.
*   Idempotency of get-or-create handlers.
*   Timeline append workflows (default timestamps, sorting, 404 validation).
*   Claim validations (ranges, categories).
*   Claims superseding, lineage links, and active filters.
*   Conflict schema validation (multi-entity constraints, active open filter).
*   PATCH-based conflict resolutions.
*   Search aggregations.

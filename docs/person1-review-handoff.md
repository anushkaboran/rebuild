# Person 1 — connecting the real inventory (backend v0.6.1)

One new read-only endpoint. Nothing existing changed, so your upload + mock flow
keeps working.

## Fetch review data

```http
GET /api/cases/{case_id}/review
```

```ts
const res = await fetch(`${API}/api/cases/${caseId}/review`);
const review = await res.json();
// review.case, .evidence, .items, .findings, .questions, .decisions, .stats, .source
```

`source` is `"spacetimedb"` when the data came from the shared database,
`"memory"` in tests/demo, and `"memory (stale: SpacetimeDB unreachable)"` if the
database was down — show a warning in that last case rather than pretending.

### Items — the part you asked for

| Field | Notes |
|---|---|
| `id` | **backend id** — send this to the correction endpoint |
| `agent_item_id` | original CREAO id (`itm_001`), for display/traceability |
| `brand`, `model`, `purchase_date` | `string \| null` — verbatim from the agent, never inferred |
| `purchase_price` | `string \| null` — exactly as printed on a receipt |
| `currency` | `string \| null` — **null when unknown**; do not substitute `USD` in the UI |
| `location_in_image` | where it is in the photo, as text |
| `visible_observations` | what the agent saw (condition language, not a damage grade) |
| `supporting_evidence_ids` | `ev_###` ids that point into `evidence[]` |
| `user_confirmed_fields` | fields a human already confirmed |
| `review_status` | `needs_review` \| `confirmed` |
| `estimated_value` | number or `null` — **separate from `purchase_price`** |
| `revision` | send as `if_revision` when correcting |

Render `purchase_price` and `estimated_value` as different things: the first is a
receipt line, the second is what a reviewer decided the item is worth.

## Submit a correction

```ts
await fetch(`${API}/api/items/${item.id}`, {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    estimated_value: 425,
    condition: 'base is cracked',
    editor: currentUser,
    note: 'verified on site',
    if_revision: item.revision,   // the revision you rendered
  }),
});
```

- `200` → the updated item with `revision: item.revision + 1`
- `409` → someone saved first. `body.detail.current` is the winning row; show a
  diff and let the user re-apply. Do **not** silently retry with a bumped
  revision.
- `status` becomes `human_corrected` automatically on a first edit;
  `human_confirmed` when the reviewer accepts it as-is.

Confirm / reject shortcuts still exist:
`POST /api/items/{id}/confirm?editor=…`, `POST /api/items/{id}/reject?editor=…`.

## Findings and questions

`findings[]`: `id`, `finding_id` (`fnd_001`), `finding_type`, `item_ids` (agent
ids), `evidence_ids`, `explanation`, `status`, `resolution_reason`.
`questions[]`: `id`, `question_id` (`q_001`), `item_id`, `question`, `reason`,
`status` (`open`/`resolved`), `resolution_reason`.

## Evidence

`evidence[]` gives `evidence_id`, `upload_id`, `filename`, `evidence_type`
(`photo`/`receipt`), `content_type`, `readable`, `description`, `size_bytes`, and
`url` — the local `/api/uploads/{upload_id}/file` route for thumbnails. No signed
or public URLs are ever returned.

## Realtime (optional)

Polling the review endpoint works. For live collaboration, subscribe to
SpacetimeDB as described in `docs/spacetimedb.md` — `item` rows carry `revision`,
so the same optimistic-concurrency rules apply.


---

# Decisions: answering questions and resolving findings (v0.6.2)

Two endpoints. Both are per-record revisioned: read `revision` from the review
payload, send it back as `if_revision`, and handle `409` by re-reading.

## Answer an investigator question

```http
PATCH /api/cases/{case_id}/questions/{question_ref}
Content-Type: application/json
```

`question_ref` is either `question.id` (`inv_…:q_001`) or `question.question_id`
(`q_001`) — whichever you already hold.

```json
{ "answer": "Yes, mine — it was in the room.", "reviewer": "priya", "if_revision": 0 }
```

Success (`200`):

```json
{
  "id": "inv_b58112068a26:q_001",
  "question_id": "q_001",
  "item_id": "itm_001",
  "question": "Is the brown armchair yours and was it in the room when the water entered?",
  "reason": "Ownership affects the claim.",
  "evidence_ids": ["ev_001", "ev_002"],
  "status": "answered",
  "answer": "Yes, mine — it was in the room.",
  "answered_by": "priya",
  "answered_at": "2026-10-24T19:40:02.114Z",
  "revision": 1
}
```

**Clearing** works by sending `"answer": null` (or `""`) with the current
revision: you get `status: "open"`, `answer: null` and `revision + 1`. A question
only shows `answered` while an answer is stored.

## Resolve a finding

```http
PATCH /api/cases/{case_id}/findings/{finding_ref}
```

`finding_ref` = `finding.id` or `finding.finding_id` (`fnd_001`).

```json
{ "resolution": "confirmed",
  "reason": "Second photo shows the same chair.",
  "reviewer": "priya",
  "if_revision": 0 }
```

`resolution` ∈ `confirmed` | `rejected` | `needs_more_evidence` | `unresolved`.
**`reason` is required for `rejected`** — otherwise `400 REASON_REQUIRED`.

Success (`200`) returns the finding with `resolution`, `resolution_reason`,
`resolved_by`, `resolved_at`, `revision`, while `finding_id`, `finding_type`,
`explanation`, `item_ids` and `evidence_ids` stay exactly as the agent wrote them.

## Error shapes

```jsonc
// 400
{ "detail": { "code": "REASON_REQUIRED",
              "message": "A reason is required when rejecting a finding." } }
// 404
{ "detail": { "code": "QUESTION_NOT_FOUND",
              "message": "Question 'q_999' was not found in case 'case_…'." } }
// 409
{ "detail": { "code": "REVISION_CONFLICT",
              "message": "Question 'q_001' is at revision 2, not 1.",
              "current": { /* the saved question or finding */ } } }
```

On `409`, replace your row with `detail.current`, show the user what changed, and
let them re-apply. Never retry with a bumped revision — that discards someone
else's answer. Sending the same `if_revision` twice also returns `409`
(no silent double-apply).

## Reading decisions back

`GET /api/cases/{case_id}/review` now includes, per record:

- `questions[]`: `answer`, `answered_by`, `answered_at`, `revision`, `status`,
  plus the untouched agent `question` / `reason` / `evidence_ids`
- `findings[]`: `resolution`, `resolution_reason`, `resolved_by`, `resolved_at`,
  `revision`, plus agent `finding_id` / `finding_type` / `explanation` /
  `evidence_ids`

and two audit arrays: `question_decisions[]` and `finding_decisions[]`, each entry
carrying `target_id` (backend id) and `agent_ref` (`q_001` / `fnd_001`).

`decisions[]` is unchanged and still means **item** decisions.

## Realtime

`question`, `finding` and `decision_event` are public tables. Subscribe to them
alongside `item` and the answers/resolutions arrive without polling — verified
with two live clients (`spacetimedb/scripts/two-client-decisions.ts`).

## Identity caveat

`reviewer` / `answered_by` / `resolved_by` are display strings you supply. They
are **not authenticated** — there is no auth layer in the backend. Show them as
attribution, never as permission.


---

# "Update Investigation" — follow-up runs (v0.6.3)

Start a follow-up exactly like the first investigation; nothing new is required
from the UI:

```http
POST /api/cases/{case_id}/investigations
{ "upload_id": "upl_…", "upload_ids": ["upl_…","upl_…"], "room_hint": "Living room" }
```

The backend automatically:

1. reuses each upload's **stable evidence ID** (`ev_001`…), assigning new numbers
   only to newly uploaded files;
2. sends every saved question answer as `review_answers` (JSON string);
3. reconciles the returned inventory into the existing case.

## What to show after the run

Poll `GET /api/investigations/{id}` until `status` is `complete` or `failed`, then
refresh the review screen. The investigation now reports:

| Field | UI use |
|---|---|
| `follow_up` | badge the run as an update rather than a first pass |
| `items_created` | "2 new belongings proposed" |
| `items_reconciled` | "1 existing belonging updated" |
| `items_flagged` | "1 needs identity review" |
| `status` / `error` | surface failures (`failed` means nothing was imported) |

## Three item states to render differently

Each item in `GET /api/cases/{id}/review` has `reconciliation`:

| Value | Meaning | Suggested UI |
|---|---|---|
| `new` | a newly proposed belonging | "New" chip, needs review |
| `matched` | the same belonging seen before; `id` is unchanged | "Updated" chip |
| `identity_conflict` | the agent changed the item id; name + evidence suggest the same object | "Possible duplicate — review" banner, show `possible_match_item_id` |

For `identity_conflict`, link to the suspected original
(`items.find(i => i.id === item.possible_match_item_id)`) and let the reviewer
decide. The backend never merges them automatically.

If a reviewer had already decided a row, the follow-up's proposal is in
`agent_proposal` instead of overwriting the confirmed values — show it as a
"what the last run suggested" diff, never as the current truth.

## Guarantees you can rely on

- human corrections, confirmed fields, `revision`, reviewer names and decision
  history are preserved across follow-ups;
- question answers and finding resolutions stay attached to their
  `question_id` / `finding_id` even when the agent rewrites the wording;
- retries are safe: running the same follow-up twice creates no duplicates;
- stale `if_revision` still returns `409` with `detail.current`.

## `review_answers` (confirmed for Investigator v1.3.1)

The backend sends saved answers automatically as a JSON **string** with exactly
three keys per answer:

```json
[{"question_id": "q_001", "answer": "Yes, mine.", "answered_by": "priya"}]
```

Only answered questions are included. The UI does not build this — but note that
the question text, item link and `answered_at` are still returned by
`GET /api/cases/{id}/review`, so your review screen loses nothing.

Answers never confirm inventory fields, and they never overwrite a reviewer's
existing decisions.


---

# Researcher: product research for one belonging (v0.8.0, confirmed contract)

Second agent, own App ID (`a7260cbc-8253-4fa7-88d3-c644267e3221`) and credential.
Suggestions only — nothing it returns changes an inventory item.

## Endpoints (canonical)

```
POST /api/items/{item_id}/research
GET  /api/research/{research_id}
GET  /api/items/{item_id}/research
```

The old case-scoped URLs still work as aliases.

## Start

```http
POST /api/items/itm_a16684bd49c2/research
Content-Type: application/json

{ "region_currency": "USD", "requested_by": "priya" }
```

`region_currency` is optional; **omit it and no region/currency is assumed**.

- `202 Accepted` → new run queued.
- `200 OK` → an identical request while a run is already queued/running returns
  that run; no second paid run is started. Use the returned `id`.

```json
{ "id": "rsch_9f21c0a7b3d4", "item_id": "itm_a16684bd49c2", "status": "queued",
  "region_currency": "USD", "agent_run_id": null, "candidate_ids": [] }
```

## Poll

```http
GET /api/research/rsch_9f21c0a7b3d4
```

```json
{
  "run": { "id": "rsch_…", "status": "complete", "candidates_valid": 1,
           "error": null, "queries_run": ["strandmon wing chair"],
           "limitations": ["Prices shown are retail listings, not purchase prices."],
           "input_summary": { "item_ref": "itm_…", "region_currency": "USD" } },
  "candidates": [
    { "candidate_id": "cand_001", "brand": "IKEA", "model": "STRANDMON",
      "product_name": "STRANDMON wing chair",
      "product_url": "https://www.ikea.com/us/en/p/strandmon-wing-chair",
      "matching_features": ["wing back"], "differing_features": ["leg colour differs"],
      "missing_verification": "Measurements not confirmed.",
      "price_as_displayed": "$349.00", "currency": "USD",
      "source_accessed_at": "2026-10-24T21:00:00Z",
      "status": "unverified_possible_match", "unverified": true }
  ]
}
```

Statuses: `queued` → `running` → `complete` | `failed`.

| Status | Meaning |
|---|---|
| `queued` / `running` | still working → keep polling (~3 s) |
| `complete` | `candidates` is final; may be **empty** (valid: no matches) |
| `failed` | `run.error` explains why (`RESEARCH_INVALID_RESULT: …`) |

## Rendering rules

- Always show an **"Unverified suggestion"** badge (`status` /
  `unverified: true`).
- `price_as_displayed` is what a shop page showed — not a purchase price and not
  a replacement value. Never copy it into `purchase_price` or `estimated_value`.
- Show `matching_features`, `differing_features` and `missing_verification` so the
  reviewer can judge the match.
- To accept, the reviewer edits the item via `PATCH /api/items/{id}` (brand,
  model, purchase_price are correctable) — normal revision/conflict rules apply.
- `queries_run` and `limitations` are shown as provenance ("what was searched",
  "what this cannot tell you").

## Errors

```jsonc
// 404
{ "detail": { "code": "RESEARCH_NOT_FOUND", "message": "Research run 'rsch_x' was not found." } }
// 409 — mock mode
{ "detail": { "code": "CREAO_MODE_MOCK", "message": "…" } }
// 503 — missing CREAO_RESEARCH_API_KEY
{ "detail": { "code": "RESEARCH_NOT_CONFIGURED", "message": "Researcher is not configured: CREAO_RESEARCH_API_KEY." } }
```

A `failed` run still returns `200` from `GET /api/research/{id}` with
`run.status = "failed"` and `run.error` set.

## Required environment variables

| Variable | Purpose |
|---|---|
| `CREAO_RESEARCH_APP_ID` | `a7260cbc-8253-4fa7-88d3-c644267e3221` |
| `CREAO_RESEARCH_API_KEY` | Researcher `capi_` key (server-side only) |
| `CREAO_MODE=live` | research is refused in mock mode |
| `REBUILD_PUBLIC_BASE_URL` | optional; enables `room_photos` URLs |

## Recovering a run whose artifact was missed (v0.8.1)

If a research record failed with `no readable research-candidates.json`, recover
the **existing** CREAO run — no new paid run:

```http
POST /api/research/rsch_3491e9cce4aa/recover
{ "creao_run_id": "11626040-0ac3-44db-84cd-59c6f22279cc" }
```

`200` → `{ run, candidates, source, already_recovered }`, with
`run.status = "complete"`. Calling it again returns `already_recovered: true`
and adds nothing.

To see what the agent actually returned (safe to share — no URLs or secrets):

```http
GET /api/research/rsch_3491e9cce4aa/diagnostics
```

```json
{ "run_status": "completed", "result_fields": ["artifacts", "chatText", "text"],
  "artifacts_seen": 2, "resolved": true, "source": "result.artifacts:Outputs",
  "probes": [ { "container": "result.artifacts", "title": "Outputs",
                "type": "application/octet-stream", "has_url": true,
                "bytes": 1841, "json": true,
                "keys": ["candidates", "item_ref", "limitations", "queries_run"],
                "error": null } ] }
```

Error codes are listed in `docs/contract.md` §6g — they distinguish “no artifact
metadata” from “artifact found but unusable”, so a support question can be
answered without guessing.

### Shapes you will actually receive (v0.8.2)

Verified against the live run's unmodified `research-candidates.json`:

```json
"missing_verification": ["No dimensions could be taken…", "Chair base … cannot be seen."],
"queries_run": [ { "query": "upholstered armchair rolled arms …", "tool": "web_search" },
                 { "query": "ikea.com/us/en/p/ektorp…", "tool": "web_fetch" } ],
"currency": null,
"price_as_displayed": "€ 349"
```

- `missing_verification` is an **array** — render it as a bullet list.
- `queries_run` is an array of **objects** — show `tool` as the source badge.
- `currency: null` is legitimate; pair it with `price_as_displayed`'s own symbol
  rather than assuming a currency.
- `source_accessed_at` is passed through untouched.

## Real-run status

**No live Researcher run has been executed.** Everything above is verified
against faked CREAO responses (the contract shape, validation, dedupe, isolation
and persistence). The first real run is the remaining check.

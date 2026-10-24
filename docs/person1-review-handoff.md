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

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

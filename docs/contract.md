# Rebuild API contract

**Status:** v0.8.2 — owned by Person 3 (backend). URL-based evidence + recovery of already-completed CREAO runs.
**Base URL (local):** `http://localhost:8000`
**Machine-readable schema:** `GET /openapi.json` · interactive docs at `/docs`

Anything marked ⚠️ is an assumption that must be confirmed before it is treated
as final — see [Open questions](#open-questions).

---

## 1. Conventions

| Topic | Rule |
|---|---|
| Content type | JSON (`application/json`) everywhere except uploads (`multipart/form-data`) and the PDF export (`application/pdf`). |
| IDs | Opaque strings with a prefix: `case_…`, `upl_…`, `inv_…`, `itm_…`, `rev_…`, `cmt_…`, `evt_…`. Never parse them. |
| Timestamps | ISO-8601 UTC with offset, e.g. `2026-03-14T18:22:05.412Z`. |
| Money | `estimated_value` is a float plus a separate 3-letter `currency` (default `USD`). No cents rounding is enforced. |
| Casing | `snake_case` in JSON, matching the Python models. |
| Enum values | Lowercase snake strings (see [§8](#8-enums)). |
| Unknown fields | Ignored on input, never returned on output. |

### Errors

All non-2xx responses use one of these two shapes.

```jsonc
// 4xx from an explicit check (404, 415, 413, 409…)
{ "detail": "Case 'case_abc' not found.", "path": "/api/cases/case_abc" }
```

```jsonc
// 422 from Pydantic validation
{
  "detail": "Validation failed",
  "errors": [
    { "loc": ["body", "title"], "msg": "String should have at least 1 character", "type": "string_too_short" }
  ]
}
```

`409 Conflict` is the one case where `detail` is an **object**, because the
client needs the winning row to merge:

```jsonc
{
  "detail": {
    "message": "Item was updated by marcus (revision 4). Refetch and reapply your change.",
    "current": { /* full EvidenceItem */ }
  }
}
```

| Code | Meaning in this API |
|---|---|
| 400 | Upload does not belong to the case, or an invalid storage path |
| 404 | Case / upload / item / investigation does not exist |
| 409 | Stale `if_revision` on a review edit |
| 413 | File larger than `MAX_UPLOAD_BYTES` (25 MB default) |
| 415 | File type not in `ALLOWED_CONTENT_TYPES` |
| 422 | Malformed body or query params |
| 502 | CREAO rejected the request (bad key, unknown agent, no credits) |
| 503 | `CREAO_MODE=live` but misconfigured (`CREAO_NOT_CONFIGURED`), evidence transport unset (`CREAO_EVIDENCE_TRANSPORT_UNVERIFIED`), or evidence URLs not fetchable (`CREAO_EVIDENCE_URLS_UNAVAILABLE`) — never silently mocked, and no run is created |

---

## 2. Health

### `GET /api/health`

Tells the frontend whether it is talking to a real agent or to mock data, and
which parts of the stack are wired. Use it for a status chip in the header.

```json
{
  "status": "ok",
  "service": "Rebuild API",
  "version": "0.5.0",
  "environment": "development",
  "integrations": {
    "creao": {
      "mode": "mock",
      "configured": true,
      "transport": "mock",
      "base_url": null,
      "app_id": "9819d9a5-89e0-4d62-a284-91bac67f8cd1",
      "api_key_present": false,
      "api_key_prefix": null,
      "evidence_transport": "unset",
      "evidence_ready": true,
      "missing": []
    },
    "spacetimedb": {
      "enabled": false,
      "host": "http://127.0.0.1:3000",
      "database": "rebuild",
      "module": "rebuild"
    }
  }
}
```

**`integrations.creao.mode`** is `"mock"` or `"live"`. `transport` is
`"app-trigger"` (live) or `"mock"`. `api_key_prefix` is the documented prefix
only (`capi_`) — the key itself is never returned by any endpoint, so this
response is safe to display. `evidence_transport` reports what evidence would
actually be sent (see §5); `evidence_ready` is false until that is configured.

### `GET /api/creao/status`

Same `creao` object as above, without the rest of the health payload.

### `POST /api/creao/validate`

Proves the live credentials work **without spending credits**. Calls only the
documented read-only routes `GET /v1/agents` and `GET /v1/account/usage`.

```json
{
  "ok": true,
  "mode": "live",
  "transport": "app-trigger",
  "app_id": "9819d9a5-89e0-4d62-a284-91bac67f8cd1",
  "base_url": "https://agent.creao.ai/api/v1",
  "api_key_prefix": "capi_",
  "recent_runs_visible": 0,
  "evidence_transport": "pre_attached",
  "evidence_ready": true
}
```

`409 CREAO_MODE_MOCK` in mock mode (nothing to validate, no network);
`503` when live mode is not configured; `502` when CREAO rejects the key. See
[creao-api-integration.md](./creao-api-integration.md).

---

## 3. Cases

### `GET /api/cases` → `Case[]`

Newest first.

### `POST /api/cases` → `201 Case`

```json
{ "title": "42 Willow Creek Rd", "address": "Asheville, NC", "disaster_type": "flood", "created_by": "priya", "notes": null }
```

`title` is required (1–200 chars); everything else is optional.

```json
{
  "id": "case_bf317d4b3fbb",
  "title": "42 Willow Creek Rd",
  "address": "Asheville, NC",
  "disaster_type": "flood",
  "status": "open",
  "notes": null,
  "created_by": "priya",
  "members": [{ "id": "usr_9a1c…", "display_name": "priya", "role": "owner" }],
  "upload_count": 0,
  "item_count": 0,
  "created_at": "2026-03-14T18:20:00.000Z",
  "updated_at": "2026-03-14T18:20:00.000Z"
}
```

### `GET /api/cases/{case_id}` → `CaseDetail`

`Case` plus `uploads: Upload[]` and `items: EvidenceItem[]`. **This is the one
call the case page should make on load.**

### `PATCH /api/cases/{case_id}` → `Case`

Partial update. Accepts `title`, `address`, `notes`, `status`. Unknown keys are
ignored; an all-null body is a no-op returning the current case.

### `GET /api/cases/{case_id}/stats` → `ReviewStats`

Drives the review-progress header.

```json
{
  "case_id": "case_bf317d4b3fbb",
  "total_items": 6,
  "ai_suggested": 3,
  "human_confirmed": 2,
  "human_corrected": 1,
  "rejected": 0,
  "total_estimated_value": 6800.0,
  "currency": "USD",
  "contributors": ["marcus", "priya"],
  "last_activity_at": "2026-03-14T18:44:10.220Z"
}
```

---

## 4. Uploads

### `POST /api/cases/{case_id}/uploads` → `201 Upload`

`multipart/form-data`:

| Field | Type | Required | Notes |
|---|---|---|---|
| `file` | file | yes | `image/jpeg`, `image/png`, `image/webp`, `image/heic`, `application/pdf` |
| `kind` | `photo` \| `receipt` | no | default `photo` |
| `uploaded_by` | string | no | default `anonymous` |

```bash
curl -X POST http://localhost:8000/api/cases/case_abc/uploads \
  -F "file=@kitchen.jpg" -F "kind=photo" -F "uploaded_by=priya"
```

```json
{
  "id": "upl_7c2e91a4bb01",
  "case_id": "case_bf317d4b3fbb",
  "kind": "photo",
  "original_filename": "kitchen.jpg",
  "content_type": "image/jpeg",
  "size_bytes": 1842331,
  "sha256": "e3b0c442…",
  "uploaded_by": "priya",
  "url": "/api/uploads/upl_7c2e91a4bb01/file",
  "created_at": "2026-03-14T18:22:05.412Z"
}
```

Notes:

- The declared content type is **sniffed from magic bytes**; `content_type` in
  the response is the sniffed value, and a mismatch can produce `415`.
- Use `url` for `<img src>` — it is already API-relative. **Do not** build file
  URLs yourself; the storage path is deliberately not exposed.
- `sha256` lets the frontend de-duplicate re-uploads of the same photo.

### `GET /api/uploads/{upload_id}` → `Upload`

### `GET /api/uploads/{upload_id}/file` → binary

Serves the original bytes with the correct `Content-Type` and a
`Content-Disposition` filename. Cacheable per upload (content never changes).

---

## 5. Investigations (CREAO agent)

### `POST /api/cases/{case_id}/investigations` → `202 Investigation`

```json
{
  "upload_id": "upl_7c2e91a4bb01",
  "room_hint": "kitchen",
  "instructions": "Focus on appliances under the water line.",
  "requested_by": "priya"
}
```

Returns **immediately** with `status: "queued"`; the agent call runs in the
background. One upload can be investigated more than once (e.g. re-run after a
better photo) — each run creates its own items.

**`503 CREAO_NOT_CONFIGURED`** is returned *before* work is accepted when
`CREAO_MODE=live` but the key/agent id are missing, or when the key is an
app-scoped `capi_` key (that route cannot receive files). The backend never
falls back to mock data silently.

```json
{
  "detail": {
    "code": "CREAO_NOT_CONFIGURED",
    "message": "CREAO live mode is on but configuration is incomplete: CREAO_API_KEY, CREAO_AGENT_ID. …",
    "mode": "live"
  }
}
```

### `GET /api/investigations/{investigation_id}` → `Investigation`

```json
{
  "id": "inv_2f81a0c93d77",
  "case_id": "case_bf317d4b3fbb",
  "upload_id": "upl_7c2e91a4bb01",
  "status": "complete",
  "agent": "rebuild-evidence-investigator",
  "mode": "live",
  "agent_run_id": "f5ddc714-e0d0-49fa-b2af-df49a1c4a814",
  "conversation_id": "e9066165-4272-4b16-9970-059784024cde",
  "model": null,
  "credits_used": null,
  "evidence_transport": "url",
  "evidence_ids": ["ev_001", "ev_002"],
  "summary": "Kitchen inspected: 4 belongings recorded, no items invented.",
  "error": null,
  "item_ids": ["itm_…", "itm_…"],
  "requested_by": "priya",
  "created_at": "2026-03-14T18:23:00.000Z",
  "finished_at": "2026-03-14T18:23:21.180Z"
}
```

`status`: `queued` → `running` → `complete` | `failed`. On failure `error`
carries the CREAO code and message (truncated to 1000 chars) and `item_ids` is
empty.

**`agent_run_id`** is the app-scoped run `id`; **`conversation_id`** is its
`threadId` (mapped for follow-up runs). `model` and `credits_used` are `null`
because app-scoped runs do not report them today.

**`evidence_transport`** records what evidence was actually sent: `mock` |
`url` (signed HTTPS links) | `demo_url` (staged fixture) | `none`. It is the flag that keeps the backend from
claiming photographs reached the agent when they did not. **`evidence_ids`** are
the `ev_###` IDs the backend assigned before the call.

**`mode`** is `"mock"` or `"live"` and is the flag the UI must badge. A `mock`
run is simulated (`agent_run_id` starts with `mock_`, summary prefixed
`[SIMULATED]`) and must never be presented as a real finding. `"live"` means
the values came from an actual CREAO run.

**Preferred frontend flow:** rely on the event stream
(`investigation.completed`) instead of polling; poll only as a fallback with a
~1 s interval.

### `GET /api/cases/{case_id}/investigations` → `Investigation[]`

Newest first, including queued and failed runs.

---

## 6. Evidence items

An item is one belonging in the shared recovery record.

```json
{
  "id": "itm_44b1ee907a2c",
  "case_id": "case_bf317d4b3fbb",
  "investigation_id": "inv_2f81a0c93d77",
  "upload_id": "upl_7c2e91a4bb01",
  "room": "kitchen",
  "name": "Refrigerator",
  "category": "appliance",
  "description": "Refrigerator photographed in the kitchen after flooding.",
  "condition": "Substantial water damage, replacement likely",
  "damage_level": "severe",
  "confidence": 0.87,
  "estimated_value": 1800.0,
  "currency": "USD",
  "bbox": { "x": 0.1, "y": 0.1, "w": 0.35, "h": 0.18 },
  "agent_reasoning": "Visible water line on the lower section…",
  "status": "ai_suggested",
  "agent_item_id": "itm_001",
  "supporting_evidence_ids": ["ev_001", "ev_002"],
  "review_status": "needs_review",
  "revision": 1,
  "created_at": "2026-03-14T18:23:00.120Z",
  "updated_at": "2026-03-14T18:23:00.120Z",
  "updated_by": null
}
```

`bbox` is **normalised 0–1** relative to the source image, so the frontend can
overlay boxes on any rendered size: `left = x * width`, etc. `null` when the
agent could not localise the item.

**Agent traceability fields:** `agent_item_id` is the agent's own `itm_###`,
`supporting_evidence_ids` lists the `ev_###` evidence it cited, and
`review_status` is the agent's `needs_review` | `confirmed`
(`confirmed` items are created with `status: "human_confirmed"`). The full agent
record (brand, model, `purchase_price`, …) is retained server-side, not exposed.

`damage_level` is `unknown`, `confidence` `0.0`, `estimated_value` `null` and
`bbox` `null` for agent-produced items: the investigator reports only what is
visible and never emits a damage grade, confidence score or replacement value.
The backend does not invent them.

`upload_id` is the evidence link — always render "View source photo" from it.
`null` means a human added the item manually (evidence column in the PDF shows
`manual entry`).

### `GET /api/cases/{case_id}/items` → `EvidenceItem[]`

Query params: `status` (enum filter), `room` (case-insensitive exact match).

### `POST /api/cases/{case_id}/items` → `201 EvidenceItem`

For a belonging the agent missed. Created with `status: "human_confirmed"`,
`revision: 1`, `upload_id: null`.

```json
{ "name": "Wedding album", "room": "bedroom", "category": "keepsake", "damage_level": "destroyed", "estimated_value": 0, "editor": "priya" }
```

### `PATCH /api/items/{item_id}` → `EvidenceItem`

The main collaborative write. Send only what changed.

| Field | Type | Notes |
|---|---|---|
| `name` | string | 1–200 |
| `category` | string | free text, ≤80 |
| `room` | string | ≤80 |
| `description` | string | ≤2000 |
| `condition` | string | ≤1000 |
| `damage_level` | enum | see §8 |
| `estimated_value` | float ≥ 0 | |
| `currency` | string | 3 letters, upper-cased |
| `status` | enum | see §8 |
| `editor` | string | who made the change; **required in practice** for the audit trail |
| `note` | string | ≤1000, shown in the PDF correction history |
| `if_revision` | int ≥ 1 | optimistic-concurrency guard |

Behavior:

- If `if_revision` is supplied and does not match the stored `revision`,
  returns **409** with the current item — refetch, reapply, retry.
- Omitting `if_revision` forces the write (last-write-wins). Use only for
  single-user paths.
- If `status` is not supplied and the item is `ai_suggested`, a successful edit
  sets it to `human_corrected` automatically.
- An empty change set is a no-op returning the item unchanged.
- Every accepted call writes a `Revision` and bumps `revision` by 1.

```jsonc
// request
{ "estimated_value": 2400, "note": "Receipt found", "editor": "marcus", "if_revision": 2 }
```

### `POST /api/items/{item_id}/confirm` → `EvidenceItem`

Shortcut: `?editor=priya`. Sets `status: human_confirmed`, note
`"Confirmed without changes"`. Still audit-logged.

### `POST /api/items/{item_id}/reject` → `EvidenceItem`

Shortcut: `?editor=priya&note=false+positive`. Sets `status: rejected` for
false positives. The row stays in the record but is excluded visually from
totals by the frontend (the backend still counts it in `total_items` and in
`total_estimated_value` — **filter `rejected` out client-side when totalling**).

### `GET /api/items/{item_id}` → `EvidenceItem`

### `GET /api/items/{item_id}/revisions` → `Revision[]`

```json
[
  {
    "id": "rev_…",
    "item_id": "itm_44b1ee907a2c",
    "editor": "marcus",
    "note": "Receipt found",
    "changes": { "estimated_value": 2400, "status": "human_corrected" },
    "created_at": "2026-03-14T18:40:02.981Z"
  }
]
```

`changes` holds only the fields that actually changed, in their JSON form.

---

## 6b. Public evidence routes (signed, for CREAO)

These are **not for the frontend** — they exist so the CREAO agent can download
the exact files referenced in `room_photos` / `receipt_files`.

| Route | Serves |
|---|---|
| `GET /api/evidence/{upload_id}/public?exp=…&sig=…` | the uploaded photo/receipt bytes |
| `GET /api/evidence/payloads/{investigation_id}/public?exp=…&sig=…` | a prior `rebuild-result.json` for `previous_result` |

- Unauthenticated (CREAO has no session with us) but signed: HMAC-SHA256 over
  `path|id|expiry`.
- `403` when expired or tampered, `404` for an unknown id, `410` if the file was
  deleted. No listing route.
- `Cache-Control: private, no-store`; links expire after
  `REBUILD_EVIDENCE_URL_TTL_SECONDS` (default 900 s).
- Omitted from the OpenAPI schema since clients should never call them directly.

---

## 6c. Recovering an already-completed CREAO run

A CREAO run costs credits the moment it executes, so a failure in our download or
import step must not force a second paid run. `POST /api/creao/recover` imports
the result of a run that already finished. It **never creates a CREAO run** — the
only outbound calls are `GET /apps/{appId}/runs/{run_id}` and the artifact
download.

```json
{
  "run_id": "56821244-bbbf-461d-bb50-52cd3aeabfe3",
  "investigation_id": "inv_b58112068a26",
  "case": { "id": "case_18b532045bc7", "title": "42 Willow Creek Rd",
            "address": "Asheville, NC", "disaster_type": "flood", "created_by": "priya" },
  "evidence": [
    { "evidence_id": "ev_001", "upload_id": "upl_e290d454b817",
      "filename": "01_room_armchair.jpg", "content_type": "image/jpeg", "kind": "photo" }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `run_id` | yes | the completed CREAO run |
| `investigation_id` | no | reuse the original local investigation record |
| `case` | only if the case is gone | restores the case record from these fields |
| `evidence` | only if the case is gone | maps `evidence_id` → `upload_id`; files must still exist on disk |

Behaviour:

- **Validation first.** `rebuild-result.json` must carry all seven top-level
  fields, and every `evidence_id` / `supporting_evidence_ids` must resolve to an
  upload of this case. Nothing is written until both pass.
- **Status flips to `complete` only after a successful import**, with
  `agent_run_id` set to the CREAO run id and `recovered: true`,
  `evidence_transport: "recovered"`.
- **Idempotent.** Retrying returns `already_recovered: true` with
  `items_created: 0`. Items are keyed on `(investigation_id, agent_item_id)`, so
  a forced re-import creates no duplicates.
- **Human corrections win.** An existing item is never overwritten by a
  re-import; `revision`, `updated_by` and revision history are untouched.
- **No state is invented.** Without `case` the request fails
  `404 CASE_NOT_RESTORABLE`; a missing upload file fails `404 UPLOAD_BYTES_MISSING`;
  an unresolvable evidence filename fails `404 EVIDENCE_METADATA_MISSING`.

```json
{
  "recovered": true, "already_recovered": false,
  "run_id": "56821244-…", "run_status": "completed", "case_id": "case_…",
  "investigation": { "id": "inv_…", "status": "complete", "recovered": true,
                     "agent_run_id": "56821244-…", "item_ids": ["itm_…"] },
  "items_created": 8, "items_already_present": 0,
  "evidence_ids": ["ev_001", "ev_002", "ev_003", "ev_004"],
  "restored_case": true, "restored_uploads": ["upl_…"]
}
```

> **Persistence caveat:** case, upload and investigation metadata live in process
> memory. After a restart only the uploaded bytes survive on disk, so recovery
> needs `case` + `evidence` in the request to rebuild the records. A durable
> store (SpacetimeDB or SQLite) removes that requirement — see
> [creao-api-integration.md](./creao-api-integration.md) §9.

---

## 6d. Review inventory — `GET /api/cases/{case_id}/review`

Read-only view of the **original CREAO fields** for the review UI. Before this
endpoint those fields were only inside `agent_record`, which is excluded from API
responses.

`?refresh=true` (default) re-reads SpacetimeDB first when
`STORE_BACKEND=spacetime`, so the response is never a stale cache. `source`
tells you which path answered: `spacetimedb`, `memory`, or
`memory (stale: SpacetimeDB unreachable)`.

```http
GET /api/cases/case_18b532045bc7/review
```

```jsonc
{
  "case": { "id": "case_18b532045bc7", "title": "42 Willow Creek Rd",
            "address": "Asheville, NC", "disaster_type": "flood",
            "status": "review", "item_count": 8, "upload_count": 4 },

  "source": "spacetimedb",
  "generated_at": "2026-10-24T06:55:27.230Z",

  "evidence": [
    { "evidence_id": "ev_001", "upload_id": "upl_e290d454b817",
      "filename": "01_room_armchair.jpg", "evidence_type": "photo",
      "content_type": "image/jpeg", "readable": true,
      "description": "Wide interior view of a living room …",
      "size_bytes": 37170, "url": "/api/uploads/upl_e290d454b817/file" }
  ],

  "items": [
    {
      "id": "itm_a16684bd49c2",              // backend id → PATCH /api/items/{id}
      "agent_item_id": "itm_001",            // original CREAO id
      "investigation_id": "inv_b58112068a26",
      "name": "Brown fabric armchair",
      "room": "Living room",
      "category": "other",
      "brand": null,                          // null = agent could not read it
      "model": null,
      "purchase_date": null,
      "purchase_price": null,                 // printed receipt value, a STRING
      "currency": null,                       // null = unknown, never invented
      "location_in_image": "ev_001: right third of the frame …",
      "visible_observations": "Deep brown upholstery … water line across the base",
      "condition": null,
      "supporting_evidence_ids": ["ev_001", "ev_002"],
      "user_confirmed_fields": [],
      "review_status": "needs_review",
      "damage_level": "unknown",
      "confidence": 0.0,
      "estimated_value": null,                // separate from purchase_price
      "status": "ai_suggested",
      "revision": 1,                          // send as if_revision on PATCH
      "updated_by": null,
      "updated_at": "2026-10-24T05:56:54.132Z"
    }
  ],

  "findings": [
    { "id": "inv_…:fnd_001", "finding_id": "fnd_001",
      "finding_type": "possible_duplicate", "item_ids": ["itm_001"],
      "evidence_ids": ["ev_001", "ev_002"],
      "explanation": "The armchair appears in two files …",
      "status": "needs_review", "resolution_reason": null }
  ],

  "questions": [
    { "id": "inv_…:q_001", "question_id": "q_001", "item_id": "itm_001",
      "question": "Is the brown armchair yours …?",
      "reason": "Ownership affects the claim.",
      "status": "open", "resolution_reason": null }
  ],

  "decisions": [
    { "item_id": "itm_a16684bd49c2", "agent_item_id": "itm_001",
      "editor": "priya", "note": "verified on site",
      "changes": { "estimated_value": 425.0, "condition": "base is cracked" },
      "created_at": "2026-10-24T07:02:11.004Z" }
  ],

  "investigations": [ { "id": "inv_b58112068a26", "status": "complete",
                        "agent_run_id": "56821244-…", "recovered": false } ],

  "stats": { "total_items": 8, "ai_suggested": 8, "human_confirmed": 0,
             "human_corrected": 0, "rejected": 0,
             "total_estimated_value": 0.0, "currency": "USD",
             "contributors": [] }
}
```

### Guarantees

| Rule | Detail |
|---|---|
| Agent values are verbatim | `brand`, `model`, `purchase_date`, `purchase_price`, `location_in_image`, `visible_observations` come straight from `rebuild-result.json`; missing stays `null` |
| `currency` may be `null` | The agent often reports none. It is **never** defaulted to `USD`; statistics fall back to `USD` for display only |
| `purchase_price` ≠ `estimated_value` | `purchase_price` is a printed string from a receipt. `estimated_value` is a reviewer-entered number. The backend never copies one into the other |
| Both ids present | `id` is what `PATCH /api/items/{id}` expects; `agent_item_id` is the CREAO `itm_###` |
| `revision` | Send it back as `if_revision`; a stale value returns **409** with the current row |
| Human state is separate | `status`, `revision`, `updated_by` and `decisions[]` describe human work; agent output is untouched |
| No secrets | No storage paths, API keys, signed evidence URLs or CREAO artifact URLs. `evidence[].url` is the local `/api/uploads/{id}/file` route |

### Correcting an item

```http
PATCH /api/items/itm_a16684bd49c2
{ "estimated_value": 425, "condition": "base is cracked",
  "editor": "priya", "note": "verified on site", "if_revision": 1 }
```

`200` → new `revision: 2`; `409` → `detail.current` holds the winning row.
Re-read the review endpoint and re-apply on top of it.

---

## 6e. Review decisions — questions and findings

Two write endpoints. Both bump a per-record `revision`, append to an audit trail,
and never touch inventory items, `user_confirmed_fields` or receipt prices.

### `PATCH /api/cases/{case_id}/questions/{question_ref}`

`question_ref` accepts either the backend id (`inv_…:q_001`) or the agent id
(`q_001`) — both are in the review payload.

```json
{ "answer": "Yes, mine — it was in the room.", "reviewer": "priya", "if_revision": 0 }
```

| Field | Notes |
|---|---|
| `answer` | string, or `null`/`""` to **clear** a saved answer |
| `reviewer` | required when saving an answer; free text (see the auth note) |
| `if_revision` | the `revision` you rendered; `0` for a never-answered question |

`200` → the updated question:

```json
{
  "id": "inv_b58112068a26:q_001", "question_id": "q_001",
  "item_id": "itm_001",
  "question": "Is the brown armchair yours and was it in the room when the water entered?",
  "reason": "Ownership affects the claim.",
  "evidence_ids": ["ev_001", "ev_002"],
  "status": "answered",
  "resolution_reason": null,
  "answer": "Yes, mine — it was in the room.",
  "answered_by": "priya",
  "answered_at": "2026-10-24T19:40:02.114Z",
  "revision": 1
}
```

Clearing (`answer: null`) returns `status: "open"`, `answer: null`,
`answered_by: null`, `answered_at: null` and `revision` incremented — a question
is only `answered` while an answer is actually stored. The agent's `question`
and `reason` text are never rewritten.

### `PATCH /api/cases/{case_id}/findings/{finding_ref}`

`finding_ref` accepts the backend id or `fnd_001`.

```json
{ "resolution": "confirmed", "reason": "Second photo shows the same chair.",
  "reviewer": "priya", "if_revision": 0 }
```

| Field | Notes |
|---|---|
| `resolution` | `confirmed` \| `rejected` \| `needs_more_evidence` \| `unresolved` |
| `reason` | **required when `resolution` is `rejected`** |
| `reviewer` | required |
| `if_revision` | the `revision` you rendered |

`200` → the updated finding, with the agent's `finding_id`, `finding_type`,
`explanation`, `item_ids` and `evidence_ids` unchanged, plus `resolution`,
`resolution_reason`, `resolved_by`, `resolved_at`, `revision`.

### Errors

```jsonc
// 400 — rejection without a reason, or a missing reviewer
{ "detail": { "code": "REASON_REQUIRED",
              "message": "A reason is required when rejecting a finding." } }

// 404 — case, question or finding not in this case
{ "detail": { "code": "QUESTION_NOT_FOUND",
              "message": "Question 'q_999' was not found in case 'case_…'." } }

// 409 — stale revision; `current` is the saved decision to reconcile against
{ "detail": {
    "code": "REVISION_CONFLICT",
    "message": "Question 'q_001' is at revision 2, not 1.",
    "current": { "id": "inv_…:q_001", "answer": "Yes, mine.", "revision": 2, "…": "…" } } }
```

Codes: `REASON_REQUIRED`, `REVIEWER_REQUIRED`, `QUESTION_NOT_FOUND`,
`FINDING_NOT_FOUND`, `REVISION_CONFLICT`.

Re-submitting the **same** `if_revision` twice is a conflict, not a silent
double-apply: the second call returns `409` with the already-saved decision.

### Audit trail

Every accepted change appends a `DecisionEvent`:

```json
{ "id": "dec_…", "case_id": "case_…", "target_type": "question",
  "target_id": "inv_…:q_001", "agent_ref": "q_001", "revision": 1,
  "value": "Yes, mine — it was in the room.", "reviewer": "priya",
  "note": null, "created_at": "2026-10-24T19:40:02.114Z" }
```

### Reading decisions back

`GET /api/cases/{case_id}/review` now embeds the decision on each record **and**
returns two audit arrays:

| Location | Links to the source by |
|---|---|
| `questions[].answer / answered_by / answered_at / revision / status` | `questions[].id` (or `question_id`) |
| `findings[].resolution / resolution_reason / resolved_by / resolved_at / revision` | `findings[].id` (or `finding_id`) |
| `question_decisions[]` / `finding_decisions[]` | `target_id` (backend id) and `agent_ref` (`q_001` / `fnd_001`) |
| `decisions[]` (unchanged) | inventory items: `item_id` / `agent_item_id` |

`decisions[]` remains **item** decisions only (PATCH /api/items/{id}); the two new
arrays cover questions and findings. All previous top-level fields are unchanged.

> **Identity:** `reviewer` / `answered_by` / `resolved_by` are client-supplied
> strings. They are **not authenticated identities** — there is no auth layer.
> Treat them as display names, and do not rely on them for authorization.

---

## 6f. Follow-up investigations

Starting a second investigation for a case (`POST /api/cases/{id}/investigations`)
now reuses stable evidence IDs, sends saved answers to the agent, and reconciles
the new inventory into the existing one instead of duplicating it.

### What the agent receives

`inputs` keeps every existing field and adds one:

| Field | Type | Notes |
|---|---|---|
| `review_answers` | **JSON string** | saved human answers; `[]` when nothing is answered |

```json
"[{\"question_id\":\"q_001\",\"answer\":\"Yes, mine.\",\"answered_by\":\"priya\"}]"
```

**Exactly three keys** — the Investigator v1.3.1 contract. The question text,
`item_id` and `answered_at` are **not** sent; they stay in SpacetimeDB on the
question row and in `decision_event`, so the review API and the audit trail keep
them.

- unanswered questions are **omitted**;
- an answer is context for the agent and is **never** emitted inside
  `confirmed_items` — an answer does not confirm an inventory field;
- `confirmed_items`, `previous_result` and the evidence arrays are unchanged.

> ✅ **Confirmed** by Person 2 for Investigator **v1.3.1**: the agent accepts
> `review_answers` as JSON text, serialized as a string, with exactly
> `question_id`, `answer`, `answered_by`. Verified by
> `tests/test_followup.py::test_review_answers_payload_matches_agent_contract_exactly`.

### Evidence IDs are stable

Each upload gets an `evidence_id` (`ev_001`…) **once**, at first use, and it is
persisted. Follow-up runs reuse it; a newly uploaded file takes the next free
number. Existing IDs are never renumbered, and a result that references an
unknown ID is rejected (`CREAO_INVALID_RESULT`) rather than being mapped onto
some other file.

### Item reconciliation

| `reconciliation` | Meaning |
|---|---|
| `new` | genuinely new proposal from this run |
| `matched` | reconciled with an existing belonging; **backend `id` preserved** |
| `identity_conflict` | the agent changed the item id but name + shared evidence suggest the same object — **flagged, never merged** |

Additional item fields: `possible_match_item_id` (for conflicts) and
`agent_proposal` (the latest AI proposal when a human already decided the row).

Rules:

- matching is by `agent_item_id` within the case, so a retry or a follow-up can
  never create a second row for the same belonging;
- an item a human touched (status change, revision > 1, confirmed fields) is
  **never overwritten** — the incoming values go to `agent_proposal`;
- an untouchted item is refreshed in place and keeps its id and revision;
- a different object with a similar name stays a separate row; only
  name **and** a shared evidence reference produce an `identity_conflict` flag;
- findings and questions are matched on `(case, finding_id/question_id)`, so
  their agent text updates while answers, resolutions, reviewers, timestamps,
  revisions and history survive.

### Investigation metadata

```json
{
  "follow_up": true,
  "review_answers_sent": 1,
  "items_created": 2,
  "items_reconciled": 1,
  "items_flagged": 1,
  "status": "complete"
}
```

`follow_up` is true whenever the case already had a completed investigation —
independent of whether a `previous_result` URL could be built.

### Frontend fields on review items

`GET /api/cases/{id}/review` adds, per item: `reconciliation`,
`possible_match_item_id`, `agent_proposal`. Everything previously returned is
unchanged, so existing UI code keeps working.

---

## 6g. Researcher — Rebuild Product Researcher (confirmed contract)

A second, independent app-scoped CREAO agent (App ID
`a7260cbc-8253-4fa7-88d3-c644267e3221`) with its own credential
(`CREAO_RESEARCH_API_KEY`). The Investigator's App ID and credential are never
used for research, and vice versa.

Research is **advisory**. Candidates live in separate records, are always
`unverified_possible_match`, and never modify an item's brand, model,
purchase_price, purchase_date, currency, estimated_value, confirmed fields or
review status.

### Routes

| Route | Purpose |
|---|---|
| `POST /api/items/{item_id}/research` | start (or rejoin) research |
| `GET /api/research/{research_id}` | status + candidates + queries + limitations |
| `GET /api/items/{item_id}/research` | saved research history (newest first) |

Case-scoped aliases remain for compatibility:
`POST /api/cases/{c}/items/{i}/research`, `GET /api/cases/{c}/items/{i}/research/{run}`,
`GET /api/cases/{c}/items/{i}/research`, `GET /api/cases/{c}/research/{run}/candidates`.

### Start

```json
{ "region_currency": "USD", "requested_by": "priya" }
```

`region_currency` is **optional and explicit** — the backend never infers a
region or currency.

`202 Accepted` (new run):

```json
{
  "id": "rsch_9f21c0a7b3d4", "case_id": "case_…", "item_id": "itm_…",
  "status": "queued", "mode": "live",
  "app_id": "a7260cbc-8253-4fa7-88d3-c644267e3221",
  "region_currency": "USD", "agent_run_id": null,
  "candidate_ids": [], "queries_run": [], "limitations": []
}
```

`200 OK` with the **existing run** when that item already has one queued or
running — a duplicate click never starts a second paid run.

### Poll

```json
{
  "run": {
    "id": "rsch_…", "status": "complete", "agent_run_id": "creao-run-…",
    "thread_id": "…", "error": null, "region_currency": "USD",
    "candidates_valid": 1, "candidate_ids": ["rc_…"],
    "queries_run": ["strandmon wing chair"],
    "limitations": ["Prices shown are retail listings, not purchase prices."],
    "input_summary": { "item_ref": "itm_…", "visible_observations": "…",
                       "room_photo_upload_ids": ["upl_…"], "region_currency": "USD" }
  },
  "candidates": [
    {
      "id": "rc_…", "candidate_id": "cand_001",
      "run_id": "rsch_…", "case_id": "case_…", "item_id": "itm_…",
      "brand": "IKEA", "model": "STRANDMON",
      "product_name": "STRANDMON wing chair",
      "product_url": "https://www.ikea.com/us/en/p/strandmon-wing-chair",
      "matching_features": ["wing back", "deep brown upholstery"],
      "differing_features": ["leg colour differs"],
      "missing_verification": "Measurements not confirmed against the photo.",
      "price_as_displayed": "$349.00", "currency": "USD",
      "source_accessed_at": "2026-10-24T21:00:00Z",
      "status": "unverified_possible_match", "unverified": true
    }
  ]
}
```

Statuses: `queued` → `running` → `complete` | `failed`. On failure `error` carries
the code and message (e.g. `RESEARCH_INVALID_RESULT: item_ref mismatch…`).

`signed evidence URLs are never returned` — only `room_photo_upload_ids` inside
`input_summary`. The full `input_snapshot` is stored server-side for audit.

### Recovery and diagnostics

A completed CREAO run can be re-imported without starting a new run:

```http
POST /api/research/{research_id}/recover
{ "creao_run_id": "11626040-0ac3-44db-84cd-59c6f22279cc" }
```

`200` → `{ run, candidates, source, already_recovered }`. The backend record is
marked `complete` only after the artifact validates **and** (with
`STORE_BACKEND=spacetime`) persists. Candidates are deterministic per run, so a
repeated recovery never duplicates them.

`GET /api/research/{research_id}/diagnostics` returns a **sanitized** view of the
CREAO run's output structure — status, `result` field names, and one row per
artifact with `title`, `type`, `has_url`, `bytes`, `json`, `keys`, `error`.
It never contains URLs, headers or credentials.

### Artifact errors (coded)

| Code | Meaning |
|---|---|
| `RESEARCH_NO_ARTIFACTS` | the run response had no artifact metadata at all |
| `RESEARCH_ARTIFACT_NOT_FOUND` | artifacts exist, none is a candidates document |
| `RESEARCH_ARTIFACT_AUTH` | the artifact endpoint rejected the credential (401/403) |
| `RESEARCH_ARTIFACT_DOWNLOAD` | download/redirect failure, or empty body |
| `RESEARCH_INVALID_JSON` | a candidate document was found but is not JSON |
| `RESEARCH_INVALID_RESULT` | JSON found but fails the contract (item_ref, count, status, URL) |
| `RESEARCH_PERSIST_FAILED` | validated, but SpacetimeDB persistence failed |

**Artifact discovery does not depend on the title.** Every artifact the run
returns is probed — `result.artifacts`, `result.outputs`, `result.files`,
`result.attachments` — and the first body that parses as JSON *and* matches
`{item_ref, candidates}` wins. A title like `"Outputs"` with the file name only in
the artifact `id` therefore still resolves.

### Agent inputs (built server-side)

| Field | Type | Source |
|---|---|---|
| `item_ref` | string | backend item id |
| `visible_observations` | string \| null | the Investigator's observations |
| `distinguishing_features` | string[] | documented features only (never invented) |
| `evidence_context` | **JSON string** | evidence IDs, descriptions, attributed human corrections |
| `room_photos` | string[] | HTTPS URLs of supporting photographs |
| `region_currency` | string | present only when supplied explicitly |

### Output validation

`research-candidates.json` must be `{item_ref, candidates[], queries_run[], limitations[]}`.
The run **fails** (never fabricates) when:

1. `item_ref` ≠ the requested backend item id;
2. more than **3** candidates;
3. any candidate `status` ≠ `"unverified_possible_match"`;
4. a `product_url` is present but not `http(s)`;
5. the document is malformed or an artifact is unreadable.

An **empty** `candidates` array is valid and means "no matches". Missing values
stay `null`. Re-importing the same run is idempotent (candidate ids are
deterministic).

**Shapes preserved verbatim** (verified against the real run
`11626040-0ac3-44db-84cd-59c6f22279cc`):

| Field | Actual shape |
|---|---|
| `matching_features` / `differing_features` | arrays of strings |
| `missing_verification` | **array** of strings (not a single string) |
| `queries_run` | array of **objects** `{"query": "...", "tool": "web_search" \| "web_fetch"}` |
| `currency` | `null` when the agent could not determine it — never defaulted |
| `price_as_displayed` | string exactly as the page rendered it (`"€ 349"`, `"from $2,250.00"`) |
| `source_accessed_at` | ISO timestamp as returned — never corrected |

`queries_run` entries keep their object form; they are not stringified.

---

## 7. Comments, events and export

### `GET /api/cases/{case_id}/comments` → `Comment[]`
### `POST /api/cases/{case_id}/comments` → `201 Comment`

```json
{ "author": "marcus", "body": "This is the upstairs unit, not the basement.", "item_id": "itm_…" }
```

`item_id` is optional; omit it for a case-level comment.

### `GET /api/cases/{case_id}/events` → `Event[]`

Polling fallback. `?limit=50` (1–200), oldest→newest, last N only.

### `GET /api/cases/{case_id}/events/stream` → `text/event-stream` (SSE)

Live updates for one case. No auth, no extra headers required.

```
: connected

event: item.created
data: {"id":"evt_…","type":"item.created","case_id":"case_…","payload":{"item":{…}},"created_at":"…"}

: keep-alive
```

- A comment line (`: keep-alive`) is sent every 15 s to keep proxies open.
- `event:` is the event `type`; `data:` is the full `Event` object as JSON.
- Reconnect with `EventSource`; the browser does this automatically. Events are
  not replayed, so refetch `GET /api/cases/{id}` after a reconnect.
- **Once SpacetimeDB subscriptions are live, the frontend should use those
  instead** — this endpoint exists so Person 1 is never blocked.

Event types: `case.created`, `case.updated`, `upload.created`,
`investigation.started`, `investigation.completed`, `item.created`,
`item.updated`, `comment.created`.

### `GET /api/cases/{case_id}/export.pdf` → `application/pdf`

Returns the full recovery record as a downloadable attachment
(`Content-Disposition: attachment; filename="rebuild-<slug>.pdf"`), plus
`X-Rebuild-Item-Count`.

Contents: case header, summary counters, one row per belonging with its source
upload and review status, the correction history (who changed what, when), and
the contributor list.

Frontend should fetch it as a blob and trigger a download:

```ts
const res = await fetch(`/api/cases/${caseId}/export.pdf`);
const blob = await res.blob();
const url = URL.createObjectURL(blob);
```

Exporting with zero items is allowed (useful for a smoke test) but produces a
one-page document.

### `POST /api/demo/seed` → `201 { case_id, items, next }`

Creates a populated demo case (6 belongings across 3 rooms, 2 reviewers,
mixed review states). Demo convenience only — safe to remove.

---

## 8. Enums

| Enum | Values |
|---|---|
| `CaseStatus` | `open`, `investigating`, `review`, `exported`, `archived` |
| `UploadKind` | `photo`, `receipt` |
| `InvestigationStatus` | `queued`, `running`, `complete`, `failed` |
| `ItemStatus` | `ai_suggested`, `human_confirmed`, `human_corrected`, `rejected` |
| `DamageLevel` | `none`, `light`, `moderate`, `severe`, `destroyed`, `unknown` |

Unknown values sent to the API produce a `422` (except inside agent output,
which is normalised to `unknown`).

---

## 9. Frontend integration notes

- **TypeScript types:** generate from `/openapi.json` rather than hand-writing
  (`npx openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.ts`).
- **Vite proxy:** avoids CORS entirely.

  ```ts
  // vite.config.ts
  server: { proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } } }
  ```

- **Rendering review state:** show `ai_suggested` items with a "needs review"
  treatment and a confidence badge; `confidence < 0.7` is a good threshold for
  an extra visual warning.
- **Review queue:** `GET /api/cases/{id}/items?status=ai_suggested`.
- **Conflict handling:** on `409`, take `detail.current` as the new truth, show
  the user what changed, and let them reapply.

---

## 10. Open questions

⚠️ These are assumptions in the current code and must be confirmed:

1. ~~**CREAO agent endpoint shape.**~~ **Resolved.** Create/poll/artifact
   retrieval now match Person 2's captured runs exactly and are covered by
   tests using those fixtures. See
   [creao-api-integration.md](./creao-api-integration.md).
1b. ~~**Evidence upload.**~~ **Resolved**: public HTTPS URL arrays, confirmed
   by Person 2 and implemented with signed links (§5, `/api/evidence/…/public`). The app-scoped route has no documented
   file-upload endpoint. Person 2's demo run inspected files that are committed
   to the `agent` branch under `demo/`, which points to **pre-attached**
   evidence rather than per-run upload. `CREAO_EVIDENCE_TRANSPORT` must be set
   explicitly and is refused when `unset`, so no run starts on an unproven
   assumption. Person 2 needs to supply the create-request body from their
   successful run to close this.
2. **Agent ownership.** `CREAO_AGENT_ID` must be a *personal* agent owned by the
   same account as the `cr_sk_` API key, and must appear in `GET /v1/agents`.
   A team-owned agent, or the app id of an agent installed from another account,
   returns `AGENT_NOT_FOUND`. Verify with `POST /api/creao/validate` before a
   demo. Person 2 owns confirming which id is correct.
3. **Agent output shape.** We accept the inventory under
   `items`/`belongings`/`inventory`/`findings`/`contents`, delivered as a JSON
   artifact or in `result.text`. Person 2 should confirm `rebuild-result.json`
   matches one of these; extending `normalise_result` is preferable to changing
   this contract.
4. **SpacetimeDB reducer name.** Assumed `publish_event(payload_json: String)`
   on module `rebuild`. Confirm with `/spacetimedb` and update
   `services/spacetime.py::_REDUCER`.
5. **Multi-user identity.** `editor` / `author` / `uploaded_by` are plain
   strings chosen by the client — there is no auth. Confirm how Presence /
   logins should work for the demo.
6. **Persistence.** The backend holds state in memory. If SpacetimeDB is not
   wired before judging, a server restart clears the workspace.

---

## Changelog

| Version | Change |
|---|---|
| 0.8.2 | Preserve the real document's shapes: `missing_verification` stays an array, `queries_run` keeps its `{query, tool}` objects, null currency untouched. Regression test uses the live `research-candidates.json` byte-for-byte. |
| 0.8.1 | Artifact retrieval no longer trusts the artifact title (probes every artifact container and validates by content); `POST /api/research/{id}/recover` re-imports a completed CREAO run without a new run; `/diagnostics` reports the sanitized run structure; coded artifact errors. |
| 0.8.0 | Researcher aligned to Person 1's confirmed contract: canonical `/api/items/{id}/research` + `/api/research/{id}` routes, explicit `region_currency`, exact input keys, strict `research-candidates.json` validation (max 3, fixed status, item_ref match), active-run dedupe, redacted input snapshot. |
| 0.7.0 | Researcher agent integration: separate `CREAO_RESEARCHER_API_KEY`/App ID, async research runs per item, `research-candidates.json` validation, unverified candidates isolated from inventory, persisted in SpacetimeDB. Also made `brand`/`model`/`purchase_date`/`purchase_price` human-correctable. |
| 0.6.4 | `review_answers` narrowed to the exact v1.3.1 contract (`question_id`, `answer`, `answered_by` as a JSON string); saved timestamps/history still stored in SpacetimeDB. |
| 0.6.3 | Follow-up investigations: `review_answers` JSON-string input, stable per-upload `evidence_id`, case-level item reconciliation (`new`/`matched`/`identity_conflict`), findings/questions matched by agent id so human decisions survive, plus `follow_up` / `items_*` counters. |
| 0.6.2 | Question answers (`PATCH /api/cases/{id}/questions/{q}`) and finding resolutions (`PATCH /api/cases/{id}/findings/{f}`) with per-record revisions, 400/404/409 handling, an append-only `decision_event` audit trail, persistence in SpacetimeDB and live broadcast. Review responses now embed answers/resolutions. |
| 0.6.1 | `GET /api/cases/{id}/review`: agent fields (brand, model, purchase_date, purchase_price, nullable currency, location_in_image, visible_observations, user_confirmed_fields), findings, questions and decisions, with both backend and agent ids. Nullable currency no longer defaulted to USD; `purchase_price` never folded into `estimated_value`. |
| 0.5 | `POST /api/creao/recover` imports an already-completed CREAO run without starting a new one: shared import pipeline, validation-before-write, idempotent retries, human corrections preserved, case/upload restoration from supplied metadata + disk. |
| 0.4 | URL-based evidence: `room_photos`/`receipt_files` are arrays of short-lived signed HTTPS URLs served by this backend; JSON-string `evidence_manifest`/`confirmed_items`; `previous_result` omitted on first runs; signed public evidence routes; pre-flight URL reachability check; evidence-ID validation against the IDs actually sent. |
| 0.3 | Switched to Person 2's verified **app-scoped API Trigger** (`capi_` key, `appId` in URL, `{"inputs": …}`, poll by `id`, authenticated artifact download of `rebuild-result.json`). `Investigation` gains `evidence_transport` / `evidence_ids`; `EvidenceItem` gains `agent_item_id` / `supporting_evidence_ids` / `review_status`; new `503 CREAO_EVIDENCE_TRANSPORT_UNVERIFIED` gate. The `/v1` CLI path is removed. |
| 0.2 | CREAO integration rebuilt against the documented API: `mode` (`mock`/`live`) on investigations, `conversation_id`, `credits_used`; richer `/api/health`; new `/api/creao/status` and `/api/creao/validate`; `503 CREAO_NOT_CONFIGURED` instead of a silent mock fallback. |
| 0.1 | Initial contract: cases, uploads, investigations, evidence items + revisions, comments, SSE events, PDF export, demo seed. |

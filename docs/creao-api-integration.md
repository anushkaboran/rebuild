# CREAO integration — app-scoped API Trigger (verified shapes)

**Owner:** Person 3 (backend)
**Status:** implemented in `backend/app/services/creao.py` (v0.4.1) — URL-based evidence confirmed
**Agent:** Rebuild Evidence Investigator — app id `9819d9a5-89e0-4d62-a284-91bac67f8cd1`
**Evidence base:** Person 2's captured runs on the `agent` branch —
`agent/api-create-response.json`, `agent/api-poll-response.json`,
`agent/api-run-result.json`, `agent/SKILL.md`, `agent/../docs/contract.md`
(copied into `backend/tests/fixtures/` and used as test fixtures).

This document replaces the earlier `/v1` CREAO CLI design. The team-owned
investigator is not reachable through `/v1` (CREAO omits team agents from
`GET /v1/agents`), and Person 2 has proven the app-scoped route works.

---

## 1. The three request/response shapes we now rely on

### Create

```
POST https://agent.creao.ai/api/v1/apps/9819d9a5-89e0-4d62-a284-91bac67f8cd1/runs
Authorization: Bearer capi_…
Content-Type: application/json

{"inputs": { … }}
```

```json
{"id":"f5ddc714-e0d0-49fa-b2af-df49a1c4a814","status":"pending",
 "appId":"9819d9a5-89e0-4d62-a284-91bac67f8cd1","appName":"Rebuild Evidence Investigator"}
```

Note the field is `id` on this route (not `run_id`), and there is **no
`agent_id`** in the body — the app id is in the URL. CREAO keeps the two route
families separate: `/v1` takes `agent_id` + `input`, this one takes `appId` +
`inputs`.

### Poll

```
GET https://agent.creao.ai/api/v1/apps/{appId}/runs/{run_id}
```

```json
{"id":"f5ddc714-…","status":"completed","threadId":"e9066165-…",
 "result":{
   "text":"{\"status\":\"success\",\"summary\":\"…\"}",
   "chatText":"…",
   "artifacts":[
     {"id":"workspaces/…/files/rebuild-result.json",
      "url":"https://agent.creao.ai/api/v1/runs/f5ddc714-…/artifacts/download?artifactId=workspaces%2F…",
      "type":"application/json","title":"rebuild-result.json"}]},
 "errorCode":null,"startedAt":"…","completedAt":"…","createdAt":"…"}
```

`result.text` is a **JSON string containing a summary**, not the inventory. The
authoritative inventory is the `rebuild-result.json` artifact.

### Download

`artifact.url` is an **authenticated API route on the same host**
(`/api/v1/runs/{runId}/artifacts/download?artifactId=…`), so it needs the same
`Authorization: Bearer capi_…` header.

**Verified against a real run (2026-10-24):** that route answers **`302`** with a
`Location` pointing at a presigned S3 object
(`storage-bucket-creao-user-upload-production.s3.us-west-2.amazonaws.com/...`).
The client therefore follows exactly one redirect with a **clean, anonymous
client**, so the capi_ key is sent to the API route and never to the storage
host. A response with no redirect and no body raises
`CREAO_ARTIFACT_DOWNLOAD_FAILED` instead of attempting to parse an empty string.

This was a real bug in v0.4.0: httpx does not follow redirects by default, so the
302 body (`b""`) was handed to `json.loads`, the investigation failed with
`CREAO_INVALID_RESULT`, and the already-successful run's inventory was lost. The
fix is in v0.4.1 and is covered by a wire-level test where the artifact route
302s to a second local server that asserts **no `Authorization` header** arrives.

---

## 2. `inputs` — built only from the agent's declared form fields

From `agent/SKILL.md`:

| Field | Type | We send |
|---|---|---|
| `case_description` | text, required | composed from the case (title, address, disaster, notes) |
| `room_name` | text, required | `room_hint` from the investigation request |
| `room_photos` | files, required | **see §3 — transport dependent** |
| `receipt_files` | files | **see §3** |
| `evidence_manifest` | JSON text | `[{"evidence_id","filename","description"}]` |
| `confirmed_items` | JSON text | only when supplied |
| `previous_result` | file | not yet wired |

Per the agent contract rule 1, **the backend assigns `ev_###` IDs before the
call**; the agent binds them unchanged. One upload = `ev_001` today.

---

## 3. Evidence delivery — CONFIRMED: public HTTPS URLs

Person 2 confirmed the mechanism: `room_photos` and `receipt_files` are **arrays
of public HTTPS URL strings**. CREAO downloads those files itself; nothing is
pre-attached to the agent. This replaces every earlier hypothesis
(pre-attached files, base64 in `inputs`).

```json
{
  "inputs": {
    "case_description": "Living room flooded; documenting damaged belongings.",
    "room_name": "Living room",
    "room_photos": ["https://…/01_room_armchair.jpg", "https://…/02_armchair_closeup.jpg"],
    "receipt_files": ["https://…/04_receipt_lamp.png", "https://…/05_receipt_groceries.png"],
    "evidence_manifest": "[{\"evidence_id\":\"ev_001\",\"filename\":\"…\"}]",
    "confirmed_items": "[]"
  }
}
```

Three details are load-bearing and are asserted in tests:

| Detail | Rule |
|---|---|
| `room_photos` / `receipt_files` | arrays of **URL strings**, split by upload `kind` |
| `evidence_manifest` | a **JSON-serialized string**, not an array |
| `confirmed_items` | a **JSON-serialized string**, not an array |
| `previous_result` | **omitted entirely** on a first run; a URL on follow-ups |

### Where the URLs come from

Uploaded bytes live on our disk, so the backend issues **short-lived signed
URLs** that it serves itself:

```
GET {REBUILD_PUBLIC_BASE_URL}/api/evidence/{upload_id}/public?exp=…&sig=…
```

- HMAC-SHA256 over `path|object_id|expiry`, keyed by
  `REBUILD_EVIDENCE_SIGNING_SECRET` (random per-process if unset).
- TTL `REBUILD_EVIDENCE_URL_TTL_SECONDS` (default 900 s). Private receipts never
  live at a permanent public address — the staged GitHub URLs are demo fixtures
  only and are never used for real case evidence.
- Responses are `Cache-Control: private, no-store` and `X-Robots-Tag: noindex`.
- Expired or tampered links return `403` with a reason; there is no listing
  route and no way to reach another case's evidence.
- Follow-up runs pass `previous_result` as a signed URL to
  `/api/evidence/payloads/{investigation_id}/public`.

### Refusing to run when the URLs are not fetchable

`REBUILD_PUBLIC_BASE_URL` must be an origin CREAO's cloud can reach. Two guards:

1. **Host policy** — loopback, `.local`, RFC1918 and other reserved addresses are
   rejected outright, because CREAO would never resolve them.
2. **Pre-flight fetch** (`CREAO_VERIFY_EVIDENCE_URLS=true`, the default) — every
   URL is requested before the run is created; it must answer `200`/`206` with an
   `image/*` or `application/pdf` content type and a non-empty body.

If either fails the API returns **503 `CREAO_EVIDENCE_URLS_UNAVAILABLE`** (or the
investigation fails with that code) and **no CREAO run is created** — asserted by
`test_no_api_request_when_evidence_urls_unreachable`.

`CREAO_EVIDENCE_TRANSPORT=demo_url` maps uploaded filenames onto Person 2's
staged demo files (`CREAO_DEMO_EVIDENCE_BASE_URL`) for demos and fixtures only.
Those URLs are verified too: `test_creao_network.py` (opt-in via
`CREAO_NETWORK_TESTS=1`) fetches all four and asserts image content types.

### Evidence-ID discipline

The backend assigns `ev_001…ev_NNN` in upload order and puts them in the
manifest. After the run, `validate_evidence_ids` rejects the result if:

- the agent reports an `evidence_id` that was never sent, or
- an `items[].supporting_evidence_ids` entry is unknown, or
- `evidence[]` is empty while evidence was supplied.

A mismatch fails the investigation with `CREAO_INVALID_RESULT` and stores no
items, so no claim can be traced to a photograph that wasn't provided.

## 4. Result validation

`rebuild-result.json` is validated against the agent contract before anything is
stored. Required top-level keys, all present in the captured example:

```
case_summary (string), evidence[], items[], findings[], questions[],
limitations[], receipt_extracts[]
```

Missing or wrong-typed keys raise `CREAO_INVALID_RESULT` and the investigation
fails with **no items created**. The backend never pads a partial inventory.

### Field mapping (no invented values)

| Agent `items[]` | Rebuild `EvidenceItem` |
|---|---|
| `item_id` (`itm_001`) | `agent_item_id` |
| `name` | `name` |
| `room` | `room` |
| `location_in_image` | `description` |
| `visible_observations` | `condition` |
| `supporting_evidence_ids[]` | `supporting_evidence_ids` |
| `review_status` | `review_status`; `confirmed` → `human_confirmed`, else `ai_suggested` |
| `brand` / `model` / `purchase_price` / `currency` | preserved in `agent_record` |
| — | `damage_level` = `unknown`, `confidence` = `0.0`, `estimated_value` = `null`, `bbox` = `null` |

That last row is deliberate: the agent records what is *visible* and never emits
a damage grade, a confidence score or a replacement value. Mapping
`purchase_price` onto `estimated_value` would manufacture a valuation, so it is
kept as the printed string in `agent_record` instead.

---

## 5. Polling, errors, modes

Poll `GET …/runs/{id}` every `CREAO_POLL_INTERVAL_SECONDS` (default 3 s) until
`completed` / `succeeded`, or `failed` / `cancelled` / `error`. Timeout after
`CREAO_RUN_TIMEOUT_SECONDS` (default 300 s).

| Failure | Behaviour |
|---|---|
| 401 | `CREAO_AUTHENTICATION_REQUIRED` — message points at the agent detail page; a `cr_sk_` key is detected up-front and refused with guidance |
| 404 | `CREAO_AGENT_NOT_FOUND`-style message naming `CREAO_APP_ID` |
| `status: failed` + `errorCode` | `CreaoRunFailed` carrying the code |
| no readable `rebuild-result.json` | `CREAO_INVALID_RESULT`, zero items |
| transport unset | **503 before any call** |
| timeout | `CREAO_RUN_TIMEOUT` |

Mode separation is unchanged: `CREAO_MODE=mock` never touches the network and is
badged `[SIMULATED]`; `CREAO_MODE=live` with missing configuration returns 503
rather than simulated evidence.

---

## 6. Configuration

| Variable | Required live | Notes |
|---|---|---|
| `CREAO_MODE` | `live` | `mock` (default) otherwise |
| `CREAO_APP_ID` | yes (defaulted) | `9819d9a5-89e0-4d62-a284-91bac67f8cd1` |
| `CREAO_APP_API_KEY` | yes | `capi_…` from the agent detail page in `agent.creao.ai` |
| `CREAO_APP_BASE_URL` | default ok | `https://agent.creao.ai/api/v1` |
| `CREAO_EVIDENCE_TRANSPORT` | yes | `unset` / `url` / `demo_url` / `none` |
| `REBUILD_PUBLIC_BASE_URL` | yes when `url` | public origin CREAO can reach |
| `REBUILD_EVIDENCE_SIGNING_SECRET` | recommended | HMAC key for evidence links |
| `REBUILD_EVIDENCE_URL_TTL_SECONDS` | optional | 900 |
| `CREAO_VERIFY_EVIDENCE_URLS` | optional | `true` (pre-flight fetch) |
| `CREAO_POLL_INTERVAL_SECONDS` | optional | 3 |
| `CREAO_RUN_TIMEOUT_SECONDS` | optional | 300 |
| `CREAO_MAX_EVIDENCE_FILES` | optional | 10 (agent contract cap) |

### Read-only verification (no credits)

```bash
cd backend && CREAO_MODE=live python scripts/creao_preflight.py
```

Probes `GET /apps/{appId}/runs` only — the documented list route — which proves
the key and app id without starting a run. It prints the key prefix, never the
key. `POST /api/creao/validate` does the same over HTTP.

---

## 7. Security

- `capi_` key read only from `CREAO_APP_API_KEY`; `.env` gitignored, template blank.
- `/api/health` exposes `api_key_prefix` (`capi_`) and booleans, never the value.
- The key is sent only to the configured API host; artifact downloads to any
  other host are anonymous. Tested both ways.
- No key in logs, no key in the frontend, no key in the PDF.

---

## 7b. Recovering a completed run (no second paid run)

A run is billed when it executes, not when we read it. If our own download or
import fails, the result is still retrievable: `POST /api/creao/recover` re-reads
`GET /apps/{appId}/runs/{run_id}` and the `rebuild-result.json` artifact and
imports them through the same pipeline the live path uses (`services/result_import.py`).

Verified on the real run `56821244-bbbf-461d-bb50-52cd3aeabfe3`, whose original
investigation had been marked failed by the v0.4.0 redirect bug: the same run was
recovered into its original case and investigation, 8 items imported, and a
retry afterwards created nothing (`already_recovered: true`, `items_created: 0`)
while a human correction made in between survived unchanged.

Recovery sends no run request — only a read-only GET and one authenticated
artifact download (302 → presigned S3, no key forwarded).

## 8. Persistence (the remaining gap)

Case, upload and investigation metadata live in process memory. Uploaded bytes
survive on disk; the records do not. Consequences:

* A restart loses cases and investigations.
* Recovery can rebuild a case from supplied metadata **only if** the upload files
  are still present (`case` + `evidence` in the request).
* A durable store — SpacetimeDB, as originally planned, or SQLite as a quick
  substitute — makes recovery work from `run_id` alone.

## 9. Differences from the retired `/v1` design

| | `/v1` CREAO CLI (removed) | app-scoped trigger (current) |
|---|---|---|
| Key | `cr_sk_` Account key | `capi_` app key |
| Identity | `agent_id` in body | `appId` in URL |
| Body | `{agent_id, input, workspace_id}` | `{inputs}` |
| Evidence | `POST /v1/workspaces/{id}/files` | **no documented upload route** |
| Run id field | `run_id` | `id` |
| Thread id | `conversation_id` | `threadId` |
| Output | `result.text` / `result.artifacts` | same, plus `chatText` |
| Team agents | invisible | **works** — this is why we switched |

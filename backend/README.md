# Rebuild backend

FastAPI service for the Rebuild recovery workspace: file uploads, CREAO Evidence
Investigator orchestration, collaborative review state, and PDF export.

Runs standalone with **zero configuration** — no CREAO key, no SpacetimeDB. In
that mode (`CREAO_MODE=mock`, the default) the investigator returns deterministic
stub data, clearly badged as `[SIMULATED]`, so the frontend can be built in
parallel.

---

## Quick start

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp ../.env.example ../.env         # optional — every value has a default

uvicorn app.main:app --reload --port 8000
```

Then:

| URL | What |
|---|---|
| http://localhost:8000/docs | Swagger UI — every endpoint, try it live |
| http://localhost:8000/api/health | Liveness + CREAO/SpacetimeDB wiring status |
| http://localhost:8000/openapi.json | Schema Person 1 can generate a TS client from |

Populate a screen for the demo:

```bash
curl -X POST http://localhost:8000/api/demo/seed
```

Run the tests:

```bash
pytest
```

---

## Project layout

```
backend/
├── app/
│   ├── main.py            FastAPI app, CORS, error handlers, router wiring
│   ├── config.py          Settings from env / .env (pydantic-settings)
│   ├── models.py          Pydantic request + response models = the API contract
│   ├── deps.py            Shared 404-returning lookups
│   ├── store.py           In-process store (single swap point for SpacetimeDB)
│   ├── routers/
│   │   ├── health.py          GET  /api/health
│   │   ├── cases.py           cases, stats, comments, SSE event stream
│   │   ├── uploads.py         photo / receipt upload + file serving
│   │   ├── investigations.py  trigger + poll the CREAO agent
│   │   ├── evidence.py        list / correct / confirm / reject belongings
│   │   ├── exports.py         GET  /api/cases/{id}/export.pdf
│   │   └── demo.py            POST /api/demo/seed
│   └── services/
│       ├── creao.py       App-scoped trigger client + validator + stub
│       ├── storage.py     Upload writes, MIME sniffing, path containment
│       ├── pdf.py         ReportLab recovery-record layout
│       ├── events.py      SSE fan-out + event log
│       └── spacetime.py   SpacetimeDB reducer mirror (feature-flagged no-op)
├── scripts/make_sample_pdf.py
├── scripts/creao_preflight.py   Verify CREAO credentials without spending credits
├── tests/test_smoke.py
├── tests/test_creao_live.py     Mode separation, validation, mapping, transport gate
├── tests/test_creao_wire.py     Real HTTP against documented shapes
├── tests/fixtures/              Person 2's captured create/poll/result JSON
├── requirements.txt
└── pytest.ini
```

## Request flow

```
POST /api/cases                        → case created
POST /api/cases/{id}/uploads           → photo stored, Upload row returned (201)
POST /api/cases/{id}/investigations    → 202 immediately, agent runs in background
   ├─ GET /api/investigations/{inv}    → poll status/result
   └─ GET /api/cases/{id}/events/stream → or listen for live events (SSE)
PATCH /api/items/{item_id}             → reviewer correction, bumps revision
GET  /api/cases/{id}/export.pdf        → printable evidence-linked record
```

## CREAO integration

The investigator is a **saved, team-owned agent** reached through the
**app-scoped API Trigger** — the path Person 2 verified. The `/v1` CREAO CLI
path is gone: CREAO omits team agents from `GET /v1/agents`, so it could never
see this agent.

| `CREAO_MODE` | Behaviour |
|---|---|
| `mock` (default) | Deterministic stub, no network. `mode="mock"`, summary prefixed `[SIMULATED]`. |
| `live` | Real `POST /apps/{appId}/runs` → poll → download `rebuild-result.json`. If configuration or evidence transport is missing the API returns **503** — never fake evidence, and never a paid run without evidence. |

### Evidence reaches the agent as public HTTPS URLs

Confirmed by Person 2: `room_photos` and `receipt_files` are **arrays of public
HTTPS URL strings** that CREAO downloads itself. This backend issues **short-lived
signed links** to its own uploads:

```
GET {REBUILD_PUBLIC_BASE_URL}/api/evidence/{upload_id}/public?exp=…&sig=…
```

`REBUILD_PUBLIC_BASE_URL` must be an origin CREAO can reach (tunnel, deployed
host, or CDN in front of the API). Loopback/private hosts are rejected, and every
URL is fetched and content-type checked **before** a run is created — if any link
is unreachable the API returns `503 CREAO_EVIDENCE_URLS_UNAVAILABLE` and no run
is started.

| Value | Meaning |
|---|---|
| `unset` | refuse the run (default) |
| `url` | signed HTTPS links (real path) |
| `demo_url` | Person 2's staged GitHub demo files (fixtures/demos) |
| `none` | text only, declared as no evidence |

Links expire in `REBUILD_EVIDENCE_URL_TTL_SECONDS` (default 900 s), so private
receipts never sit at a permanent public address. Full details:
**[../docs/creao-api-integration.md](../docs/creao-api-integration.md)** §3.

### Recovering a completed run

If our download or import fails after CREAO finished, do **not** pay for another
run — import the existing one:

```bash
curl -X POST localhost:8000/api/creao/recover -H 'Content-Type: application/json' \
  -d '{"run_id":"<creao run id>","investigation_id":"<optional local inv id>"}'
```

Read-only against CREAO (GET run + artifact download), idempotent on retry,
never overwrites human corrections, and marks the investigation `complete` only
after validation passes. If the case was lost with a restart, add `case` and
`evidence` to rebuild it from the files still on disk.

### Going live

1. Get the `capi_` key from the agent detail page in `agent.creao.ai`.
2. Set `CREAO_MODE=live`, `CREAO_APP_API_KEY`, and `CREAO_EVIDENCE_TRANSPORT`.
3. Verify read-only, no credits:

```bash
python scripts/creao_preflight.py       # probes GET /apps/{appId}/runs
```

The request contract, field mapping and the evidence question are documented in
`docs/creao-api-integration.md` §1–§3.

## Persistence & real-time (SpacetimeDB)

`STORE_BACKEND=memory` (default) keeps everything in process — that is what the
test suite uses. `STORE_BACKEND=spacetime` makes SpacetimeDB authoritative:

- `spacetime_db.hydrate()` loads every table into the cache at startup
- a middleware pushes changed rows through reducers after each mutating request
- restart the API and the cases, uploads, investigations and items are still
  there (verified: `tests/test_spacetimedb_integration.py`)

The two clients in `spacetimedb/scripts/two-client-review.ts` prove live
collaboration against the real schema: reviewer A's correction appears in
reviewer B's subscription without a refresh, and a stale `revision` is rejected.

Full frontend handoff (subscriptions, corrections, conflict handling, task
claiming): **[../docs/spacetimedb.md](../docs/spacetimedb.md)**.

## Design decisions worth knowing

- **Optimistic concurrency on edits.** `PATCH /api/items/{id}` accepts
  `if_revision`. A stale value returns **409** with the current item, so two
  reviewers never silently overwrite each other. The frontend should refetch and
  replay.
- **Every correction is audited.** Each accepted patch writes a `Revision`
  (editor, changed fields, note) that appears in the PDF's correction history.
- **Uploads are MIME-sniffed, not trusted.** Declared `Content-Type` is only a
  fallback; size is enforced while streaming, and stored paths cannot escape the
  upload directory.
- **Agent output is normalised, never trusted.** `services/creao.py` coerces
  damage levels into the enum, clamps confidence to 0-1, and drops items with no
  name. Shape drift from the agent can't corrupt the record.
- **Real-time is a fallback first.** SSE works today; SpacetimeDB publication is
  behind `SPACETIMEDB_ENABLED` and is a no-op when off.

## What is stubbed / needs wiring

| Piece | Status | Owner |
|---|---|---|
| Uploads, cases, review, revisions | Working, tested | Person 3 |
| PDF export | Working (see `scripts/make_sample_pdf.py`) | Person 3 |
| CREAO agent call | **Implemented against the documented CREAO CLI API** (workspace file upload + async run + polling + artifact/result parsing). Needs `cr_sk_` key + personal `agent_id` to run live — verify with `scripts/creao_preflight.py` | Person 2 + 3 |
| SpacetimeDB mirror | Interface written; reducer name assumed — confirm with `/spacetimedb` | Person 3 |
| Persistence | In-memory; SpacetimeDB is the intended source of truth | Person 3 |
| Auth | None. `editor` / `uploaded_by` are client-supplied strings | out of scope for hackathon |

## Known limitations (fine for a 24h build)

- State lives in process memory: restarting uvicorn clears cases. Use
  `POST /api/demo/seed` to refill, or finish the SpacetimeDB mirror.
- No auth, no rate limiting, no virus scanning on uploads.
- Single worker only. With `--workers 2` the in-memory store diverges, so real
  persistence is a prerequisite for scaling out.

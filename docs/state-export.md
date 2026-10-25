# State export & restore (in-memory backends)

`STORE_BACKEND=memory` keeps every record inside the FastAPI process. A restart
erases it. This is the supported way to take a backup **from the server that is
already running** and restore it with the **original IDs**.

---

## 1. What can be exported from a running server

Export needs no new code on the server: `scripts/snapshot_state.py` rebuilds the
snapshot from read endpoints that every version already exposes.

| Data | Source endpoint | Available |
|---|---|---|
| cases | `GET /api/cases`, `GET /api/cases/{id}` | always |
| uploads (metadata + `evidence_id`) | `GET /api/cases/{id}/uploads` | always |
| items | `GET /api/cases/{id}/items` | always |
| investigations | `GET /api/cases/{id}/investigations` | always |
| comments | `GET /api/cases/{id}/comments` | always |
| revisions (human edits) | `GET /api/items/{id}/revisions` | always |
| findings / questions / decisions | `GET /api/cases/{id}/review` | v0.6.1+ |
| research runs + candidates | `GET /api/items/{id}/research`, `GET /api/research/{id}` | v0.7.0+ |

### What **cannot** be exported from the API (by design)

| Field | Why | Consequence |
|---|---|---|
| `Investigation.result_payload` | excluded from every response (large, agent-owned) | follow-up runs after a restore cannot send `previous_result` for those investigations |
| `EvidenceItem.agent_record` | excluded | Researcher `distinguishing_features` falls back to brand/model/location |
| `Upload.stored_path` | excluded (internal path) | `upload.url` still works if you copy `backend/uploads/` too |
| `ResearchRun.input_snapshot` | contains signed URLs — must not leave the server | audit-only; not needed to restore |

Everything else — IDs, statuses, timestamps, revisions, answers, resolutions,
candidate data, CREAO run associations — **is** exportable.

> A snapshot is therefore a complete logical backup of the review state, but not
> a byte-for-byte clone of process memory. The three omissions above are stated
> in the snapshot's own `warnings` array so a backup can never be mistaken for
> more than it is.

---

## 2. Export (before restarting)

```bash
cd backend
python scripts/snapshot_state.py --base-url http://127.0.0.1:8000 \
    --out rebuild-snapshot.json
```

The script probes every optional endpoint and records anything missing in
`warnings` / `unavailable_endpoints`; it never invents data.

Also copy the evidence bytes if you need them:

```bash
rsync -a backend/uploads/ ~/rebuild-uploads-backup/
```

### Verify

```bash
python scripts/snapshot_state.py --verify rebuild-snapshot.json
```

Prints counts, case/item ids, `research run → CREAO run` associations, and any
referential problems (`problems: []` means the snapshot is self-consistent).

---

## 3. Import (after restarting with the new code)

```bash
# optional: require a token for /api/admin/*
export ADMIN_TOKEN=$(openssl rand -hex 16)

curl -s -X POST localhost:8000/api/admin/import \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  --data-binary @rebuild-snapshot.json
```

Dry run first (validates without writing):

```bash
curl -s -X POST localhost:8000/api/admin/verify \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  --data-binary @rebuild-snapshot.json
```

### Guarantees

* **Original IDs preserved** — cases, items, uploads, investigations, research
  runs and candidates keep their ids; `item_ref` is never rewritten.
* **Integrity checked before writing** — a reference to an unknown case, upload,
  item or research run rejects the whole snapshot (`SNAPSHOT_INCONSISTENT`).
* **Human decisions protected** — an item with `revision > 1`, a non-`ai_suggested`
  status or confirmed fields is never overwritten. Conflicts return `409` with a
  list; use `?on_conflict=skip` to import the rest.
* **Idempotent** — importing the same snapshot twice changes nothing.
* **No CREAO calls** — import never creates a run.
* Sensitive data stays out: `stored_path`, `agent_record`, `result_payload` and
  signed URLs are never part of the document.

---

## 4. Restore shape of the running deployment

| Environment | Recommendation |
|---|---|
| Demo / hackathon | snapshot + import is enough |
| Anything that must survive a crash | set `STORE_BACKEND=spacetime`; the snapshot is then a backup, not the source of truth |

---

## 3b. Evidence photos after a restore (v0.9.1)

`Upload.stored_path` is excluded from the snapshot, so restored uploads start with
`stored_path = ""`. Before v0.9.1 that resolved to the uploads **directory**,
`FileResponse` raised `IsADirectoryError`, and the public evidence URL returned
**500** — which is why a Researcher can finish using written observations but
never inspect the photo.

v0.9.1 resolves the file by upload id on disk (`uploads/<case_id>/<upload_id>.*`),
re-attaches `stored_path` during import, and returns a clean **404
`EVIDENCE_FILE_MISSING`** when the bytes are genuinely absent.

### Did the bytes survive?

```bash
ls -l backend/uploads/*/upl_173230835194.* 2>/dev/null || echo "MISSING ON DISK"
```

- **Found** → nothing else to do; restart with v0.9.1 and the signed URL serves it.
- **Not found** → the image was never copied back. Restore it from your original
  `backend/uploads/` copy, or re-upload it as a **new** upload. Do not fabricate
  replacement evidence and do not rewrite the existing upload id.

### Verify without running the Researcher again

```bash
# 1. local route
curl -s -o /tmp/photo.bin -w "%{http_code} %{content_type} %{size_download}\n" \
  http://127.0.0.1:8000/api/uploads/upl_173230835194/file
file /tmp/photo.bin          # expect "JPEG image data" / "PNG image data"

# 2. the exact URL shape the Researcher receives (minted server-side, not printed)
python - <<'PY'
from app.services import evidence_urls
import urllib.request
url = evidence_urls.upload_url("upl_173230835194")
with urllib.request.urlopen(url) as r:
    body = r.read()
    print("status:", r.status,
          "| content-type:", r.headers.get("content-type"),
          "| bytes:", len(body),
          "| jpeg:", body[:3] == b"\xff\xd8\xff",
          "| png:", body[:8] == b"\x89PNG\r\n\x1a\n")
PY
```

No signed URL is printed — only status, type, length and a magic-byte check.

### Inspect the completed Researcher run without rerunning it

```bash
curl -s localhost:8000/api/research/rsch_484120b13d89 | jq '.run | {status, agent_run_id, error}'
curl -s localhost:8000/api/research/rsch_484120b13d89 | jq '.candidates[]? | {candidate_id, product_name, status}'
curl -s localhost:8000/api/research/rsch_484120b13d89/diagnostics | jq '.result_fields, .probes'
```

The run's `limitations` record that the photo could not be fetched — that stays
true for **that** run and is not rewritten. The fix applies to future runs.

---

## 6h. Admin diagnostic — does the signed evidence URL work?

```
GET /api/admin/evidence/{upload_id}/check
X-Admin-Token: $ADMIN_TOKEN
```

Answers one question: *if the Agent fetched this upload's signed public URL right
now, would it get the image?*

- **Requires `ADMIN_TOKEN`.** With the token unset the route returns `503
  ADMIN_TOKEN_NOT_CONFIGURED` — there is **no loopback bypass**, because the
  server is publicly exposed through ngrok.
- Accepts **only an upload id**. The caller cannot supply a URL, so there is no
  SSRF surface from user input.
- Mints a **fresh** signed URL with the running process's key, fetches it back
  through `REBUILD_PUBLIC_BASE_URL` (https required unless
  `CREAO_ALLOW_PRIVATE_EVIDENCE_HOSTS=true` for tests), follows at most two
  redirects **to the same host only**, and enforces
  `ADMIN_CHECK_TIMEOUT_SECONDS` (10 s) and `ADMIN_CHECK_MAX_BYTES` (25 MiB).
- Never returns, logs or echoes the URL, query string, signing key or headers.
- Does not modify evidence, inventory or case state, and never calls CREAO.

### Success

```json
{ "upload_id": "upl_173230835194", "http_status": 200,
  "content_type": "image/png", "byte_count": 37170,
  "valid_png_signature": true }
```

### Fetch problems (HTTP 200, diagnostic body)

The endpoint itself succeeded; the public URL did not.

| `error_code` | Meaning |
|---|---|
| `PUBLIC_URL_HTTP_ERROR` | the origin answered 4xx/5xx (status is in `http_status`) |
| `FETCH_TIMEOUT` | no answer within the configured timeout |
| `REDIRECT_NOT_ALLOWED` | it redirected to a different host |
| `REDIRECT_LIMIT` | more than two same-host redirects |
| `RESPONSE_TOO_LARGE` | body exceeded `ADMIN_CHECK_MAX_BYTES` |
| `FETCH_FAILED` | connection error (details deliberately withheld) |

### Local failures (HTTP 4xx)

| Status | `code` | Meaning |
|---|---|---|
| 401 | `ADMIN_TOKEN_INVALID` | missing or wrong token |
| 404 | `UPLOAD_NOT_FOUND` | unknown upload id |
| 404 | `EVIDENCE_FILE_MISSING` | no file on disk for this upload |
| 409 | `PUBLIC_BASE_URL_NOT_CONFIGURED` / `_INVALID` / `_INSECURE` / `_NOT_PUBLIC` | bad public base |
| 503 | `ADMIN_TOKEN_NOT_CONFIGURED` | `ADMIN_TOKEN` unset |

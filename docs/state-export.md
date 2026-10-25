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

# SpacetimeDB persistence & real-time collaboration

Owner: Person 3 (backend) · Frontend handoff: Person 1
Module: `spacetimedb/src/index.ts` (TypeScript, SpacetimeDB 2.13)
Database: `rebuild` · Local server `http://127.0.0.1:3000`

---

## 1. Architecture

```
React (Person 1) ──WebSocket subscription──▶ SpacetimeDB ◀──reducers── FastAPI
         ▲                                        │
         └──── REST for uploads / PDF / CREAO ─────┘
```

**SpacetimeDB is the durable, shared source of truth.** FastAPI keeps an
in-process cache (`app/store.py`) because all existing routers were written
against it and 45 tests depend on that shape. The cache is:

- **hydrated from the database at startup** (`spacetime_db.hydrate()`), and
- **written back after every mutating request** (`spacetime_db.sync()`, a
  middleware that pushes only rows whose hash changed).

`STORE_BACKEND=memory` is the explicit test/demo mode. `STORE_BACKEND=spacetime`
is the real one — if the database is unreachable the app **fails at startup**
rather than silently serving un-persisted data. There is no implicit fallback in
either direction.

Raw evidence bytes stay in file storage (`backend/uploads/…`); `upload` rows hold
metadata and the storage reference only.

---

## 2. Schema

| Table | Holds |
|---|---|
| `app_case` | case metadata + `members_json` |
| `upload` | evidence metadata (kind, filename, mime, size, sha256, stored_path) |
| `investigation` | CREAO run: status, `agent_run_id`, `evidence_transport`, `evidence_ids_json`, recovered flag |
| `item` | belongings: agent proposal fields **and** human review columns (`status`, `revision`, `updated_by`) |
| `revision` | every human edit: editor, note, `changes_json` |
| `finding` / `question` | agent findings and open questions |
| `comment` | case/item discussion |
| `task` | missing-evidence work items with **ownership** (`owner`, `claimed_at`) |
| `decision_event` | append-only audit trail for question answers and finding resolutions |
| `activity` | shared feed (`item.corrected`, claims, imports) |

`question` and `finding` carry both agent fields and human decision fields on the
same row. Agent fields are only rewritten on re-import when
`revision = 0` (no human decision yet), so `upsert_question` / `upsert_finding`
cannot erase an answer or resolution. `apply_question_decision` and
`apply_finding_decision` take `expected_revision`, throw `REVISION_CONFLICT` when
stale, and append a `decision_event` in the same transaction.

**Agent vs human:** an item is created with `status="ai_suggested"`; a reviewer
edit sets `human_corrected` / `human_confirmed` and bumps `revision`. The
`upsert_item` reducer **refuses to downgrade** a human-decided row back to
`ai_suggested`, so a later agent import can never erase a correction.

**Duplicate imports:** `upsert_item` also skips an insert when
`(case_id, investigation_id, agent_item_id)` already exists — enforced in the
database, so two workers or two clients cannot double-import the same CREAO
result.

**Stale edits:** `apply_review(item_id, expected_revision, patch_json, editor,
note, revision_id)` throws `REVISION_CONFLICT:<current>` when the caller's
revision is stale. FastAPI also checks in memory first and returns **409** with
the current row, which is what the REST contract already promises.

---

## 3. Frontend: subscribing (Person 1)

Generated bindings:

```bash
cd spacetimedb
npm install
spacetime generate --lang typescript --out-dir src/module_bindings -p .
```

Connect and subscribe to exactly what a screen needs:

```ts
import { DbConnection } from './module_bindings';

const conn = DbConnection.builder()
  .withUri('ws://localhost:3000')
  .withDatabaseName('rebuild')
  .onConnect((c, identity, token) => {
    localStorage.setItem('stdb_token', token);        // stable identity per browser
    c.subscriptionBuilder()
      .onApplied(() => console.log('live'))
      .subscribe([
        `SELECT * FROM app_case WHERE id = '${caseId}'`,
        `SELECT * FROM item WHERE case_id = '${caseId}'`,
        `SELECT * FROM revision WHERE case_id = '${caseId}'`,
        `SELECT * FROM task WHERE case_id = '${caseId}'`,
        `SELECT * FROM activity WHERE case_id = '${caseId}'`,
        `SELECT * FROM question WHERE case_id = '${caseId}'`,
        `SELECT * FROM finding WHERE case_id = '${caseId}'`,
        `SELECT * FROM decision_event WHERE case_id = '${caseId}'`,
      ]);
  })
  .build();

conn.db.item.onInsert((_ctx, row) => upsertIntoLocalState(row));
conn.db.item.onUpdate((_ctx, _old, row) => upsertIntoLocalState(row));
conn.db.item.onDelete((_ctx, row) => removeFromLocalState(row.id));
```

`onUpdate` is the live path: no polling, no refetch. Keep a keyed map
(`Map<id, row>`) in React state and replace on every callback — that is what
produced the verified two-client demo.

> The bearer `token` from `onConnect` is your identity. Store it and pass it back
> via `.withToken(token)` so reconnects keep the same author name/history.

---

## 4. Frontend: submitting corrections and review decisions

**Preferred — write directly to SpacetimeDB** (optimistic UI, live for others):

```ts
conn.reducers.applyReview({
  itemId: item.id,
  expectedRevision: BigInt(item.revision),   // from the row you are editing
  patchJson: JSON.stringify({ estimated_value: 425, condition: 'base is cracked' }),
  editor: currentUser,
  note: 'verified on site',
  revisionId: crypto.randomUUID(),
});
```

**Or through FastAPI** if you prefer the existing REST shape (it persists via the
same path):

```http
PATCH /api/items/{item_id}
{ "estimated_value": 425, "editor": "priya", "note": "verified on site", "if_revision": 2 }
```

Keep using REST for uploads (`POST /api/cases/{id}/uploads`), investigations and
PDF export — those are server-side concerns.

---

## 5. Handling conflicting edits

Two reviewers, one row. The row carries `revision`. On submit:

1. Send `expectedRevision` = the revision you rendered (or `if_revision` over REST).
2. **Conflict** → the reducer throws `REVISION_CONFLICT:<current>`; REST returns
   **409** with `detail.current` (the winning row).
3. UI: show "Marcus changed this" with a field-level diff (`revision.changes_json`
   has exactly what changed), then let the user re-apply on top of the new value.

Never retry blindly with a bumped revision — that silently discards the other
person's edit. Always re-read, merge, resubmit.

---

## 6. Task claiming and the activity feed

Missing-evidence work items carry an owner:

```ts
conn.reducers.claimTask({ payload: JSON.stringify({ id, case_id, question_id, title }), owner: currentUser });
conn.reducers.releaseTask({ taskId: task.id });
```

The `task` row flips to `claimed` with `owner` + `claimed_at`, and everyone
watching sees it immediately. `activity` receives `item.corrected` and claim
events, so a sidebar can render "Priya confirmed the armchair · 2m ago" purely
from the subscription — no extra endpoint.

---

## 7. Running it

```bash
# 1. server
spacetime start --listen-addr 127.0.0.1:3000 --data-dir /tmp/st-data

# 2. publish the module
cd spacetimedb && npm install
spacetime publish rebuild -s http://127.0.0.1:3000 -y -p .
spacetime generate --lang typescript --out-dir client -p .

# 3. backend against the database
cd ../backend
STORE_BACKEND=spacetime SPACETIMEDB_URL=http://127.0.0.1:3000 uvicorn app.main:app --port 8000

# 4. live collaboration proof (two WebSocket clients)
cd ../spacetimedb && npx tsx scripts/two-client-review.ts

# 5. integration tests
cd ../backend && SPACETIMEDB_URL=http://127.0.0.1:3000 pytest tests/test_spacetimedb_integration.py -v
```

---

## 8. Known limits (honest list)

- **Single FastAPI writer.** The cache is hydrated once at startup; if another
  process writes, this instance's REST reads can lag until it restarts or a
  `/sql` re-hydrate is triggered. Multi-instance deployment needs either
  subscription-driven cache invalidation or direct DB reads per request.
- **`investigation.result_payload` is not persisted** (it can be megabytes and is
  only needed for follow-up `previous_result` links). Recovery re-reads it from
  CREAO instead.
- **No auth on the module.** `editor`/`author` are client-supplied strings; the
  SpacetimeDB identity is available but not enforced.
- **Bindings are generated, not committed** (`spacetimedb/client/` is gitignored).
  Run `spacetime generate` after schema changes.

import { schema, table, t } from 'spacetimedb/server';

/**
 * Rebuild shared state.
 *
 * Design rules that matter to the backend:
 *  - FastAPI assigns all ids (uuid-based) and passes them in, because reducers
 *    cannot return values.
 *  - Every upsert is idempotent by primary key, so a write-behind sync can
 *    replay safely.
 *  - `import_item` is additionally guarded on (case_id, investigation_id,
 *    agent_item_id): re-processing the same CREAO result can never duplicate a
 *    belonging, even from two clients at once.
 *  - Human decisions live in their own columns/table and are never overwritten
 *    by an agent import path (`status`/`revision`/`updated_by`).
 *  - Raw evidence bytes stay in file storage; `upload` holds metadata only.
 *  - Mutating reducers bump `revision` and reject stale writes, which is how two
 *    reviewers avoid clobbering each other.
 */

const spacetimedb = schema({
  app_case: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      title: t.string(),
      address: t.string(),
      disaster_type: t.string(),
      status: t.string(),
      notes: t.string(),
      created_by: t.string(),
      upload_count: t.u64(),
      item_count: t.u64(),
      members_json: t.string(),
      created_at: t.string(),
      updated_at: t.string(),
    }
  ),

  upload: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      case_id: t.string(),
      kind: t.string(),
      original_filename: t.string(),
      content_type: t.string(),
      size_bytes: t.u64(),
      sha256: t.string(),
      uploaded_by: t.string(),
      url: t.string(),
      stored_path: t.string(),
      created_at: t.string(),
    }
  ),

  investigation: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      case_id: t.string(),
      upload_id: t.string(),
      status: t.string(),
      mode: t.string(),
      agent_run_id: t.string(),
      conversation_id: t.string(),
      model: t.string(),
      credits_used: t.f64(),
      evidence_transport: t.string(),
      evidence_ids_json: t.string(),
      summary: t.string(),
      error: t.string(),
      recovered: t.bool(),
      item_ids_json: t.string(),
      requested_by: t.string(),
      created_at: t.string(),
      finished_at: t.string(),
    }
  ),

  item: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      case_id: t.string(),
      investigation_id: t.string(),
      upload_id: t.string(),
      agent_item_id: t.string(),
      room: t.string(),
      name: t.string(),
      category: t.string(),
      description: t.string(),
      condition: t.string(),
      damage_level: t.string(),
      confidence: t.f64(),
      estimated_value: t.f64(),
      currency: t.string(),
      bbox_json: t.string(),
      agent_reasoning: t.string(),
      review_status: t.string(),
      status: t.string(),
      supporting_evidence_ids_json: t.string(),
      agent_record_json: t.string(),
      revision: t.u64(),
      created_at: t.string(),
      updated_at: t.string(),
      updated_by: t.string(),
    }
  ),

  revision: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      case_id: t.string(),
      item_id: t.string(),
      editor: t.string(),
      note: t.string(),
      changes_json: t.string(),
      created_at: t.string(),
    }
  ),

  finding: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      case_id: t.string(),
      investigation_id: t.string(),
      finding_id: t.string(),
      finding_type: t.string(),
      item_ids_json: t.string(),
      evidence_ids_json: t.string(),
      explanation: t.string(),
      status: t.string(),
      resolution_reason: t.string(),
    }
  ),

  question: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      case_id: t.string(),
      investigation_id: t.string(),
      question_id: t.string(),
      item_id: t.string(),
      question: t.string(),
      reason: t.string(),
      status: t.string(),
      resolution_reason: t.string(),
    }
  ),

  comment: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      case_id: t.string(),
      item_id: t.string(),
      author: t.string(),
      body: t.string(),
      created_at: t.string(),
    }
  ),

  // Missing-evidence work items with explicit ownership ("who is chasing this").
  task: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      case_id: t.string(),
      question_id: t.string(),
      title: t.string(),
      status: t.string(),
      owner: t.string(),
      claimed_at: t.string(),
      created_at: t.string(),
    }
  ),

  // Shared activity feed for the collaboration sidebar.
  activity: table(
    { public: true },
    {
      id: t.string().primaryKey(),
      case_id: t.string(),
      activity_type: t.string(),
      actor: t.string(),
      payload_json: t.string(),
      created_at: t.string(),
    }
  ),
});

export default spacetimedb;

export const init = spacetimedb.init(_ctx => {});

const now = () => new Date().toISOString();

// --------------------------------------------------------------------------
// Cases
// --------------------------------------------------------------------------
export const upsert_case = spacetimedb.reducer({ payload: t.string() }, (ctx, { payload }) => {
  const p = JSON.parse(payload);
  const row = {
    id: p.id,
    title: p.title ?? '',
    address: p.address ?? '',
    disaster_type: p.disaster_type ?? 'flood',
    status: p.status ?? 'open',
    notes: p.notes ?? '',
    created_by: p.created_by ?? '',
    upload_count: BigInt(p.upload_count ?? 0),
    item_count: BigInt(p.item_count ?? 0),
    members_json: JSON.stringify(p.members ?? []),
    created_at: p.created_at ?? now(),
    updated_at: p.updated_at ?? now(),
  };
  if (ctx.db.app_case.id.find(row.id)) ctx.db.app_case.id.update(row);
  else ctx.db.app_case.insert(row);
});

// --------------------------------------------------------------------------
// Uploads (metadata only — bytes live in file storage)
// --------------------------------------------------------------------------
export const upsert_upload = spacetimedb.reducer({ payload: t.string() }, (ctx, { payload }) => {
  const p = JSON.parse(payload);
  const row = {
    id: p.id,
    case_id: p.case_id,
    kind: p.kind ?? 'photo',
    original_filename: p.original_filename ?? '',
    content_type: p.content_type ?? '',
    size_bytes: BigInt(p.size_bytes ?? 0),
    sha256: p.sha256 ?? '',
    uploaded_by: p.uploaded_by ?? '',
    url: p.url ?? '',
    stored_path: p.stored_path ?? '',
    created_at: p.created_at ?? now(),
  };
  if (ctx.db.upload.id.find(row.id)) ctx.db.upload.id.update(row);
  else ctx.db.upload.insert(row);
});

// --------------------------------------------------------------------------
// Investigations
// --------------------------------------------------------------------------
export const upsert_investigation = spacetimedb.reducer(
  { payload: t.string() },
  (ctx, { payload }) => {
    const p = JSON.parse(payload);
    const row = {
      id: p.id,
      case_id: p.case_id,
      upload_id: p.upload_id ?? '',
      status: p.status ?? 'queued',
      mode: p.mode ?? 'live',
      agent_run_id: p.agent_run_id ?? '',
      conversation_id: p.conversation_id ?? '',
      model: p.model ?? '',
      credits_used: p.credits_used ?? 0,
      evidence_transport: p.evidence_transport ?? '',
      evidence_ids_json: JSON.stringify(p.evidence_ids ?? []),
      summary: p.summary ?? '',
      error: p.error ?? '',
      recovered: !!p.recovered,
      item_ids_json: JSON.stringify(p.item_ids ?? []),
      requested_by: p.requested_by ?? '',
      created_at: p.created_at ?? now(),
      finished_at: p.finished_at ?? '',
    };
    if (ctx.db.investigation.id.find(row.id)) ctx.db.investigation.id.update(row);
    else ctx.db.investigation.insert(row);
  }
);

// --------------------------------------------------------------------------
// Items — agent proposal vs human-confirmed facts
// --------------------------------------------------------------------------
export const upsert_item = spacetimedb.reducer({ payload: t.string() }, (ctx, { payload }) => {
  const p = JSON.parse(payload);
  const row = {
    id: p.id,
    case_id: p.case_id,
    investigation_id: p.investigation_id ?? '',
    upload_id: p.upload_id ?? '',
    agent_item_id: p.agent_item_id ?? '',
    room: p.room ?? '',
    name: p.name,
    category: p.category ?? 'other',
    description: p.description ?? '',
    condition: p.condition ?? '',
    damage_level: p.damage_level ?? 'unknown',
    confidence: p.confidence ?? 0,
    estimated_value: p.estimated_value ?? 0,
    currency: p.currency ?? 'USD',
    bbox_json: JSON.stringify(p.bbox ?? null),
    agent_reasoning: p.agent_reasoning ?? '',
    review_status: p.review_status ?? 'needs_review',
    status: p.status ?? 'ai_suggested',
    supporting_evidence_ids_json: JSON.stringify(p.supporting_evidence_ids ?? []),
    agent_record_json: JSON.stringify(p.agent_record ?? null),
    revision: BigInt(p.revision ?? 1),
    created_at: p.created_at ?? now(),
    updated_at: p.updated_at ?? now(),
    updated_by: p.updated_by ?? '',
  };

  const existing = ctx.db.item.id.find(row.id);
  if (existing) {
    // A human decision is never undone by an agent re-import.
    if (existing.status !== 'ai_suggested' && row.status === 'ai_suggested') {
      return;
    }
    ctx.db.item.id.update(row);
    return;
  }

  // Duplicate-import guard for the same CREAO result, even across clients.
  if (row.agent_item_id) {
    for (const other of ctx.db.item.iter()) {
      if (
        other.case_id === row.case_id &&
        other.investigation_id === row.investigation_id &&
        other.agent_item_id === row.agent_item_id
      ) {
        return;
      }
    }
  }
  ctx.db.item.insert(row);
});

/**
 * Human review edit with optimistic concurrency.
 * Throws when `expected_revision` does not match, which the API surfaces as 409.
 */
export const apply_review = spacetimedb.reducer(
  {
    item_id: t.string(),
    expected_revision: t.u64(),
    patch_json: t.string(),
    editor: t.string(),
    note: t.string(),
    revision_id: t.string(),
  },
  (ctx, { item_id, expected_revision, patch_json, editor, note, revision_id }) => {
    const existing = ctx.db.item.id.find(item_id);
    if (!existing) throw new Error('ITEM_NOT_FOUND');
    if (existing.revision !== expected_revision) {
      throw new Error(`REVISION_CONFLICT:${existing.revision}`);
    }

    const patch = JSON.parse(patch_json);
    const changed: Record<string, unknown> = {};
    const next: any = { ...existing, revision: existing.revision + 1n, updated_at: now(), updated_by: editor };

    for (const key of Object.keys(patch)) {
      if (key in existing) {
        next[key] = patch[key];
        changed[key] = patch[key];
      }
    }
    if (next.status === 'ai_suggested' && !patch.status) next.status = 'human_corrected';

    ctx.db.item.id.update(next);
    ctx.db.revision.insert({
      id: revision_id,
      case_id: existing.case_id,
      item_id,
      editor,
      note: note ?? '',
      changes_json: JSON.stringify(changed),
      created_at: now(),
    });
    ctx.db.activity.insert({
      id: `${revision_id}-act`,
      case_id: existing.case_id,
      activity_type: 'item.corrected',
      actor: editor,
      payload_json: JSON.stringify({ item_id, changed }),
      created_at: now(),
    });
  }
);

export const upsert_revision = spacetimedb.reducer({ payload: t.string() }, (ctx, { payload }) => {
  const p = JSON.parse(payload);
  const row = {
    id: p.id,
    case_id: p.case_id ?? '',
    item_id: p.item_id,
    editor: p.editor ?? '',
    note: p.note ?? '',
    changes_json: JSON.stringify(p.changes ?? {}),
    created_at: p.created_at ?? now(),
  };
  if (ctx.db.revision.id.find(row.id)) ctx.db.revision.id.update(row);
  else ctx.db.revision.insert(row);
});

// --------------------------------------------------------------------------
// Findings, questions, comments
// --------------------------------------------------------------------------
export const upsert_finding = spacetimedb.reducer({ payload: t.string() }, (ctx, { payload }) => {
  const p = JSON.parse(payload);
  const row = {
    id: p.id,
    case_id: p.case_id,
    investigation_id: p.investigation_id ?? '',
    finding_id: p.finding_id ?? '',
    finding_type: p.finding_type ?? '',
    item_ids_json: JSON.stringify(p.item_ids ?? []),
    evidence_ids_json: JSON.stringify(p.evidence_ids ?? []),
    explanation: p.explanation ?? '',
    status: p.status ?? 'needs_review',
    resolution_reason: p.resolution_reason ?? '',
  };
  if (ctx.db.finding.id.find(row.id)) ctx.db.finding.id.update(row);
  else ctx.db.finding.insert(row);
});

export const upsert_question = spacetimedb.reducer({ payload: t.string() }, (ctx, { payload }) => {
  const p = JSON.parse(payload);
  const row = {
    id: p.id,
    case_id: p.case_id,
    investigation_id: p.investigation_id ?? '',
    question_id: p.question_id ?? '',
    item_id: p.item_id ?? '',
    question: p.question ?? '',
    reason: p.reason ?? '',
    status: p.status ?? 'open',
    resolution_reason: p.resolution_reason ?? '',
  };
  if (ctx.db.question.id.find(row.id)) ctx.db.question.id.update(row);
  else ctx.db.question.insert(row);
});

export const upsert_comment = spacetimedb.reducer({ payload: t.string() }, (ctx, { payload }) => {
  const p = JSON.parse(payload);
  const row = {
    id: p.id,
    case_id: p.case_id,
    item_id: p.item_id ?? '',
    author: p.author ?? '',
    body: p.body ?? '',
    created_at: p.created_at ?? now(),
  };
  if (ctx.db.comment.id.find(row.id)) ctx.db.comment.id.update(row);
  else ctx.db.comment.insert(row);
});

// --------------------------------------------------------------------------
// Task ownership (missing evidence someone is chasing)
// --------------------------------------------------------------------------
export const claim_task = spacetimedb.reducer(
  { payload: t.string(), owner: t.string() },
  (ctx, { payload, owner }) => {
    const p = JSON.parse(payload);
    const existing = p.id ? ctx.db.task.id.find(p.id) : undefined;
    if (existing) {
      ctx.db.task.id.update({ ...existing, status: 'claimed', owner, claimed_at: now() });
      return;
    }
    ctx.db.task.insert({
      id: p.id,
      case_id: p.case_id,
      question_id: p.question_id ?? '',
      title: p.title ?? '',
      status: 'claimed',
      owner,
      claimed_at: now(),
      created_at: now(),
    });
  }
);

export const release_task = spacetimedb.reducer({ task_id: t.string() }, (ctx, { task_id }) => {
  const existing = ctx.db.task.id.find(task_id);
  if (!existing) throw new Error('TASK_NOT_FOUND');
  ctx.db.task.id.update({ ...existing, status: 'open', owner: '', claimed_at: '' });
});

// --------------------------------------------------------------------------
// Activity feed
// --------------------------------------------------------------------------
export const log_activity = spacetimedb.reducer({ payload: t.string() }, (ctx, { payload }) => {
  const p = JSON.parse(payload);
  const row = {
    id: p.id,
    case_id: p.case_id,
    activity_type: p.activity_type ?? '',
    actor: p.actor ?? '',
    payload_json: JSON.stringify(p.payload ?? {}),
    created_at: p.created_at ?? now(),
  };
  if (ctx.db.activity.id.find(row.id)) ctx.db.activity.id.update(row);
  else ctx.db.activity.insert(row);
});

// --------------------------------------------------------------------------
// Reset for tests (local only)
// --------------------------------------------------------------------------
export const clear_case = spacetimedb.reducer({ case_id: t.string() }, (ctx, { case_id }) => {
  for (const row of [...ctx.db.item.iter()]) if (row.case_id === case_id) ctx.db.item.id.delete(row.id);
  for (const row of [...ctx.db.revision.iter()]) if (row.case_id === case_id) ctx.db.revision.id.delete(row.id);
  for (const row of [...ctx.db.finding.iter()]) if (row.case_id === case_id) ctx.db.finding.id.delete(row.id);
  for (const row of [...ctx.db.question.iter()]) if (row.case_id === case_id) ctx.db.question.id.delete(row.id);
  for (const row of [...ctx.db.comment.iter()]) if (row.case_id === case_id) ctx.db.comment.id.delete(row.id);
  for (const row of [...ctx.db.task.iter()]) if (row.case_id === case_id) ctx.db.task.id.delete(row.id);
  for (const row of [...ctx.db.activity.iter()]) if (row.case_id === case_id) ctx.db.activity.id.delete(row.id);
  for (const row of [...ctx.db.investigation.iter()]) if (row.case_id === case_id) ctx.db.investigation.id.delete(row.id);
  for (const row of [...ctx.db.upload.iter()]) if (row.case_id === case_id) ctx.db.upload.id.delete(row.id);
  const c = ctx.db.app_case.id.find(case_id);
  if (c) ctx.db.app_case.id.delete(case_id);
});

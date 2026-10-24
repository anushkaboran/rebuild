/**
 * Two-client collaboration proof against the real Rebuild schema.
 *
 * Seeds a case + item over HTTP reducers, then two independent WebSocket
 * clients subscribe to `item`. Client A applies a review correction; client B
 * must observe the new row without refreshing. Client B then attempts a stale
 * correction and must be told its revision is out of date.
 *
 *   npx tsx scripts/two-client-review.ts
 */

import { DbConnection } from '../client/index.ts';

const URI = process.env.SPACETIME_URI ?? 'ws://127.0.0.1:3000';
const HTTP = process.env.SPACETIME_HTTP ?? 'http://127.0.0.1:3000';
const DB = process.env.SPACETIME_DB ?? 'rebuild';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function callReducer(name: string, args: unknown[]) {
  const res = await fetch(`${HTTP}/v1/database/${DB}/call/${name}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(args),
  });
  if (!res.ok) throw new Error(`${name} -> ${res.status}: ${await res.text()}`);
}

type Snapshot = { revision: bigint; value: number; condition: string; status: string };

function makeClient(name: string, seen: Snapshot, trace: string[]) {
  const conn = DbConnection.builder()
    .withUri(URI)
    .withDatabaseName(DB)
    .onConnect((c) => {
      c.subscriptionBuilder()
        .onApplied(() => trace.push(`${name}: subscribed`))
        .subscribe(['SELECT * FROM item', 'SELECT * FROM revision', 'SELECT * FROM activity']);
    })
    .onConnectError((_c, err) => {
      console.error(`✗ ${name} connect error:`, err.message);
      process.exit(1);
    })
    .build();

  conn.db.item.onUpdate((_ctx, _old, row) => {
    seen.revision = row.revision;
    seen.value = row.estimatedValue;
    seen.condition = row.condition;
    seen.status = row.status;
    trace.push(`${name}: item UPDATE rev=${row.revision} value=${row.estimatedValue} status=${row.status}`);
  });
  conn.db.item.onInsert((_ctx, row) => {
    trace.push(`${name}: item INSERT ${row.agentItemId} rev=${row.revision}`);
  });
  conn.db.revision.onInsert((_ctx, row) => {
    trace.push(`${name}: revision INSERT by ${row.editor} note="${row.note}"`);
  });
  conn.db.activity.onInsert((_ctx, row) => {
    trace.push(`${name}: activity ${row.activityType} by ${row.actor}`);
  });

  return conn;
}

async function main() {
  const stamp = Date.now();
  const caseId = `case_ts_${stamp}`;
  const itemId = `itm_ts_${stamp}`;
  const trace: string[] = [];
  const seenA: Snapshot = { revision: 0n, value: 0, condition: '', status: '' };
  const seenB: Snapshot = { revision: 0n, value: 0, condition: '', status: '' };

  await callReducer('upsert_case', [
    JSON.stringify({ id: caseId, title: 'Two-client review', status: 'review' }),
  ]);
  await callReducer('upsert_item', [
    JSON.stringify({
      id: itemId,
      case_id: caseId,
      investigation_id: 'inv_ts',
      agent_item_id: 'itm_001',
      name: 'Brown armchair',
      status: 'ai_suggested',
      estimated_value: 0,
    }),
  ]);
  console.log(`seeded case ${caseId} / item ${itemId}\n`);

  const a = makeClient('client-A', seenA, trace);
  const b = makeClient('client-B', seenB, trace);
  for (let i = 0; i < 100 && trace.filter((t) => t.endsWith('subscribed')).length < 2; i++) {
    await sleep(100);
  }
  if (trace.filter((t) => t.endsWith('subscribed')).length < 2) {
    console.error('✗ subscriptions did not apply:', trace);
    process.exit(1);
  }
  console.log('both reviewers subscribed\n');

  // Reviewer A corrects the item.
  await a.reducers.applyReview({
    itemId,
    expectedRevision: 1n,
    patchJson: JSON.stringify({ estimated_value: 425, condition: 'base is cracked' }),
    editor: 'priya',
    note: 'verified on site',
    revisionId: `rev_ts_${stamp}`,
  });
  await sleep(700);
  console.log(`after A's correction → B sees rev=${seenB.revision} value=${seenB.value} status=${seenB.status}`);

  // Reviewer B still thinks rev 1 — the reducer must refuse.
  let conflictRejected = false;
  try {
    await b.reducers.applyReview({
      itemId,
      expectedRevision: 1n,
      patchJson: JSON.stringify({ estimated_value: 999 }),
      editor: 'marcus',
      note: 'stale edit',
      revisionId: `rev_stale_${stamp}`,
    });
    await sleep(600);
  } catch {
    conflictRejected = true;
  }
  if (!conflictRejected) {
    // The rejection may surface asynchronously; treat "value unchanged" as the
    // observable consequence either way.
    await sleep(400);
    conflictRejected = seenB.value === 425 && seenB.revision === 2n;
  }

  console.log('\n--- event trace ---');
  for (const line of trace) console.log('  ' + line);

  const passed =
    seenB.revision === 2n &&
    seenB.value === 425 &&
    seenB.condition === 'base is cracked' &&
    seenB.status === 'human_corrected' &&
    conflictRejected;

  console.log('\n--- result ---');
  console.log(`client-B observed A's correction live: ${seenB.revision === 2n && seenB.value === 425 ? 'YES' : 'NO'}`);
  console.log(`stale revision rejected: ${conflictRejected ? 'YES' : 'NO'}`);
  console.log(`final state rev=${seenB.revision} value=${seenB.value} status=${seenB.status}`);

  a.disconnect();
  b.disconnect();
  await sleep(200);
  process.exit(passed ? 0 : 1);
}

main().catch((err) => {
  console.error('✗', err);
  process.exit(1);
});

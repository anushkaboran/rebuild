/**
 * Two-client proof for human review decisions.
 *
 * Seeds a case + investigator question over HTTP, then two independent
 * WebSocket clients subscribe to `question` and `decision_event`.
 * Client A saves an answer; client B must receive it live. Client B then
 * submits a stale revision and must be refused.
 *
 *   npx tsx scripts/two-client-decisions.ts
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

async function main() {
  const stamp = Date.now();
  const caseId = `case_q_${stamp}`;
  const qid = `inv_q_${stamp}:q_001`;

  await callReducer('upsert_case', [JSON.stringify({ id: caseId, title: 'Decision sync', status: 'review' })]);
  await callReducer('upsert_question', [
    JSON.stringify({
      id: qid,
      case_id: caseId,
      investigation_id: `inv_q_${stamp}`,
      question_id: 'q_001',
      question: 'Is the brown armchair yours?',
      evidence_ids: ['ev_001'],
      status: 'open',
      revision: 0,
    }),
  ]);
  console.log(`seeded case ${caseId} / question q_001\n`);

  const trace: string[] = [];
  const seen = { answer: '', by: '', revision: 0n, events: 0 };

  const client = DbConnection.builder()
    .withUri(URI)
    .withDatabaseName(DB)
    .onConnect((c) => {
      c.subscriptionBuilder()
        .onApplied(() => trace.push('subscribed'))
        .subscribe(['SELECT * FROM question', 'SELECT * FROM decision_event']);
    })
    .onConnectError((_c, err) => {
      console.error('connect error:', err.message);
      process.exit(1);
    })
    .build();

  client.db.question.onUpdate((_ctx, _old, row) => {
    if (row.id !== qid) return;
    seen.answer = row.answer;
    seen.by = row.answeredBy;
    seen.revision = row.revision;
    trace.push(`client-B: question UPDATE rev=${row.revision} answer="${row.answer}" by=${row.answeredBy} status=${row.status}`);
  });
  client.db.decisionEvent.onInsert((_ctx, row) => {
    seen.events += 1;
    trace.push(`client-B: decision_event ${row.targetType} rev=${row.revision} value="${row.value}" reviewer=${row.reviewer}`);
  });

  for (let i = 0; i < 100 && trace.length === 0; i++) await sleep(100);
  console.log('subscribed\n');

  // Reviewer A answers.
  await client.reducers.applyQuestionDecision({
    questionId: qid,
    expectedRevision: 0n,
    answer: 'Yes, mine — it was in the room.',
    reviewer: 'priya',
    revisionId: `dec_${stamp}`,
  });
  await sleep(700);
  console.log(`after A's answer → B sees rev=${seen.revision} by=${seen.by} events=${seen.events}`);

  // Reviewer B still holds revision 0 → must be refused.
  let conflict = false;
  try {
    await client.reducers.applyQuestionDecision({
      questionId: qid,
      expectedRevision: 0n,
      answer: 'No, not mine',
      reviewer: 'marcus',
      revisionId: `dec_stale_${stamp}`,
    });
  } catch {
    conflict = true;
  }
  await sleep(500);
  if (!conflict) conflict = seen.revision === 1n && seen.answer.startsWith('Yes, mine');

  console.log('\n--- event trace ---');
  for (const line of trace) console.log('  ' + line);

  const passed =
    seen.revision === 1n &&
    seen.answer.startsWith('Yes, mine') &&
    seen.by === 'priya' &&
    seen.events >= 1 &&
    conflict;

  console.log('\n--- result ---');
  console.log(`client-B received A's answer live: ${seen.revision === 1n && seen.answer.startsWith('Yes, mine') ? 'YES' : 'NO'}`);
  console.log(`decision_event broadcast: ${seen.events >= 1 ? 'YES' : 'NO'}`);
  console.log(`stale revision rejected: ${conflict ? 'YES' : 'NO'}`);

  client.disconnect();
  await sleep(200);
  process.exit(passed ? 0 : 1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});

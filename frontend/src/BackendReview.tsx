import { useRef, useState } from 'react'
import type { Finding, Question } from './ReviewFindings'

type DecisionRecord = {
  ref: string
  title: string
  context: string
  revision?: number
  answer?: string | null
  resolution?: string | null
  resolution_reason?: string | null
}

type SavedQuestion = Question & DecisionRecord
type SavedFinding = Finding & DecisionRecord

function DecisionCard({ record, caseId, kind, onSaved }: {
  record: DecisionRecord
  caseId: string
  kind: 'questions' | 'findings'
  onSaved: () => Promise<void>
}) {
  const [text, setText] = useState(record.answer ?? record.resolution_reason ?? '')
  const [reviewer, setReviewer] = useState('Lasya')
  const [choice, setChoice] = useState(record.resolution ?? 'needs_more_evidence')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState(false)
  const busy = useRef(false)

  async function save(clear = false) {
    if (busy.current || conflict) return
    setError('')
    if (record.revision == null) {
      setError('Refresh the inventory to load revision information.')
      return
    }
    if (!reviewer.trim() || (!clear && !text.trim())) {
      setError('Enter your reviewer name and an answer or reason.')
      return
    }
    busy.current = true
    setSaving(true)
    try {
      const response = await fetch(
        `/api/cases/${encodeURIComponent(caseId)}/${kind}/${encodeURIComponent(record.ref)}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            reviewer: reviewer.trim(),
            if_revision: record.revision,
            ...(kind === 'questions'
              ? { answer: clear ? null : text.trim() }
              : { resolution: choice, reason: text.trim() }),
          }),
        },
      )
      const body = await response.json()
      if (response.status === 409) {
        setConflict(true)
        const current = body.detail?.current
        throw new Error(
          'Another reviewer saved first. Currently saved: ' +
          (current?.answer ?? current?.resolution_reason ?? 'No answer or reason') +
          '. Copy your draft before refreshing.',
        )
      }
      if (!response.ok) {
        throw new Error(body.detail?.message ?? `Save failed (${response.status}).`)
      }
      await onSaved()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save.')
    } finally {
      busy.current = false
      setSaving(false)
    }
  }

  return (
    <article className="question">
      <h4>{record.title}</h4>
      <details><summary>Context</summary><p>{record.context}</p></details>
      {(record.answer || record.resolution_reason) && (
        <p className="notice">
          Saved: {record.answer ?? record.resolution_reason}
        </p>
      )}
      <form onSubmit={event => { event.preventDefault(); void save() }}>
        <fieldset disabled={saving || conflict}>
          {kind === 'findings' && <label>
            Decision
            <select value={choice} onChange={event => setChoice(event.target.value)}>
              <option value="confirmed">Confirm finding</option>
              <option value="rejected">Reject finding</option>
              <option value="needs_more_evidence">Needs more evidence</option>
              <option value="unresolved">Leave unresolved</option>
            </select>
          </label>}
          <label>
            {kind === 'questions' ? 'Your answer' : 'Reason'}
            <textarea value={text} maxLength={1000} required
              onChange={event => setText(event.target.value)} />
          </label>
          <label>
            Reviewer name
            <input value={reviewer} maxLength={120} required
              onChange={event => setReviewer(event.target.value)} />
          </label>
          <button className="button-primary" type="submit">
            {saving ? 'Saving…' : kind === 'questions' ? 'Save answer' : 'Save decision'}
          </button>
          {kind === 'questions' && record.answer && (
            <button className="button-quiet" type="button" onClick={() => void save(true)}>
              Clear answer
            </button>
          )}
        </fieldset>
      </form>
      {error && <p role="alert">{error}</p>}
      {conflict && <button className="button-secondary" type="button"
        onClick={() => void onSaved()}>Refresh saved decisions</button>}
    </article>
  )
}

export default function BackendReview({ caseId, itemId, investigationId,
  questions, findings, onSaved }: {
  caseId: string
  itemId: string
  investigationId?: string | null
  questions: Question[]
  findings: Finding[]
  onSaved: () => Promise<void>
}) {
  const [tab, setTab] = useState<'questions' | 'findings'>('questions')
  const [includeCase, setIncludeCase] = useState(false)
  const [showAnswered, setShowAnswered] = useState(false)
  const records: DecisionRecord[] = tab === 'questions'
    ? (questions as SavedQuestion[]).filter(q =>
        (showAnswered || !q.answer?.trim()) &&
        (!investigationId || q.question_id.startsWith(investigationId + ':')) &&
        (q.item_id === itemId || (includeCase && !q.item_id)),
      ).map(q => ({ ...q, ref: q.question_id, title: q.question, context: q.reason }))
    : (findings as SavedFinding[]).filter(f =>
        (!investigationId || f.finding_id.startsWith(investigationId + ':')) &&
        (f.item_ids.includes(itemId) || (includeCase && !f.item_ids.length)),
      ).map(f => ({ ...f, ref: f.finding_id,
        title: f.type.replaceAll('_', ' '), context: f.explanation }))

  return (
    <section className="review-findings backend-review">
      <h4>A little more clarity</h4>
      <div className="review-navigation">
        <button type="button" aria-pressed={tab === 'questions'}
          onClick={() => setTab('questions')}>Questions</button>
        <button type="button" aria-pressed={tab === 'findings'}
          onClick={() => setTab('findings')}>Findings</button>
      </div>
      <div className="review-filters"><label>
        <input type="checkbox" checked={includeCase}
          onChange={event => setIncludeCase(event.target.checked)} />
        Include case-wide issues
      </label></div>
      {tab === 'questions' && <div className="review-filters"><label>
        <input type="checkbox" checked={showAnswered}
          onChange={event => setShowAnswered(event.target.checked)} />
        Show answered questions
      </label></div>}
      {records.map(record => <DecisionCard
        key={record.ref + ':' + record.revision}
        record={record} caseId={caseId} kind={tab} onSaved={onSaved} />)}
      {!records.length && <p role="status">{tab === 'questions' && !showAnswered
        ? 'No unanswered questions in this view.' : 'No records in this view.'}</p>}
      <p className="helper">
        Answers and decisions are saved separately from item confirmations.
        Saving does not start another CREAO run.
      </p>
    </section>
  )
}

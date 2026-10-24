import { useState } from 'react'
import ResolveFinding from './ResolveFinding'

export type Finding = {
  finding_id: string
  type: string
  item_ids: string[]
  evidence_ids: string[]
  explanation: string
  status: string
  resolution_reason: string | null
}

export type Question = {
  question_id: string
  item_id: string | null
  question: string
  reason: string
  status: string
  resolution_reason: string | null
}

type Props = {
  itemId: string
  findings: Finding[]
  questions: Question[]
  resolutions: Record<string, string>
  onResolve: (id: string, reason: string) => void
  onUndo: (id: string) => void
}

export default function ReviewFindings({ itemId, findings, questions, resolutions, onResolve, onUndo }: Props) {
  const [reviewView, setReviewView] = useState<'questions' | 'findings'>('questions')
  const [showCase, setShowCase] = useState(false)
  const [showResolved, setShowResolved] = useState(false)
  const relevant = findings.filter(finding =>
    finding.item_ids.includes(itemId) || (showCase && finding.item_ids.length === 0)
  )
  const openQuestions = questions.filter(question =>
    question.status === 'open' &&
    (question.item_id === itemId || (showCase && question.item_id === null))
  )

  return (
    <section className="review-findings">
      <div className="review-navigation">
        <button type="button" aria-pressed={reviewView === 'questions'}
          onClick={() => setReviewView('questions')}>
          Questions ({openQuestions.length})
        </button>
        <button type="button" aria-pressed={reviewView === 'findings'}
          onClick={() => setReviewView('findings')}>
          Findings
        </button>
      </div>
      <div className="review-filters">
        <label><input type="checkbox" checked={showCase}
          onChange={event => setShowCase(event.target.checked)} />
          Include case-wide issues
        </label>
        <label><input type="checkbox" checked={showResolved}
          onChange={event => setShowResolved(event.target.checked)} />
          Show resolved
        </label>
      </div>
      <p className="helper">Open an issue for its explanation and review controls.</p>

      <div hidden={reviewView !== 'findings'}>
      {relevant.filter(finding => showResolved ||
        (finding.status !== 'resolved' && !resolutions[finding.finding_id])
      ).map(finding => (
        <details key={`${itemId}-${finding.finding_id}`} className="finding">
          <summary>
            {(finding.status === 'resolved' || resolutions[finding.finding_id]) ? 'Resolved' : 'Needs review'}
            {' · '}{finding.type.replaceAll('_', ' ')}
            <span className="finding-preview">
              {finding.explanation.length > 100
                ? finding.explanation.slice(0, 100) + '…'
                : finding.explanation}
            </span>
            
          </summary>
          <p>{finding.explanation}</p>
          <small>Evidence: {finding.evidence_ids.join(', ') || 'None'}</small>
          {resolutions[finding.finding_id] && (
            <div className="notice">
              <p>Local review decision: {resolutions[finding.finding_id]}</p>
              <button type="button" className="remove-file" onClick={() => onUndo(finding.finding_id)}>
                Undo resolution
              </button>
            </div>
          )}
          {finding.status !== 'resolved' && !resolutions[finding.finding_id] && (
            <ResolveFinding
              key={itemId + finding.finding_id}
              onResolve={reason => onResolve(finding.finding_id, reason)}
            />
          )}
          {finding.resolution_reason && (
            <p className="notice">{finding.resolution_reason}</p>
          )}
        </details>
      ))}

      </div>
      <div hidden={reviewView !== 'questions'}>
      <h4>Open questions ({openQuestions.length})</h4>
      {openQuestions.length === 0 && <p>No open questions for this item or case.</p>}
      {openQuestions.map(question => (
        <div key={question.question_id} className="question">
          <strong>{question.question}</strong>
          <details>
            <summary>Why we're asking</summary>
            <p>{question.reason}</p>
          </details>
          <small>{question.item_id === null ? 'Case question' : 'Item question'}</small>
        </div>
      ))}
      </div>
    </section>
  )
}

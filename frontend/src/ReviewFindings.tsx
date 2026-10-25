import { useState } from 'react'
import ResolveFinding from './ResolveFinding'
import './Review.css'

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
  answer?: string | null
  question_id: string
  item_id: string | null
  question: string
  reason: string
  status: string
  resolution_reason: string | null
}

type Props = {
  readOnly?: boolean
  itemId: string
  findings: Finding[]
  questions: Question[]
  resolutions: Record<string, string>
  onResolve: (id: string, reason: string) => void
  onUndo: (id: string) => void
}

function findingLabel(type: string) {
  const label = type.replaceAll('_', ' ')
  return label.charAt(0).toUpperCase() + label.slice(1)
}

export default function ReviewFindings({ readOnly = false, itemId, findings, questions, resolutions, onResolve, onUndo }: Props) {
  const [reviewView, setReviewView] = useState<'questions' | 'findings'>('questions')
  const [showCase, setShowCase] = useState(false)
  const [showResolved, setShowResolved] = useState(false)
  const relevantFindings = findings.filter(finding =>
    finding.item_ids.includes(itemId) || (showCase && finding.item_ids.length === 0)
  )
  const relevantQuestions = questions.filter(question =>
    question.item_id === itemId || (showCase && question.item_id === null)
  )
  const isQuestionResolved = (question: Question) =>
    question.status === 'resolved' || Boolean(resolutions[question.question_id])
  const isFindingResolved = (finding: Finding) =>
    finding.status === 'resolved' || Boolean(resolutions[finding.finding_id])
  const openQuestions = relevantQuestions.filter(question => !isQuestionResolved(question))
  const openFindings = relevantFindings.filter(finding => !isFindingResolved(finding))
  const resolvedCount = relevantQuestions.length + relevantFindings.length - openQuestions.length - openFindings.length
  const visibleQuestions = relevantQuestions.filter(question => showResolved || !isQuestionResolved(question))
  const visibleFindings = relevantFindings.filter(finding => showResolved || !isFindingResolved(finding))

  return (
    <section className="review-findings" aria-label="Item review">
      <div className="review-heading">
        <div>
          <h4>A little more clarity</h4>
          <p>{readOnly ? 'Saved findings and questions. Answer submission is not connected yet.' : 'Answer what you can and keep a note of what you verified.'}</p>
        </div>
        <span className="review-progress" role="status">{resolvedCount} resolved</span>
      </div>

      <div className="review-navigation" aria-label="Review categories">
        <button type="button" aria-pressed={reviewView === 'questions'}
          onClick={() => setReviewView('questions')}>
          Questions <span>{openQuestions.length}</span>
        </button>
        <button type="button" aria-pressed={reviewView === 'findings'}
          onClick={() => setReviewView('findings')}>
          Findings <span>{openFindings.length}</span>
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

      <div hidden={reviewView !== 'questions'} className="review-list">
        {visibleQuestions.length === 0 && (
          <div className="review-empty">
            <span className="review-empty-icon" aria-hidden="true">✓</span>
            <h4>{relevantQuestions.length > 0 ? 'Questions are up to date' : 'No questions for this item'}</h4>
            <p>{relevantQuestions.length > 0
              ? 'You’ve addressed the questions in this view. Turn on “Show resolved” to see previous answers.'
              : 'There’s nothing to answer here. You can still review findings or include case-wide issues.'}</p>
          </div>
        )}
        {visibleQuestions.map(question => {
          const resolved = isQuestionResolved(question)
          const localAnswer = resolutions[question.question_id]
          const answer = localAnswer || question.resolution_reason
          return (
            <article key={`${itemId}-${question.question_id}`} className={`question${resolved ? ' is-resolved' : ''}`}>
              <div className="review-card-meta">
                <span className="review-scope">{question.item_id === null ? 'Case-wide' : 'This item'}</span>
                <span className={`review-status${resolved ? ' is-resolved' : ''}`}>{resolved ? 'Answered' : 'Your input needed'}</span>
              </div>
              <h4>{question.question}</h4>
              <details className="review-context">
                <summary>Why we’re asking</summary>
                <p>{question.reason}</p>
              </details>
              {resolved ? (
                <div className="review-decision">
                  <span>{localAnswer ? 'Your answer · saved for this session' : 'Previous answer'}</span>
                  <p>{answer || 'This question was previously marked as resolved.'}</p>
                  {localAnswer && <button type="button" className="button-quiet"
                    onClick={() => onUndo(question.question_id)}>Undo answer</button>}
                </div>
              ) : readOnly ? <p className="helper">Needs review</p> : (
                <ResolveFinding
                  key={`${itemId}-${question.question_id}-answer`}
                  label="Your answer"
                  placeholder="Add what you know, or explain what you’ve checked…"
                  buttonLabel="Save answer"
                  onResolve={reason => onResolve(question.question_id, reason)}
                />
              )}
            </article>
          )
        })}
      </div>

      <div hidden={reviewView !== 'findings'} className="review-list">
        {visibleFindings.length === 0 && (
          <div className="review-empty">
            <span className="review-empty-icon" aria-hidden="true">✓</span>
            <h4>{relevantFindings.length > 0 ? 'Findings are up to date' : 'No findings for this item'}</h4>
            <p>{relevantFindings.length > 0
              ? 'Everything in this view has been reviewed. Turn on “Show resolved” to see your decisions.'
              : 'There are no findings in this view. Include case-wide issues to review shared details.'}</p>
          </div>
        )}
        {visibleFindings.map(finding => {
          const resolved = isFindingResolved(finding)
          const localDecision = resolutions[finding.finding_id]
          const decision = localDecision || finding.resolution_reason
          return (
            <details key={`${itemId}-${finding.finding_id}`} className={`finding${resolved ? ' is-resolved' : ''}`}>
              <summary>
                <span className="finding-title">{findingLabel(finding.type)}</span>
                <span className={`review-status${resolved ? ' is-resolved' : ''}`}>{resolved ? 'Resolved' : 'Needs review'}</span>
                <span className="finding-preview">{finding.explanation}</span>
              </summary>
              <div className="finding-content">
                <div className="review-card-meta">
                  <span className="review-scope">{finding.item_ids.length === 0 ? 'Case-wide' : 'This item'}</span>
                  <span className="review-record-id">{finding.finding_id}</span>
                </div>
                <p>{finding.explanation}</p>
                <div className="finding-evidence">
                  <span>Supporting evidence</span>
                  <div>{finding.evidence_ids.length > 0
                    ? finding.evidence_ids.map(id => <code key={id}>{id}</code>)
                    : <span className="helper">No evidence linked</span>}</div>
                </div>
                {resolved ? (
                  <div className="review-decision">
                    <span>{localDecision ? 'Your review note · saved for this session' : 'Previous review decision'}</span>
                    <p>{decision || 'This finding was previously marked as resolved.'}</p>
                    {localDecision && <button type="button" className="button-quiet"
                      onClick={() => onUndo(finding.finding_id)}>Undo resolution</button>}
                  </div>
                ) : readOnly ? <p className="helper">Needs review</p> : (
                  <ResolveFinding key={`${itemId}-${finding.finding_id}-resolution`}
                    onResolve={reason => onResolve(finding.finding_id, reason)} />
                )}
              </div>
            </details>
          )
        })}
      </div>
      <p className="review-local-note">{readOnly ? 'Saved findings and questions are read-only here.' : 'Your answers and review notes stay in this browser session until you refresh.'}</p>
    </section>
  )
}

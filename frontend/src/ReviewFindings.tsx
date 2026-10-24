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
}

export default function ReviewFindings({ itemId, findings, questions }: Props) {
  const relevant = findings.filter(finding =>
    finding.item_ids.includes(itemId) || finding.item_ids.length === 0
  )
  const openQuestions = questions.filter(question =>
    question.status === 'open' &&
    (question.item_id === itemId || question.item_id === null)
  )

  return (
    <section className="review-findings">
      <h4>What needs a closer look?</h4>
      <p className="helper">Includes findings about this item and the whole case.</p>

      {relevant.map(finding => (
        <details key={`${itemId}-${finding.finding_id}`} className="finding">
          <summary>
            {finding.status === 'resolved' ? 'Resolved' : 'Needs review'}
            {' · '}{finding.type.replaceAll('_', ' ')}
            {' · '}{finding.finding_id}
          </summary>
          <p>{finding.explanation}</p>
          <small>Evidence: {finding.evidence_ids.join(', ') || 'None'}</small>
          {finding.resolution_reason && (
            <p className="notice">{finding.resolution_reason}</p>
          )}
        </details>
      ))}

      <h4>Open questions ({openQuestions.length})</h4>
      {openQuestions.length === 0 && <p>No open questions for this item or case.</p>}
      {openQuestions.map(question => (
        <div key={question.question_id} className="question">
          <strong>{question.question}</strong>
          <p>{question.reason}</p>
          <small>{question.item_id === null ? 'Case question' : 'Item question'}</small>
        </div>
      ))}
    </section>
  )
}

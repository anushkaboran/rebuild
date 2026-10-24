import ItemCorrection from './ItemCorrection'
import ReviewFindings from './ReviewFindings'
import type { Finding, Question } from './ReviewFindings'

import { useState } from 'react'
import FieldEditor from './FieldEditor'

type Item = {
  revision: number
  condition?: string | null
  estimated_value?: number | null
  agent_item_id?: string | null
  investigation_id?: string | null
  item_id: string
  name: string
  room: string
  location_in_image: string
  visible_observations: string
  supporting_evidence_ids: string[]
  brand: string | null
  model: string | null
  purchase_date: string | null
  purchase_price: string | null
  currency: string | null
  user_confirmed_fields: string[]
}

type Evidence = {
  url?: string | null
  content_type?: string | null
  evidence_id: string
  filename: string
  description: string
}

type Result = {
  findings: (Finding & { finding_type?: string })[]
  questions: Question[]
  items: Item[]
  evidence: Evidence[]
}

export default function Inventory({
  caseId,
  simulated = false,
}: {
  caseId?: string
  simulated?: boolean
}) {
  const [source, setSource] = useState('')

  async function loadReview() {
    setLoading(true)
    setError('')
    try {
      const response = await fetch(
        '/api/cases/' + encodeURIComponent(caseId!) + '/review'
      )
      if (!response.ok) {
        throw new Error('Could not load inventory (' + response.status + ').')
      }
      const data = await response.json()
      if (!Array.isArray(data.items) || !Array.isArray(data.evidence)) {
        throw new Error('The review response is missing items or evidence.')
      }

      setResult({
        ...data,
        items: data.items.map((item: Item & { id: string }) => ({
          ...item,
          item_id: item.id,
          supporting_evidence_ids: item.supporting_evidence_ids ?? [],
          user_confirmed_fields: item.user_confirmed_fields ?? [],
        })),
        findings: data.findings ?? [],
        questions: data.questions ?? [],
      })
      setSource(data.source ?? '')
      setSelectedId('')
      setCorrections({})
      setResolutions({})
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load inventory.')
    } finally {
      setLoading(false)
    }
  }
  const [panel, setPanel] = useState<'details' | 'evidence' | 'review'>('details')
  const [resolutions, setResolutions] = useState<Record<string, string>>({})
  const [corrections, setCorrections] = useState<Record<string, Record<string, string>>>({})
  const [result, setResult] = useState<Result | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function loadDemo() {
    setLoading(true)
    setError('')
    try {
      const response = await fetch('/demo-result.json')
      if (!response.ok) throw new Error('Could not load demo file.')
      const data = await response.json()
      if (!Array.isArray(data.items) || !Array.isArray(data.evidence)) {
        throw new Error('Demo file is missing items or evidence.')
      }
      setResult(data)
      setSelectedId('')
    } catch {
      setError('Could not load the sample. Check public/demo-result.json.')
    } finally {
      setLoading(false)
    }
  }

  const displayedItems = result?.items.map(item => ({
    ...item,
    ...(corrections[item.item_id] ?? {}),
    user_confirmed_fields: [...new Set([
      ...item.user_confirmed_fields,
      ...Object.keys(corrections[item.item_id] ?? {})
    ])]
  })) ?? []

  const selected = displayedItems.find(item => item.item_id === selectedId)

  function confirmField(field: string, value: string) {
    setCorrections(current => ({
      ...current,
      [selectedId]: { ...(current[selectedId] ?? {}), [field]: value }
    }))
  }

  return (
    <section className="case-form">
      <p className="eyebrow">
        {caseId ? (simulated ? 'SIMULATED CASE RESULTS' : 'YOUR CASE INVENTORY') : 'SAVED DEMO CASE'}
      </p>
      <h2>{caseId ? 'Review your inventory' : 'Explore an example inventory'}</h2>
      <p className="helper">
        {caseId
          ? (simulated
            ? 'Simulated test records. These do not describe your uploaded photo.'
            : 'Select an item to inspect its details and supporting evidence.')
          : 'This example is separate from your draft and selected files.'}
      </p>
      {(!result || caseId) && (
        <button type="button" onClick={caseId ? loadReview : loadDemo} disabled={loading}>
          {loading ? 'Loading…' : caseId
            ? (result ? 'Refresh case inventory' : 'Open case inventory →')
            : 'Open demo inventory →'}
        </button>
      )}
      {error && <p role="alert">{error}</p>}
      {source.includes('stale') && (
        <p role="alert">The shared database is unreachable. Showing cached records.</p>
      )}

      {result && (
        <>
          <p>{result.items.length} item records · Select one to inspect its evidence.</p>
          <div className="inventory-grid">
            {displayedItems.map(item => (
              <button
                type="button"
                className="item-card"
                key={item.item_id}
                aria-pressed={item.item_id === selectedId}
                onClick={() => {
    setSelectedId(item.item_id)
    setPanel('details')
  }}
              >
                <small>{item.item_id}</small>
                <strong>{item.name}</strong>
                <small>
                  {item.user_confirmed_fields.length} confirmed fields
                </small>
              </button>
            ))}
          </div>

          {!selected && (
            <div className="inventory-empty">
              <span className="eyebrow">YOUR INVENTORY</span>
              <h3>Select an item to take a closer look</h3>
              <p>Its details, evidence, and review questions will appear here.</p>
            </div>
          )}
          {selected && (
            <article className="item-details">
              <h3>{selected.name}</h3>
              <div className="panel-switch" aria-label="Item information">
                <button type="button" aria-pressed={panel === 'details'}
    onClick={() => setPanel('details')}>Details</button>
    <button type="button" aria-pressed={panel === 'review'}
                  onClick={() => setPanel('review')}>Review</button>
                <button type="button" aria-pressed={panel === 'evidence'}
                  onClick={() => setPanel('evidence')}>
                  Evidence ({selected.supporting_evidence_ids.length})
                </button>
              </div>
{panel === 'details' && <div>
              {caseId && (
                <>
                  <p className="helper">
                    Estimated value: {selected.estimated_value ?? 'Unknown'}
                    {' · '}Currency: {selected.currency ?? 'Unknown'}
                  </p>
                  <ItemCorrection
                    key={selected.item_id + ':' + selected.revision}
                    item={selected}
                    onSaved={loadReview}
                  />
                </>
              )}
<details><summary>Visible observations</summary><p>{selected.visible_observations}</p></details>
              <details>
                <summary>Location in the image</summary>
                <p>{selected.location_in_image}</p>
              </details>


              <p className="notice">
                {caseId
                  ? 'Corrections are saved to the backend with the reviewer name and revision history.'
                  : 'Demo edits stay in this page until refresh. They are not saved to the server.'}
              </p>
              {(['name', 'brand', 'model', 'purchase_date', 'purchase_price', 'currency'] as const).map(field => (
                caseId ? (
                <div className="field" key={selected.item_id + field}>
                  <h4>{field.replaceAll('_', ' ')}</h4>
                  <p>{selected[field] ?? 'Unknown'}</p>
                  {selected.user_confirmed_fields.includes(field) && <small>User confirmed</small>}
                </div>
              ) : <FieldEditor
                  key={selected.item_id + field}
                  label={field}
                  value={selected[field]}
                  confirmed={selected.user_confirmed_fields.includes(field)}
                  onConfirm={value => confirmField(field, value)}
                />
              ))}


              
              </div>}

              {panel === 'evidence' && <div className="evidence-panel">
                <h4>Supporting evidence</h4>
              {selected.supporting_evidence_ids.map(id => {
                const evidence = result.evidence.find(entry => entry.evidence_id === id)
                return (
                  <details key={id}>
                    <summary>{id} · {evidence?.filename ?? 'File unavailable'}</summary>
                    <p>{evidence?.description ?? 'No description available.'}</p>
                    {evidence?.url?.startsWith('/api/uploads/') && (
                      <>
                        {evidence.content_type?.startsWith('image/') && (
                          <img
                            src={evidence.url}
                            alt={evidence.description || evidence.filename}
                            loading="lazy"
                            style={{ maxWidth: '100%', maxHeight: 360, objectFit: 'contain' }}
                          />
                        )}
                        <p><a href={evidence.url} target="_blank" rel="noreferrer">
                          Open original evidence ↗
                        </a></p>
                      </>
                    )}
                  </details>
                )
              })}
            
              </div>}
              {panel === 'review' && caseId && (
                <div className="review-findings">
                  <h4>Findings for this item and case</h4>
                  {result.findings.filter(finding =>
                    (!selected.investigation_id ||
                      (finding as Finding & { id?: string }).id?.startsWith(selected.investigation_id + ':')) &&
                    (!finding.item_ids?.length ||
                      finding.item_ids.includes(selected.agent_item_id ?? selected.item_id))
                  ).map(finding => (
                    <details key={finding.finding_id}>
                      <summary>{(finding.finding_type ?? 'Finding').replaceAll('_', ' ')} · {finding.status}</summary>
                      <p>{finding.explanation}</p>
                      <small>Evidence: {finding.evidence_ids.join(', ')}</small>
                      {finding.resolution_reason && <p>{finding.resolution_reason}</p>}
                    </details>
                  ))}
                  <h4>Questions for this item and case</h4>
                  {result.questions.filter(question =>
                    (!selected.investigation_id ||
                      (question as Question & { id?: string }).id?.startsWith(selected.investigation_id + ':')) &&
                    (!question.item_id ||
                      question.item_id === (selected.agent_item_id ?? selected.item_id))
                  ).map(question => (
                    <details key={question.question_id}>
                      <summary>{question.question}</summary>
                      <p>{question.reason}</p>
                      <small>{question.status}</small>
                    </details>
                  ))}
                </div>
              )}
              {panel === 'review' && !caseId && <ReviewFindings
      itemId={selected.item_id}
      findings={result.findings}
      questions={result.questions}
      resolutions={resolutions}
      onResolve={(id, reason) => setResolutions(current => ({ ...current, [id]: reason }))}
      onUndo={id => setResolutions(current => {
        const next = { ...current }
        delete next[id]
        return next
      })}
    />}
</article>
          )}
        </>
      )}
    </section>
  )
}

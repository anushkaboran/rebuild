import ReviewFindings from './ReviewFindings'
import type { Finding, Question } from './ReviewFindings'

import { useState } from 'react'
import FieldEditor from './FieldEditor'

type Item = {
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
  evidence_id: string
  filename: string
  description: string
}

type Result = {
  findings: Finding[]
  questions: Question[]
  items: Item[]
  evidence: Evidence[]
}

export default function Inventory() {
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
      setSelectedId(data.items[0]?.item_id ?? '')
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
      <p className="eyebrow">SAVED DEMO CASE</p>
      <h2>Explore an example inventory</h2>
      <p className="helper">
        This example is separate from your draft and selected files.
      </p>
      {!result && (
        <button type="button" onClick={loadDemo} disabled={loading}>
          {loading ? 'Loading example…' : 'Open demo inventory →'}
        </button>
      )}
      {error && <p role="alert">{error}</p>}

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
                onClick={() => setSelectedId(item.item_id)}
              >
                <small>{item.item_id}</small>
                <strong>{item.name}</strong>
                <small>
                  {item.user_confirmed_fields.length} confirmed fields
                </small>
              </button>
            ))}
          </div>

          {selected && (
            <article className="item-details">
              <h3>{selected.name}</h3>
              <p>{selected.visible_observations}</p>
              <details>
                <summary>Location in the image</summary>
                <p>{selected.location_in_image}</p>
              </details>


              <p className="notice">
                Demo edits stay in this page until refresh. They are not saved
                to the server. Agent findings and questions remain as originally returned.
              </p>
              {(['name', 'brand', 'model', 'purchase_date', 'purchase_price', 'currency'] as const).map(field => (
                <FieldEditor
                  key={selected.item_id + field}
                  label={field}
                  value={selected[field]}
                  confirmed={selected.user_confirmed_fields.includes(field)}
                  onConfirm={value => confirmField(field, value)}
                />
              ))}


              <h4>Supporting evidence</h4>
              {selected.supporting_evidence_ids.map(id => {
                const evidence = result.evidence.find(entry => entry.evidence_id === id)
                return (
                  <details key={id}>
                    <summary>{id} · {evidence?.filename ?? 'File unavailable'}</summary>
                    <p>{evidence?.description ?? 'No description available.'}</p>
                  </details>
                )
              })}
            <ReviewFindings itemId={selected.item_id} findings={result.findings} questions={result.questions} /></article>
          )}
        </>
      )}
    </section>
  )
}

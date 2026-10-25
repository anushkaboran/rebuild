import { useRef, useState } from 'react'
import type { ResearchCandidate } from './ResearchCandidates'

export type ResearchItem = {
  item_id: string
  revision: number
  brand: string | null
  model: string | null
}

export default function SelectResearchCandidate({ candidate, item, runId, onSaved }: {
  candidate: ResearchCandidate
  item: ResearchItem
  runId: string
  onSaved: () => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [recognized, setRecognized] = useState(false)
  const [reviewer, setReviewer] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [conflict, setConflict] = useState(false)
  const lock = useRef(false)
  const brand = candidate.brand?.trim() || null
  const model = candidate.model?.trim() || null

  async function save() {
    if (lock.current || saved || conflict || !recognized || !reviewer.trim()) return
    lock.current = true
    setSaving(true)
    setError('')
    try {
      const response = await fetch(`/api/items/${encodeURIComponent(item.item_id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...(brand ? { brand } : {}),
          ...(model ? { model } : {}),
          editor: reviewer.trim(),
          if_revision: item.revision,
          note: `User recognized this product and selected research candidate ${candidate.candidate_id} from run ${runId}. Source: ${candidate.product_url ?? 'not provided'}`.slice(0, 1000),
        }),
      })
      if (response.status === 409) {
        setConflict(true)
        throw new Error('This item changed. Refresh case inventory and review the latest details before selecting again.')
      }
      if (!response.ok) throw new Error(`Could not save product details (${response.status}).`)
      setSaved(true)
      try {
        await onSaved()
      } catch {
        setError('Details were saved. Refresh case inventory to see the updated fields.')
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save product details.')
    } finally {
      lock.current = false
      setSaving(false)
    }
  }

  if (!brand && !model) return null
  return (
    <div className="research-selection">
      {!open && <button type="button" className="button-secondary" onClick={() => setOpen(true)}>
        Use these product details
      </button>}
      {open && <form onSubmit={event => { event.preventDefault(); void save() }}>
        <fieldset disabled={saving || saved || conflict}>
          <legend>Review product identity</legend>
          {brand && <p>Brand: {item.brand || 'Unknown'} → <strong>{brand}</strong></p>}
          {model && <p>Model: {item.model || 'Unknown'} → <strong>{model}</strong></p>}
          <p>Only the displayed brand and model will be saved. Missing fields stay unchanged. Listing prices are not copied.</p>
          <label className="research-recognition">
            <input type="checkbox" checked={recognized} onChange={event => setRecognized(event.target.checked)} required />
            I recognize this as my item.
          </label>
          <label>Your name
            <input value={reviewer} maxLength={120} required onChange={event => setReviewer(event.target.value)} />
          </label>
          <button type="submit" className="button-primary" disabled={!recognized || !reviewer.trim()}>
            {saving ? 'Saving…' : 'Confirm and save product details'}
          </button>
          <button type="button" className="button-secondary" onClick={() => { setOpen(false); setRecognized(false); setError('') }}>
            Keep as a possible match
          </button>
        </fieldset>
        {saved && <p role="status">Product details saved from your confirmation. The research suggestion remains unverified.</p>}
        {error && <p role="alert">{error}</p>}
      </form>}
    </div>
  )
}

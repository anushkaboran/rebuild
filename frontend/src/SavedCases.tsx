import { useState } from 'react'
import Inventory from './Inventory'

type Case = {
  id: string
  title: string
  item_count: number
}

export default function SavedCases() {
  const [cases, setCases] = useState<Case[]>([])
  const [loaded, setLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<{
    id: string
    simulated: boolean
    mixed: boolean
  } | null>(null)

  async function loadCases() {
    setLoading(true)
    setError('')
    try {
      const response = await fetch('/api/cases')
      if (!response.ok) throw new Error('Could not load saved cases.')
      const data = await response.json()
      if (!Array.isArray(data)) throw new Error('Unexpected case list.')
      setCases(data)
      setLoaded(true)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Request failed.')
    } finally {
      setLoading(false)
    }
  }

  async function openCase(id: string) {
    setLoading(true)
    setError('')
    setSelected(null)
    try {
      const response = await fetch(
        `/api/cases/${encodeURIComponent(id)}/investigations`,
      )
      if (!response.ok) throw new Error('Could not check case investigations.')
      const runs: { status: string; mode: string }[] = await response.json()
      const completed = runs.filter(run => run.status === 'complete')
      const hasMock = completed.some(run => run.mode === 'mock')
      const hasLive = completed.some(run => run.mode === 'live')
      setSelected({
        id,
        simulated: hasMock && !hasLive,
        mixed: hasMock && hasLive,
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not open case.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="case-form">
      <p className="eyebrow">RETURN TO YOUR WORK</p>
      <h2>Saved cases</h2>
      <button type="button" disabled={loading}
        onClick={() => void loadCases()}>
        {loading ? 'Loading…' : loaded ? 'Refresh saved cases' : 'Find saved cases'}
      </button>

      {error && <p role="alert">{error}</p>}
      {loaded && cases.length === 0 && (
        <p>No cases are currently available in this backend.</p>
      )}

      <div className="saved-case-list">
        {cases.map(entry => (
          <button type="button" key={entry.id} disabled={loading}
            onClick={() => void openCase(entry.id)}>
            {entry.title} · {entry.item_count} items
            <small style={{ display: 'block' }}>{entry.id}</small>
          </button>
        ))}
      </div>

      {selected?.mixed && (
        <p role="alert">
          This case contains both simulated and live investigations.
          Its inventory must not be treated as entirely real evidence.
        </p>
      )}
      {selected && (
        <Inventory key={selected.id} caseId={selected.id}
          simulated={selected.simulated} />
      )}
    </section>
  )
}

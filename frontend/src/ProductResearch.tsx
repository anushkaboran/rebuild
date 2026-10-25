import { useEffect, useRef, useState } from 'react'
import ResearchCandidates from './ResearchCandidates'
import type { ResearchCandidate } from './ResearchCandidates'

type ResearchRun = {
  id: string
  status: 'queued' | 'running' | 'complete' | 'failed'
  error?: string | null
  limitations?: string[]
  created_at: string
}

type ResearchResult = {
  run: ResearchRun
  candidates: ResearchCandidate[]
}

export default function ProductResearch({ itemId }: { itemId: string }) {
  const [result, setResult] = useState<ResearchResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [regionCurrency, setRegionCurrency] = useState('USD')
  const [starting, setStarting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const startLock = useRef(false)

  async function startResearch() {
    if (startLock.current || submitted || result) return
    startLock.current = true
    setStarting(true)
    setSubmitted(true)
    setError('')
    try {
      const response = await fetch(
        `/api/items/${encodeURIComponent(itemId)}/research`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            requested_by: 'Lasya',
            ...(regionCurrency.trim()
              ? { region_currency: regionCurrency.trim() } : {}),
          }),
        },
      )
      if (!response.ok) {
        throw new Error(`Research request failed (${response.status}).`)
      }
      const run: ResearchRun = await response.json()
      if (!run.id) throw new Error('No research ID was returned.')
      setResult({ run, candidates: [] })
      setRefresh(value => value + 1)
    } catch (cause) {
      setError(
        (cause instanceof Error ? cause.message : 'Request failed.') +
        ' Refresh saved research to check whether a run was accepted before trying again.',
      )
    } finally {
      startLock.current = false
      setStarting(false)
    }
  }


  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined

    async function load() {
      setLoading(true)
      setError('')
      try {
        const historyResponse = await fetch(
          `/api/items/${encodeURIComponent(itemId)}/research`,
          { signal: controller.signal },
        )
        if (!historyResponse.ok) throw new Error('Could not load research history.')
        const history: ResearchRun[] = await historyResponse.json()
        if (!Array.isArray(history)) throw new Error('Unexpected research history.')

        const latest = [...history].sort(
          (a, b) => b.created_at.localeCompare(a.created_at),
        )[0]

        if (!latest) {
          if (!controller.signal.aborted) setResult(null)
          return
        }

        const response = await fetch(
          `/api/research/${encodeURIComponent(latest.id)}`,
          { signal: controller.signal },
        )
        if (!response.ok) throw new Error('Could not load research results.')
        const data: ResearchResult = await response.json()
        if (!data.run || !Array.isArray(data.candidates)) {
          throw new Error('Unexpected research result.')
        }
        if (controller.signal.aborted) return
        setResult(data)

        if (data.run.status === 'queued' || data.run.status === 'running') {
          timer = setTimeout(() => void load(), 3000)
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : 'Research unavailable.')
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }

    void load()
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [itemId, refresh])

  return (
    <section className="product-research" aria-label="Product research">
      <h3>Possible product matches</h3>
      <p className="helper">
        Suggestions stay separate from your confirmed item details.
      </p>
      <button type="button" className="button-secondary"
        disabled={loading} onClick={() => setRefresh(value => value + 1)}>
        {loading ? 'Checking research…' : 'Refresh saved research'}
      </button>


      {!loading && !result && !submitted && !error && (
        <form onSubmit={event => {
          event.preventDefault()
          void startResearch()
        }}>
          <label>
            Listing currency (optional)
            <input value={regionCurrency} maxLength={3}
              placeholder="USD"
              onChange={event => setRegionCurrency(event.target.value)} />
          </label>
          <p className="helper">
            Defaults to USD listings. This does not establish your location or purchase currency. Starting research uses CREAO credits.
          </p>
          <button type="submit" className="button-primary" disabled={starting}>
            Find possible matches
          </button>
        </form>
      )}
      {starting && <p role="status">Submitting research request…</p>}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && !result && <p>No saved research for this item yet.</p>}
      {result && <p role="status">Research status: {result.run.status}</p>}
      {result?.run.status === 'failed' && (
        <p role="alert">
          {result.run.error || 'Research could not be completed.'}
          {' '}No new run has been started by this screen.
        </p>
      )}
      {result?.run.status === 'complete' && (
        <ResearchCandidates candidates={result.candidates}
          limitations={result.run.limitations ?? []} />
      )}
    </section>
  )
}

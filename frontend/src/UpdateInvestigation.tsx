import { useEffect, useRef, useState } from 'react'

type Run = {
  id: string
  status: 'queued' | 'running' | 'complete' | 'failed'
  mode: 'mock' | 'live'
  error?: string | null
  items_created?: number
  items_reconciled?: number
  items_flagged?: number
  review_answers_sent?: number
}

export default function UpdateInvestigation({
  caseId, onComplete,
}: {
  caseId: string
  onComplete: () => Promise<void>
}) {
  const [run, setRun] = useState<Run | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const lock = useRef(false)
  const notified = useRef('')
  const active = run?.status === 'queued' || run?.status === 'running'

  async function start() {
    if (lock.current || run) return
    lock.current = true
    setBusy(true)
    setError('')
    try {
      const runsResponse = await fetch(`/api/cases/${caseId}/investigations`)
      if (!runsResponse.ok) throw new Error('Could not check existing runs.')
      const runs: Run[] = await runsResponse.json()
      const pending = runs.find(r => r.status === 'queued' || r.status === 'running')
      if (pending) {
        setRun(pending)
        return
      }

      const response = await fetch(`/api/cases/${caseId}/uploads`)
      if (!response.ok) throw new Error('Could not load uploaded evidence.')
      const uploads: { id: string }[] = await response.json()
      const ids = [...new Set(uploads.map(upload => upload.id))]
      if (!ids.length) throw new Error('Upload evidence before updating.')
      if (ids.length > 10) throw new Error('This case exceeds the 10-file limit.')

      const started = await fetch(`/api/cases/${caseId}/investigations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          upload_id: ids[0],
          upload_ids: ids.slice(1),
          requested_by: 'Lasya',
        }),
      })
      if (!started.ok) throw new Error(`Update request failed (${started.status}).`)
      const data: Run = await started.json()
      if (!data.id) throw new Error('No investigation ID was returned.')
      setRun(data)
    } catch (cause) {
      setError(
        (cause instanceof Error ? cause.message : 'Request failed.') +
        ' Check existing investigations before trying again.',
      )
    } finally {
      lock.current = false
      setBusy(false)
    }
  }

  const runId = run?.id
  useEffect(() => {
    if (!runId || !active) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined

    async function poll() {
      try {
        const response = await fetch(`/api/investigations/${runId}`, {
          signal: controller.signal,
        })
        if (!response.ok) throw new Error('Could not check update progress.')
        const data: Run = await response.json()
        if (controller.signal.aborted) return
        setRun(data)
        setError('')
        if (data.status === 'queued' || data.status === 'running') {
          timer = setTimeout(() => void poll(), 2000)
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : 'Progress check failed.')
        }
      }
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [runId, active, retry])

  useEffect(() => {
    if (run?.status !== 'complete' || notified.current === run.id) return
    notified.current = run.id
    void onComplete()
  }, [run, onComplete])

  return (
    <div className="investigation-controls">
      {!run && <button type="button" className="button-primary"
        disabled={busy} onClick={() => void start()}>
        {busy ? 'Starting update…' : 'Update investigation'}
      </button>}
      {!run && <p className="helper">
        Ask CREAO to reassess uploaded evidence using your saved answers.
        Starting an update uses agent credits.
      </p>}
      {run && <p className="notice" role="status">
        {run.mode === 'mock' ? 'Simulated update' : 'CREAO update'} · {run.status}
        {run.status === 'complete' &&
          ` · ${run.items_created ?? 0} new · ${run.items_reconciled ?? 0} updated · ${run.items_flagged ?? 0} need identity review · ${run.review_answers_sent ?? 0} answers sent`}
      </p>}
      {(error || run?.error) && <p role="alert">{error || run?.error}</p>}
      {error && active && <button type="button" className="button-secondary"
        onClick={() => setRetry(value => value + 1)}>
        Check progress again
      </button>}
    </div>
  )
}

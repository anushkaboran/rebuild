import Inventory from './Inventory'
import { useEffect, useRef, useState } from 'react'

type Investigation = {
  id: string
  status: 'queued' | 'running' | 'complete' | 'failed'
  mode: 'mock' | 'live'
  item_ids?: string[]
  error?: string | null
}

export default function InvestigationRunner({
  caseId,
  uploadIds,
  room,
}: {
  caseId: string
  uploadIds: string[]
  room: string
}) {
  const [run, setRun] = useState<Investigation | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const startingRef = useRef(false)
  const [pollAttempt, setPollAttempt] = useState(0)

  async function start() {
    if (startingRef.current || run || uploadIds.length === 0) return
    startingRef.current = true
    setStarting(true)
    setError('')

    try {
      const response = await fetch(
        `/api/cases/${encodeURIComponent(caseId)}/investigations`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            upload_id: uploadIds[0],
            upload_ids: uploadIds.slice(1),
            room_hint: room,
            requested_by: 'Lasya',
          }),
        },
      )

      if (!response.ok) {
        throw new Error(`Could not start investigation (${response.status}).`)
      }

      const data = await response.json()
      if (typeof data.id !== 'string') {
        throw new Error('The server did not return an investigation ID.')
      }
      setRun(data)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Request failed.')
    } finally {
      startingRef.current = false
      setStarting(false)
    }
  }

  const runId = run?.id
  const status = run?.status

  useEffect(() => {
    if (!runId || status === 'complete' || status === 'failed') return

    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>

    async function poll() {
      try {
        const response = await fetch(
          `/api/investigations/${encodeURIComponent(runId!)}`,
          { signal: controller.signal },
        )
        if (!response.ok) {
          throw new Error(`Could not check progress (${response.status}).`)
        }

        const data: Investigation = await response.json()
        if (controller.signal.aborted) return
        setRun(data)
        setError('')

        if (data.status !== 'complete' && data.status !== 'failed') {
          timer = setTimeout(() => void poll(), 1500)
        }
      } catch (cause) {
        if (controller.signal.aborted) return
        setError(cause instanceof Error ? cause.message : 'Progress check failed.')
      }
    }

    void poll()
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [runId, status, pollAttempt])

  return (
    <div className="investigation-controls">
      {!run && (
        <button type="button" disabled={starting} onClick={() => void start()}>
          {starting ? 'Starting…' : 'Start investigation →'}
        </button>
      )}

      {run && (
        <p className="notice" role="status">
          {run.mode === 'mock' ? 'Simulated investigation' : 'CREAO investigation'}
          {' · '}
          {run.status === 'complete'
            ? `Complete — ${run.item_ids?.length ?? 0} item records returned.`
            : run.status === 'failed'
              ? 'Failed.'
              : run.status === 'queued'
                ? 'Queued…'
                : 'Running…'}
        </p>
      )}

      {run?.mode === 'mock' && (
        <p className="helper">
          These are simulated test results, not an analysis of your photo.
        </p>
      )}

      {(error || run?.error) && <p role="alert">{error || run?.error}</p>}

      {error && run && run.status !== 'complete' && run.status !== 'failed' && (
        <button type="button" onClick={() => setPollAttempt(value => value + 1)}>
          Check progress again
        </button>
      )}

      {run?.status === 'complete' && (
        <Inventory key={run.id} caseId={caseId} simulated={run.mode === 'mock'} />
      )}
    </div>
  )
}

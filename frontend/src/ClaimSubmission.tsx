import { useEffect, useRef, useState } from 'react'

type Submission = {
  id: string
  status: 'queued' | 'running' | 'complete' | 'failed'
  result_status?: 'draft_ready' | 'needs_information' | 'draft_failed' | null
  recipient_email: string
  subject?: string | null
  body_text?: string | null
  attachment_filename?: string | null
  gmail_draft_id?: string | null
  missing_information?: string[]
  warnings?: string[]
  error?: string | null
  created_at: string
}

export default function ClaimSubmission({ caseId }: { caseId: string }) {
  const [open, setOpen] = useState(false)
  const [submission, setSubmission] = useState<Submission | null>(null)
  const [loading, setLoading] = useState(false)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [attempted, setAttempted] = useState(false)
  const lock = useRef(false)

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined

    async function load() {
      setLoading(true)
      setError('')
      try {
        const historyResponse = await fetch(
          `/api/cases/${encodeURIComponent(caseId)}/claim-submissions`,
          { signal: controller.signal },
        )
        if (!historyResponse.ok) {
          throw new Error(`Could not load email drafts (${historyResponse.status}).`)
        }
        const history: Submission[] = await historyResponse.json()
        if (!Array.isArray(history)) throw new Error('Unexpected draft history.')
        const latest = [...history].sort(
          (a, b) => b.created_at.localeCompare(a.created_at),
        )[0]
        if (!latest) {
          if (!controller.signal.aborted) setSubmission(null)
          return
        }
        const response = await fetch(
          `/api/claim-submissions/${encodeURIComponent(latest.id)}`,
          { signal: controller.signal },
        )
        if (!response.ok) throw new Error('Could not check draft progress.')
        const data = await response.json()
        if (!data.submission?.id) throw new Error('Unexpected draft response.')
        if (controller.signal.aborted) return
        setSubmission(data.submission)
        if (['queued', 'running'].includes(data.submission.status)) {
          timer = setTimeout(() => void load(), 3000)
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : 'Draft unavailable.')
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
  }, [caseId, open, refresh])

  async function start(form: HTMLFormElement) {
    if (lock.current || attempted || submission || loading) return
    lock.current = true
    setStarting(true)
    setAttempted(true)
    setError('')
    const fields = new FormData(form)
    const value = (name: string) => String(fields.get(name) ?? '').trim()

    try {
      const response = await fetch(
        `/api/cases/${encodeURIComponent(caseId)}/claim-submission`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            sender_name: value('sender_name'),
            insurer_name: value('insurer_name'),
            recipient_email: value('recipient_email'),
            policy_number: value('policy_number') || null,
            claim_number: value('claim_number') || null,
            additional_notes: value('additional_notes') || null,
            include_recovery_pdf: true,
            requested_by: value('sender_name'),
          }),
        },
      )
      const data = await response.json()
      if (!response.ok) {
        throw new Error(
          typeof data.detail?.message === 'string'
            ? data.detail.message
            : `Could not prepare email (${response.status}).`,
        )
      }
      if (!data.submission?.id) throw new Error('No submission ID returned.')
      setSubmission(data.submission)
      setRefresh(value => value + 1)
    } catch (cause) {
      setError(
        (cause instanceof Error ? cause.message : 'Request failed.') +
        ' Check saved drafts and the connected Gmail account before trying again.',
      )
    } finally {
      lock.current = false
      setStarting(false)
    }
  }

  const active = submission?.status === 'queued' || submission?.status === 'running'
  const ready = submission?.result_status === 'draft_ready' &&
    Boolean(submission.gmail_draft_id)

  return (
    <div className="claim-email">
      <button type="button" className="button-secondary"
        aria-expanded={open} onClick={() => setOpen(value => !value)}>
        {open ? 'Close claim email' : 'Prepare claim email'}
      </button>

      {open && <section className="claim-email-panel" aria-label="Claim email">
        <h3>Prepare your claim email</h3>
        <p>
          Creates an unsent draft in the Gmail account connected to CREAO.
          Review and send it from that account. Starting uses CREAO credits.
        </p>
        <button type="button" className="button-secondary"
          disabled={loading || starting}
          onClick={() => setRefresh(value => value + 1)}>
          {loading ? 'Checking drafts…' : 'Refresh saved drafts'}
        </button>

        {error && <p role="alert">{error}</p>}
        {!loading && !error && !submission && !attempted && (
          <form onSubmit={event => {
            event.preventDefault()
            void start(event.currentTarget)
          }}>
            <fieldset disabled={starting}>
              <label>Your name
                <input name="sender_name" autoComplete="name" required maxLength={200} />
              </label>
              <label>Insurance company
                <input name="insurer_name" required maxLength={200} />
              </label>
              <label>Recipient email
                <input name="recipient_email" type="email" required maxLength={320} />
              </label>
              <label>Policy number (optional)
                <input name="policy_number" maxLength={120} />
              </label>
              <label>Claim number (optional)
                <input name="claim_number" maxLength={120} />
              </label>
              <label>Additional notes (optional)
                <textarea name="additional_notes" maxLength={4000} />
              </label>
              <p>The recovery PDF will be included. Nothing will be sent.</p>
              <button type="submit" className="button-primary">
                {starting ? 'Submitting…' : 'Create unsent Gmail draft'}
              </button>
            </fieldset>
          </form>
        )}

        {starting && <p role="status">Submitting draft request…</p>}
        {active && <p role="status">
          {submission.status === 'queued' ? 'Draft request queued…' : 'Preparing Gmail draft…'}
        </p>}
        {ready && <div role="status">
          <h4>Draft created — not sent</h4>
          <p>Recipient: {submission.recipient_email}</p>
          <p>Subject: {submission.subject}</p>
          <p>Attachment: {submission.attachment_filename ?? 'No attachment reported'}</p>
          <p>Open the connected Gmail account’s Drafts folder to review it.</p>
        </div>}
        {submission?.result_status === 'needs_information' && (
          <p role="status">More information is needed. No draft was reported.</p>
        )}
        {(submission?.status === 'failed' ||
          submission?.result_status === 'draft_failed') && (
          <p role="alert">
            Draft creation could not be confirmed. Check the connected Gmail
            account before retrying. {submission.error}
          </p>
        )}
        {submission?.body_text && <details>
          <summary>Preview email text</summary>
          <div className="claim-email-body">{submission.body_text}</div>
        </details>}
        {!!submission?.missing_information?.length && <div>
          <h4>Missing information</h4>
          <ul>{submission.missing_information.map((text, index) =>
            <li key={index}>{text}</li>)}</ul>
        </div>}
        {!!submission?.warnings?.length && <div>
          <h4>Review before sending</h4>
          <ul>{submission.warnings.map((text, index) =>
            <li key={index}>{text}</li>)}</ul>
        </div>}
      </section>}
    </div>
  )
}

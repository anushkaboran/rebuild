import { useId, useState } from 'react'

type Props = {
  onResolve: (reason: string) => void
  label?: string
  placeholder?: string
  buttonLabel?: string
}

export default function ResolveFinding({
  onResolve,
  label = 'Add a review note',
  placeholder = 'Explain what you verified or decided…',
  buttonLabel = 'Mark as resolved',
}: Props) {
  const [reason, setReason] = useState('')
  const inputId = useId()

  return (
    <form className="resolve-form" onSubmit={event => {
      event.preventDefault()
      if (!reason.trim()) return
      onResolve(reason.trim())
      setReason('')
    }}>
      <label htmlFor={inputId}>{label}</label>
      <textarea
        id={inputId}
        value={reason}
        onChange={event => setReason(event.target.value)}
        placeholder={placeholder}
        rows={3}
        required
      />
      <div className="resolve-form-actions">
        <span>Saved for this session</span>
        <button type="submit" className="button-primary" disabled={!reason.trim()}>{buttonLabel}</button>
      </div>
    </form>
  )
}

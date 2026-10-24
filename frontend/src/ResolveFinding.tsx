import { useState } from 'react'

type Props = {
  onResolve: (reason: string) => void
}

export default function ResolveFinding({ onResolve }: Props) {
  const [reason, setReason] = useState('')

  return (
    <form onSubmit={event => {
      event.preventDefault()
      if (!reason.trim()) return
      onResolve(reason.trim())
      setReason('')
    }}>
      <label>
        How was this resolved?
        <textarea
          value={reason}
          onChange={event => setReason(event.target.value)}
          placeholder="Explain what you verified or decided."
          required
        />
      </label>
      <button type="submit">Mark resolved</button>
    </form>
  )
}

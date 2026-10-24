import { useState } from 'react'

type Props = {
  label: string
  value: string | null
  confirmed: boolean
  onConfirm: (value: string) => void
}

export default function FieldEditor({ label, value, confirmed, onConfirm }: Props) {
  const [draft, setDraft] = useState(value ?? '')
  const [editing, setEditing] = useState(false)

  return (
    <div className="field-editor">
      <div className="field-heading">
        <span>{label.replaceAll('_', ' ')}</span>
        <small>{confirmed ? 'User confirmed' : 'Not confirmed'}</small>
      </div>

      {!editing ? (
        <div className="field-value">
          <span>{value ?? 'Unknown'}</span>
          <button
            type="button"
            className="remove-file"
            onClick={() => {
              setDraft(value ?? '')
              setEditing(true)
            }}
          >
            Edit / confirm
          </button>
        </div>
      ) : (
        <form onSubmit={event => {
          event.preventDefault()
          if (!draft.trim()) return
          onConfirm(draft.trim())
          setEditing(false)
        }}>
          <label>
            {label.replaceAll('_', ' ')}
            <input
              className="field-input"
              value={draft}
              onChange={event => setDraft(event.target.value)}
              required
              type={label === 'purchase_date' ? 'date' : 'text'}
              inputMode={label === 'purchase_price' ? 'decimal' : undefined}
            />
          </label>
          <button type="submit">Confirm this field</button>
          <button
            type="button"
            className="remove-file"
            onClick={() => setEditing(false)}
          >
            Cancel
          </button>
        </form>
      )}
    </div>
  )
}

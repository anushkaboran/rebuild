import { useId, useState } from 'react'

type Props = {
  label: string
  value: string | null
  confirmed: boolean
  onConfirm: (value: string) => void
}

const fieldLabels: Record<string, string> = {
  name: 'Item name',
  brand: 'Brand',
  model: 'Model',
  purchase_date: 'Purchase date',
  purchase_price: 'Purchase price',
  currency: 'Currency',
}

export default function FieldEditor({ label, value, confirmed, onConfirm }: Props) {
  const [draft, setDraft] = useState(value ?? '')
  const [editing, setEditing] = useState(false)
  const inputId = useId()
  const displayLabel = fieldLabels[label] ?? label.replaceAll('_', ' ')
  const isPrice = label === 'purchase_price'
  const isCurrency = label === 'currency'

  return (
    <div className={`field-editor${editing ? ' is-editing' : ''}`}>
      <div className="field-heading">
        <span>{displayLabel}</span>
        <small className={confirmed ? 'is-confirmed' : ''}>{confirmed ? '✓ Confirmed' : 'Unconfirmed'}</small>
      </div>

      {!editing ? (
        <div className="field-value">
          <span className={value ? '' : 'field-missing'}>{value || 'Not provided'}</span>
          <button
            type="button"
            className="button-quiet field-edit-button"
            aria-label={`${value ? 'Edit or confirm' : 'Add'} ${displayLabel.toLowerCase()}`}
            onClick={() => {
              setDraft(value ?? '')
              setEditing(true)
            }}
          >
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m10.5 2.5 3 3M2 14l3.5-.5 8-8a2.12 2.12 0 0 0-3-3l-8 8L2 14Z" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
            {value ? 'Edit' : 'Add'}
          </button>
        </div>
      ) : (
        <form onSubmit={event => {
          event.preventDefault()
          if (!draft.trim()) return
          onConfirm(isCurrency ? draft.trim().toUpperCase() : draft.trim())
          setEditing(false)
        }}>
          <label className="field-input-label" htmlFor={inputId}>{displayLabel}</label>
          <input
            id={inputId}
            className="field-input"
            value={draft}
            onChange={event => setDraft(event.target.value)}
            required
            autoFocus
            type={label === 'purchase_date' ? 'date' : isPrice ? 'number' : 'text'}
            min={isPrice ? '0.01' : undefined}
            step={isPrice ? '0.01' : undefined}
            inputMode={isPrice ? 'decimal' : undefined}
            maxLength={isCurrency ? 3 : undefined}
            pattern={isCurrency ? '[A-Za-z]{3}' : undefined}
            placeholder={isCurrency ? 'e.g. USD, EUR, INR' : undefined}
            autoCapitalize={isCurrency ? 'characters' : undefined}
            aria-describedby={isCurrency ? `${inputId}-hint` : undefined}
          />
          {isCurrency && <p id={`${inputId}-hint`} className="field-hint">Enter the three-letter currency code for this price.</p>}
          <div className="field-actions">
            <button type="submit" className="button-primary">Save &amp; confirm</button>
            <button type="button" className="button-quiet" onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </form>
      )}
    </div>
  )
}

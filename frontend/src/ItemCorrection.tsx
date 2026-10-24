import { useRef, useState } from 'react'

type EditableItem = {
  item_id: string
  revision: number
  name: string
  currency: string | null
  condition?: string | null
  estimated_value?: number | null
}

export default function ItemCorrection({
  item,
  onSaved,
}: {
  item: EditableItem
  onSaved: () => Promise<void>
}) {
  const [name, setName] = useState(item.name)
  const [currency, setCurrency] = useState(item.currency ?? '')
  const [condition, setCondition] = useState(item.condition ?? '')
  const [estimate, setEstimate] = useState(
    item.estimated_value == null ? '' : String(item.estimated_value),
  )
  const [editor, setEditor] = useState('Lasya')
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState(false)
  const busy = useRef(false)

  async function save() {
    if (busy.current || conflict) return
    setError('')

    const changes: Record<string, string | number> = {}
    if (name.trim() !== item.name) changes.name = name.trim()

    const currencyValue = currency.trim().toUpperCase()
    if (currencyValue !== (item.currency ?? '')) {
      if (!/^[A-Z]{3}$/.test(currencyValue)) {
        setError('Enter a three-letter currency code, such as USD.')
        return
      }
      changes.currency = currencyValue
    }

    if (condition.trim() !== (item.condition ?? '')) {
      changes.condition = condition.trim()
    }

    const originalEstimate =
      item.estimated_value == null ? '' : String(item.estimated_value)
    if (estimate !== originalEstimate) {
      if (estimate.trim() === '' || !Number.isFinite(Number(estimate)) || Number(estimate) < 0) {
        setError('Enter a non-negative estimated value.')
        return
      }
      changes.estimated_value = Number(estimate)
    }

    if (!Object.keys(changes).length) {
      setError('Change a field before saving.')
      return
    }
    if (!name.trim() || !editor.trim()) {
      setError('Item name and reviewer name are required.')
      return
    }

    busy.current = true
    setSaving(true)
    try {
      const response = await fetch(
        `/api/items/${encodeURIComponent(item.item_id)}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ...changes,
            editor: editor.trim(),
            note: note.trim() || undefined,
            if_revision: item.revision,
          }),
        },
      )

      if (response.status === 409) {
        const body = await response.json()
        setConflict(true)
        setError(
          body.detail?.message ??
          'Another reviewer changed this item. Refresh the inventory before editing again.',
        )
        return
      }
      if (!response.ok) {
        throw new Error(`Could not save (${response.status}). Your draft is still here.`)
      }

      await onSaved()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save.')
    } finally {
      busy.current = false
      setSaving(false)
    }
  }

  return (
    <details className="item-correction">
      <summary>Edit this item</summary>
      <form onSubmit={event => {
        event.preventDefault()
        void save()
      }}>
        <fieldset disabled={saving || conflict}>
          <label>
            Item name
            <input value={name} maxLength={200} required
              onChange={event => setName(event.target.value)} />
          </label>
          <label>
            Currency
            <input value={currency} maxLength={3} placeholder="Unknown"
              onChange={event => setCurrency(event.target.value)} />
          </label>
          <label>
            Observed condition
            <textarea value={condition} maxLength={1000}
              onChange={event => setCondition(event.target.value)} />
          </label>
          <label>
            Your estimated value
            <input type="number" min="0" step="any" value={estimate}
              placeholder="Unknown"
              onChange={event => setEstimate(event.target.value)} />
          </label>
          <p className="helper">
            Your estimate is separate from the receipt purchase price.
          </p>
          <label>
            Reviewer name
            <input value={editor} required maxLength={120}
              onChange={event => setEditor(event.target.value)} />
          </label>
          <label>
            Reason for this correction
            <textarea value={note} maxLength={1000}
              onChange={event => setNote(event.target.value)} />
          </label>
          <button type="submit">
            {saving ? 'Saving…' : 'Save correction'}
          </button>
        </fieldset>
        {error && <p role="alert">{error}</p>}
        {conflict && (
          <p>Copy your draft, then use Refresh case inventory to review the latest record.</p>
        )}
      </form>
    </details>
  )
}

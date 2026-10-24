import SavedCases from './SavedCases'
import ConnectionStatus from './ConnectionStatus'
import Inventory from './Inventory'

import { useState } from 'react'
import './App.css'
import EvidenceUpload from './EvidenceUpload'


function App() {
  const [description, setDescription] = useState('')
  const [room, setRoom] = useState('Living room')
  const [started, setStarted] = useState(false)
  const [caseId, setCaseId] = useState('')
  const [creating, setCreating] = useState(false)
  const [caseError, setCaseError] = useState('')

  async function createCase() {
    if (creating) return
    if (!description.trim()) {
      setCaseError('Please describe the incident.')
      return
    }

    setCreating(true)
    setCaseError('')

    try {
      const response = await fetch('/api/cases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: room + ' recovery record',
          disaster_type: 'flood',
          notes: description.trim(),
          created_by: 'Lasya',
        }),
      })

      if (!response.ok) {
        throw new Error('Case creation failed: HTTP ' + response.status)
      }

      const created = await response.json()
      if (typeof created.id !== 'string') {
        throw new Error('Unexpected response from backend.')
      }

      setCaseId(created.id)
      setStarted(true)
    } catch (error) {
      setCaseError(error instanceof Error ? error.message : 'Cannot reach backend.')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="app">
      <header className="header">
        <a className="brand" href="/">rebuild<span>.</span></a>
        <ConnectionStatus />
      </header>

      <main className="welcome">
        <div className="setup-layout"><div className="setup-copy"><p className="eyebrow">YOUR RECOVERY WORKSPACE</p>
        <h1>Rebuild your record.<br /><span>One room at a time.</span></h1>
        <p className="intro">
          Turn scattered photos and receipts into an organized inventory.
          Review the details, fill the gaps, and keep the evidence together.
        </p>

        <ol className="setup-steps">
            <li><span>01</span><div><strong>Add your evidence</strong><p>Room photos, close-ups, and receipts.</p></div></li>
            <li><span>02</span><div><strong>Review what we find</strong><p>Check proposed items and connections.</p></div></li>
            <li><span>03</span><div><strong>Build your recovery record</strong><p>Keep confirmed details linked to their sources.</p></div></li>
          </ol>
        </div><form
          className="case-form"
          onSubmit={(event) => {
            event.preventDefault()
            void createCase()
          }}
        >
          <p className="eyebrow">LET’S GET STARTED</p><h2>Tell us about your room</h2>

          <label htmlFor="description">What happened?</label>
          <textarea
            id="description"
            placeholder="Briefly describe the incident and what you want to document."
            value={description}
            onChange={(event) => {
              setDescription(event.target.value)
              setStarted(false)
            }}
            required
          />

          <label htmlFor="room">Room</label>
          <select
            id="room"
            value={room}
            onChange={(event) => {
              setRoom(event.target.value)
              setStarted(false)
            }}
          >
            <option>Living room</option>
            <option>Bedroom</option>
            <option>Kitchen</option>
            <option>Other</option>
          </select>

          <button type="submit" disabled={creating || started}>
            {creating ? 'Creating case…' : started ? 'Case created' : 'Continue →'}
          </button>
          {caseError && <p role="alert">{caseError}</p>}
          <p className="helper">Start with one room. You can review every proposed detail.</p>

          {started && (
            <p className="notice" role="status">
              Case created: {caseId}. Your description is saved in the local backend.
              Files have not been uploaded. Restarting the backend in memory
              mode clears the case.
            </p>
          )}
        </form></div>{started && <EvidenceUpload key={caseId} caseId={caseId} room={room} />}
      <SavedCases />
      <Inventory /></main>
    </div>
  )
}

export default App

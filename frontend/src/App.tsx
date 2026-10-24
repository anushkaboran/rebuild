import Inventory from './Inventory'

import { useState } from 'react'
import './App.css'
import EvidenceUpload from './EvidenceUpload'


function App() {
  const [description, setDescription] = useState('')
  const [room, setRoom] = useState('Living room')
  const [started, setStarted] = useState(false)

  return (
    <div className="app">
      <header className="header">
        <a className="brand" href="/">rebuild<span>.</span></a>
        <span className="prototype">Local prototype</span>
      </header>

      <main className="welcome">
        <p className="eyebrow">ONE STEP AT A TIME</p>
        <h1>A little order.<br />A way forward.</h1>
        <p className="intro">
          Bring your photos and receipts together. Build a clear record
          of your belongings, with evidence behind every detail.
        </p>

        <form
          className="case-form"
          onSubmit={(event) => {
            event.preventDefault()
            setStarted(true)
          }}
        >
          <h2>Start your recovery record</h2>

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

          <label htmlFor="room">Which room are we starting with?</label>
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

          <button type="submit">Continue →</button>
          <p className="helper">Start with one room. You can review every proposed detail.</p>

          {started && (
            <p className="notice" role="status">
              Your {room.toLowerCase()} draft is ready in this page.
              Uploads come next. Nothing has been sent or saved to a server.
            </p>
          )}
        </form>{started && <EvidenceUpload />}
      <Inventory /></main>
    </div>
  )
}

export default App

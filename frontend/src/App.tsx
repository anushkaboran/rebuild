import { useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { MotionConfig } from 'motion/react'
import EvidenceUpload from './EvidenceUpload'
import Inventory from './Inventory'
import './App.css'

type View = 'overview' | 'evidence' | 'inventory' | 'review'

function Icon({ name, ...props }: { name: string; className?: string }) {
  const paths: Record<string, ReactNode> = {
    home: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1Z" /><path d="M9 21v-8h6v8" /></>,
    image: <><rect x="3" y="3" width="18" height="18" rx="3" /><circle cx="8" cy="8" r="1.5" /><path d="m21 15-5-5L5 21" /></>,
    grid: <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
    review: <><path d="M9 4H6a2 2 0 0 0-2 2v14a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V6a2 2 0 0 0-2-2h-3" /><rect x="8" y="2" width="8" height="4" rx="1" /><path d="m8 13 3 3 5-6" /></>,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    lock: <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2" /></>,
    help: <><circle cx="12" cy="12" r="9" /><path d="M9.5 9a2.5 2.5 0 1 1 4 2c-1.5 1-1.5 1.5-1.5 2.5m0 3h.01" /></>,
    sun: <><path d="M4 20h16M6 16a6 6 0 0 1 12 0M12 3v3M3 8l2 2m16-2-2 2M2 16h2m16 0h2" /></>,
    chevron: <path d="m9 6 6 6-6 6" />,
  }
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name] ?? paths.home}</svg>
}

const navItems: { id: View; label: string; icon: string }[] = [
  { id: 'overview', label: 'Overview', icon: 'home' },
  { id: 'evidence', label: 'Evidence', icon: 'image' },
  { id: 'inventory', label: 'Inventory', icon: 'grid' },
  { id: 'review', label: 'Review', icon: 'review' },
]

function RoomIllustration() {
  return (
    <svg className="room-illustration" viewBox="0 0 320 210" fill="none" role="img" aria-label="A peaceful room with an armchair, a plant, and sunlight through a window">
      <circle cx="163" cy="105" r="88" fill="#e0e8da" />
      <path d="M29 174h262" stroke="#8c9d88" strokeWidth="1.5" />
      <path d="M76 45h70v77H76z" fill="#f8faf4" stroke="#9aab92" strokeWidth="1.5" />
      <path d="M111 45v77m-35-39h70" stroke="#9aab92" strokeWidth="1.5" />
      <path d="m119 53 20 20m-20-10 11 11" stroke="#e1e7d9" strokeWidth="3" strokeLinecap="round" />
      <path d="M118 133V109a14 14 0 0 1 14-14h42a14 14 0 0 1 14 14v24" fill="#96ac8c" stroke="#6e8666" strokeWidth="1.5" />
      <rect x="107" y="124" width="92" height="36" rx="11" fill="#b6c5a7" stroke="#6e8666" strokeWidth="1.5" />
      <path d="M117 159v15m70-15v15M121 141h64" stroke="#6e8666" strokeWidth="2" strokeLinecap="round" />
      <rect x="133" y="111" width="25" height="23" rx="5" transform="rotate(-8 133 111)" fill="#eee7cc" stroke="#9d9d7d" strokeWidth="1.2" />
      <path d="M215 146h27l-4 28h-19z" fill="#d4b89a" stroke="#ab9177" strokeWidth="1.4" />
      <path d="M228 146v-40m0 22c-20 1-23-16-23-16 20-3 23 16 23 16Zm1-11c18 0 20-17 20-17-18 0-20 17-20 17Zm-1-9c-12-7-8-21-8-21 15 6 8 21 8 21Z" fill="#8eaa81" stroke="#6e8666" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M64 157h26m-22 0v17m18-17v17" stroke="#a68f73" strokeWidth="2" strokeLinecap="round" />
      <path d="M72 157v-9h10v9" fill="#eee7cc" stroke="#a68f73" strokeWidth="1.3" />
      <path d="M247 60v-9m-5 4h10m-64-18v-7m-3.5 3.5h7" stroke="#aeb89b" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  )
}

function App() {
  const [view, setView] = useState<View>('overview')
  const [description, setDescription] = useState('')
  const [room, setRoom] = useState('Living room')
  const [started, setStarted] = useState(false)
  const [descriptionError, setDescriptionError] = useState(false)
  const [fileCount, setFileCount] = useState(0)
  const [files, setFiles] = useState<File[]>([])
  const [demoRequested, setDemoRequested] = useState(false)
  const [demoRequestId, setDemoRequestId] = useState(0)
  const mainRef = useRef<HTMLElement>(null)
  const helpRef = useRef<HTMLDialogElement>(null)

  function navigate(next: View) {
    setView(next)
    window.scrollTo({ top: 0, behavior: 'instant' })
    mainRef.current?.focus()
  }

  function openDemo() {
    setDemoRequested(true)
    setDemoRequestId(current => current + 1)
    navigate('inventory')
  }

  return (
    <MotionConfig reducedMotion="user"><div className="app-shell">
      <a href="#main-content" className="skip-link">Skip to workspace</a>
      <aside className="sidebar" aria-label="Workspace navigation">
        <a className="brand" href="#overview" onClick={event => { event.preventDefault(); navigate('overview') }}><span className="brand-mark"><Icon name="home" /></span>rebuild<span className="brand-dot">.</span></a>
        <div className="workspace-label"><span className="workspace-avatar">R</span><div>Your recovery workspace<small>One room at a time</small></div></div>
        <p className="nav-caption">WORKSPACE</p>
        <nav className="main-nav">
          {navItems.map(item => <button key={item.id} className={`nav-item ${view === item.id ? 'is-active' : ''}`} aria-current={view === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><Icon name={item.icon} /><span>{item.label}</span>{item.id === 'evidence' && fileCount > 0 && <span className="nav-count">{fileCount}</span>}{view === item.id && <span className="nav-active-dot" />}</button>)}
        </nav>
        {started && <div className="sidebar-room"><p className="nav-caption">YOUR DRAFT</p><div><Icon name="home" /><span>{room}<small>In this session</small></span></div></div>}
        <div className="sidebar-bottom"><div className="encouragement"><Icon name="sun" /><strong>A fresh start takes small steps.</strong><p>Start with what you have. You can add the details as you go.</p></div><div className="session-label"><span className="session-avatar">Y</span><div>Your workspace<small>Local prototype</small></div><span className="session-status" aria-label="Local session" /></div></div>
      </aside>

      <div className="app-main">
        <header className="topbar"><div className="breadcrumb">Workspace<Icon name="chevron" /><span>{navItems.find(item => item.id === view)?.label}</span></div><div className="topbar-actions"><span className="local-pill"><span />Browser session</span><button className="help-button" onClick={() => helpRef.current?.showModal()} aria-label="How rebuild works"><Icon name="help" /><span>How it works</span></button></div></header>
        <main ref={mainRef} id="main-content" className="main-content" tabIndex={-1}>
          <div hidden={view !== 'overview'}>
            <div className="page-heading"><div><p className="eyebrow">A LITTLE CLARITY. A FRESH START.</p><h1>Let's take it <em>one room at a time.</em></h1><p>Bring your photos, receipts, and memories together. We'll help you organize the details.</p></div><span className="gentle-badge"><Icon name="sun" />At your own pace</span></div>
            <div className="overview-layout">
              <section className="setup-card" aria-labelledby="setup-title">
                <div className="setup-progress"><span className="step-dot current">1</span><span>Room details</span><span className="step-line" /><span className={`step-dot ${started ? 'current' : ''}`}>2</span><span className="step-next">Add evidence</span></div>
                <div className="setup-card-title"><span className="section-icon"><Icon name="home" /></span><div><h2 id="setup-title">Start with a room</h2><p>A little context is all you need to begin.</p></div></div>
                <form onSubmit={event => { event.preventDefault(); if (!description.trim()) { setDescriptionError(true); document.getElementById('description')?.focus(); return }; setDescriptionError(false); setStarted(true); navigate('evidence') }}>
                  <label htmlFor="room">Which room are we documenting?</label>
                  <select id="room" value={room} onChange={event => setRoom(event.target.value)}><option>Living room</option><option>Bedroom</option><option>Kitchen</option><option>Dining room</option><option>Home office</option><option>Other</option></select>
                  <label htmlFor="description">Tell us a little about what happened <span className="required-label">Required</span></label>
                  <textarea id="description" value={description} onChange={event => { setDescription(event.target.value); setDescriptionError(false) }} placeholder="For example: A water leak affected our living room. I'd like to document the furniture and electronics." required maxLength={3000} aria-invalid={descriptionError || undefined} aria-describedby={descriptionError ? 'description-error' : 'description-hint'} />
                  {descriptionError && <p className="form-error" id="description-error" role="alert">Add a few words about what you'd like to document.</p>}
                  <p className="input-hint" id="description-hint">A short description is enough. You can come back and update it.</p>
                  <div className="form-footer"><span><Icon name="lock" />Draft stays in this browser session</span><button type="submit" className="button-primary">{started ? 'Continue to evidence' : 'Create room draft'}<Icon name="arrow" /></button></div>
                </form>
              </section>
              <aside className="guide-card" id="getting-started"><div className="guide-top"><span className="eyebrow">FROM SCATTERED TO SORTED</span><RoomIllustration /></div><div className="guide-body"><h2>A clearer picture,<br />one detail at a time.</h2><ol className="guide-steps"><li><span>01</span><div><strong>Gather your evidence</strong><p>Room photos, close-ups, and receipts.</p></div></li><li><span>02</span><div><strong>Make sense of the details</strong><p>Explore item records and their sources.</p></div></li><li><span>03</span><div><strong>Review with confidence</strong><p>Confirm what you know. Fill in the gaps.</p></div></li></ol></div></aside>
            </div>
            <section className="sample-invitation"><span className="sample-icon"><Icon name="grid" /></span><div><h2>Want to look around first?</h2><p>Explore a sample living room inventory. Your draft and files stay separate.</p></div><button className="button-secondary" onClick={openDemo}>Explore sample<Icon name="arrow" /></button></section>
            <div className="workspace-footnote"><Icon name="lock" /><p>This prototype keeps your work in this page. Refreshing clears your draft, selected files, and edits.</p></div>
          </div>
          <div hidden={view !== 'evidence'}>
            <div className="page-heading"><div><p className="eyebrow">YOUR EVIDENCE, TOGETHER</p><h1>Start with <em>what you have.</em></h1><p>{started ? `Gather photos and receipts for your ${room.toLowerCase()} draft.` : 'Add room photos, close-ups, or receipts. You can create your room draft in Overview.'}</p></div><span className="gentle-badge"><Icon name="image" />{fileCount} {fileCount === 1 ? 'file' : 'files'} selected</span></div>
            {started && <div className="room-context"><span className="section-icon"><Icon name="home" /></span><div><strong>{room}</strong><p>{description}</p></div><button className="button-quiet" onClick={() => navigate('overview')}>Edit details</button></div>}
            <EvidenceUpload room={started ? room : undefined} onExploreDemo={openDemo} onCountChange={setFileCount} onFilesChange={setFiles} onCreateItems={() => navigate('inventory')} />
          </div>
          <div hidden={view !== 'inventory' && view !== 'review'}><Inventory activeView={view === 'review' ? 'review' : 'inventory'} demoRequested={demoRequested} demoRequestId={demoRequestId} onDemoRequest={openDemo} files={files} onAddEvidence={() => navigate('evidence')} /></div>
        </main>
        <footer className="app-footer"><span>Made for the next chapter.</span><span>rebuild.</span></footer>
      </div>
      <dialog className="help-dialog" ref={helpRef} aria-labelledby="help-title"><button className="dialog-close button-quiet" onClick={() => helpRef.current?.close()} aria-label="Close help">×</button><span className="section-icon"><Icon name="sun" /></span><p className="eyebrow">A PLACE TO BEGIN</p><h2 id="help-title">A little progress goes a long way.</h2><p>Create a room draft, gather your evidence, then explore how item records and review questions fit together in the sample inventory.</p><ol><li><strong>Overview</strong> — add the room and a brief description.</li><li><strong>Evidence</strong> — select and preview files locally.</li><li><strong>Inventory & review</strong> — try the sample, edit details, and answer questions.</li></ol><p className="notice">This is a local prototype. Files aren't uploaded or processed, and changes clear when you refresh.</p><button className="button-primary" onClick={() => helpRef.current?.close()}>Got it<Icon name="check" /></button></dialog>
    </div></MotionConfig>
  )
}

export default App

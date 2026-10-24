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
        <div className="workspace-label"><span className="workspace-avatar">R</span><div>Your workspace</div></div>
        <p className="nav-caption">WORKSPACE</p>
        <nav className="main-nav">
          {navItems.map(item => <button key={item.id} className={`nav-item ${view === item.id ? 'is-active' : ''}`} aria-current={view === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><Icon name={item.icon} /><span>{item.label}</span>{item.id === 'evidence' && fileCount > 0 && <span className="nav-count">{fileCount}</span>}{view === item.id && <span className="nav-active-dot" />}</button>)}
        </nav>
        {started && <div className="sidebar-room"><p className="nav-caption">YOUR DRAFT</p><div><Icon name="home" /><span>{room}<small>In this session</small></span></div></div>}
        <div className="sidebar-bottom"><div className="session-label"><span className="session-avatar">Y</span><div>Your workspace<small>Local session</small></div><span className="session-status" aria-label="Local session" /></div></div>
      </aside>

      <div className="app-main">
        <header className="topbar"><div className="breadcrumb">Workspace<Icon name="chevron" /><span>{navItems.find(item => item.id === view)?.label}</span></div><span className="local-pill"><span />Local session</span></header>
        <main ref={mainRef} id="main-content" className="main-content" tabIndex={-1}>
          <div hidden={view !== 'overview'}>
            <div className="page-heading"><div><p className="eyebrow">YOUR RECOVERY WORKSPACE</p><h1>Rebuild your <span>record.</span></h1></div><button className="button-secondary" onClick={openDemo}>Open sample<Icon name="arrow" /></button></div>
            <div className="overview-layout">
              <section className="setup-card" aria-labelledby="setup-title">
                <div className="setup-progress"><span className="step-dot current">1</span><span>Room details</span><span className="step-line" /><span className={`step-dot ${started ? 'current' : ''}`}>2</span><span className="step-next">Add evidence</span></div>
                <div className="setup-card-title"><span className="section-icon"><Icon name="home" /></span><div><h2 id="setup-title">Room details</h2></div></div>
                <form onSubmit={event => { event.preventDefault(); if (!description.trim()) { setDescriptionError(true); document.getElementById('description')?.focus(); return }; setDescriptionError(false); setStarted(true); navigate('evidence') }}>
                  <label htmlFor="room">Room</label>
                  <select id="room" value={room} onChange={event => setRoom(event.target.value)}><option>Living room</option><option>Bedroom</option><option>Kitchen</option><option>Dining room</option><option>Home office</option><option>Other</option></select>
                  <label htmlFor="description">What happened? <span className="required-label">Required</span></label>
                  <textarea id="description" value={description} onChange={event => { setDescription(event.target.value); setDescriptionError(false) }} placeholder="Describe the incident and what you want to document." required maxLength={3000} aria-invalid={descriptionError || undefined} aria-describedby={descriptionError ? 'description-error' : undefined} />
                  {descriptionError && <p className="form-error" id="description-error" role="alert">Add a few words about what you'd like to document.</p>}
                  <div className="form-footer"><button type="submit" className="button-primary">{started ? 'Continue to evidence' : 'Create room draft'}<Icon name="arrow" /></button></div>
                </form>
              </section>
            </div>
          </div>
          <div hidden={view !== 'evidence'}>
            <div className="page-heading"><div><p className="eyebrow">EVIDENCE</p><h1>Add your <span>evidence.</span></h1></div><span className="file-count-badge">{fileCount} {fileCount === 1 ? 'file' : 'files'} selected</span></div>
            {started && <div className="room-context"><span className="section-icon"><Icon name="home" /></span><div><strong>{room}</strong><p>{description}</p></div><button className="button-quiet" onClick={() => navigate('overview')}>Edit details</button></div>}
            <EvidenceUpload room={started ? room : undefined} onExploreDemo={openDemo} onCountChange={setFileCount} onFilesChange={setFiles} onCreateItems={() => navigate('inventory')} />
          </div>
          <div hidden={view !== 'inventory' && view !== 'review'}><Inventory activeView={view === 'review' ? 'review' : 'inventory'} demoRequested={demoRequested} demoRequestId={demoRequestId} onDemoRequest={openDemo} files={files} onAddEvidence={() => navigate('evidence')} /></div>
        </main>
        <footer className="app-footer"><span>Session only. Refresh clears your draft, files, and edits.</span><span>rebuild.</span></footer>
      </div>

    </div></MotionConfig>
  )
}

export default App

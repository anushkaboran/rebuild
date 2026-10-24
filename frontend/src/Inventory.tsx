import { useEffect, useRef, useState } from 'react'
import { Disclosure, DisclosureContent, DisclosureTrigger } from './components/core/disclosure'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle } from './components/core/dialog'
import { TransitionPanel } from './components/core/transition-panel'
import FieldEditor from './FieldEditor'
import ReviewFindings from './ReviewFindings'
import type { Finding, Question } from './ReviewFindings'
import { sampleImages } from './sampleImages'
import './Inventory.css'

type Item = {
  item_id: string
  name: string
  room: string
  location_in_image: string
  visible_observations: string
  supporting_evidence_ids: string[]
  brand: string | null
  model: string | null
  purchase_date: string | null
  purchase_price: string | null
  currency: string | null
  user_confirmed_fields: string[]
}

type Evidence = {
  evidence_id: string
  filename: string
  description: string
  evidence_type?: string
}

type Result = {
  findings: Finding[]
  questions: Question[]
  items: Item[]
  evidence: Evidence[]
}

type Props = {
  activeView: 'inventory' | 'review'
  demoRequested: boolean
  demoRequestId?: number
  onDemoRequest: () => void
  files?: File[]
  onAddEvidence?: () => void
}

type IconName = 'search' | 'arrow' | 'file' | 'layers' | 'review' | 'info' | 'check' | 'image'

function InventoryIcon({ name, className = '' }: { name: IconName; className?: string }) {
  return (
    <svg className={className} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {name === 'search' && <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4.5 4.5" /></>}
      {name === 'arrow' && <><path d="M5 12h14m-5-5 5 5-5 5" /></>}
      {name === 'file' && <><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z" /><path d="M14 3v5h5M9 12h6m-6 4h6" /></>}
      {name === 'layers' && <><path d="m12 3 9 5-9 5-9-5zM3 12l9 5 9-5M3 16l9 5 9-5" /></>}
      {name === 'review' && <><path d="M8 5H5v16h14V5h-3M9 3h6v4H9zM8 12h8m-8 4h5" /></>}
      {name === 'info' && <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>}
      {name === 'check' && <path d="m5 12 4 4L19 6" />}
      {name === 'image' && <><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8" cy="8" r="1.5" /><path d="m3 17 5-5 4 4 3-3 6 6" /></>}

    </svg>
  )
}

const editableFields = ['name', 'brand', 'model', 'purchase_date', 'purchase_price', 'currency'] as const

function PhotoPreview({ file, className = '' }: { file: File; className?: string }) {
  const imageRef = useRef<HTMLImageElement>(null)
  const [failedFile, setFailedFile] = useState<File | null>(null)
  useEffect(() => {
    const image = imageRef.current
    if (!image) return
    const url = URL.createObjectURL(file)
    image.src = url
    return () => {
      image.removeAttribute('src')
      URL.revokeObjectURL(url)
    }
  }, [file])
  return failedFile === file
    ? <div className="inventory-photo-unavailable">The preview isn’t available for this image format. Your photo is still attached.</div>
    : <img ref={imageRef} className={className} alt={`Selected photo: ${file.name}`} onError={() => setFailedFile(file)} />
}

function fileKey(file: File) {
  return `${file.name}:${file.size}:${file.lastModified}`
}

function isImageFile(file: File) {
  return file.type ? file.type.startsWith('image/') : /\.(jpe?g|png)$/i.test(file.name)
}

function validatePhoto(file: File) {
  if (!isImageFile(file)) return 'Choose an image file, such as a JPG or PNG.'
  if (file.size > 10 * 1024 * 1024) return 'Choose an image smaller than 10 MB.'
  return ''
}

const panelNames = ['details', 'evidence', 'review'] as const

export default function Inventory({ activeView, demoRequested, demoRequestId = 0, onDemoRequest, files = [], onAddEvidence }: Props) {
  const [panel, setPanel] = useState<(typeof panelNames)[number]>('details')
  const [resolutions, setResolutions] = useState<Record<string, string>>({})
  const [corrections, setCorrections] = useState<Record<string, Record<string, string>>>({})
  const [result, setResult] = useState<Result | null>(null)
  const [scope, setScope] = useState<'yours' | 'sample' | null>(null)
  const [lastDemoRequestId, setLastDemoRequestId] = useState(demoRequestId)
  const [userItems, setUserItems] = useState<Item[]>([])
  const [userEvidence, setUserEvidence] = useState<Evidence[]>([])
  const [assignedPhotos, setAssignedPhotos] = useState<Record<string, File | null>>({})
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [itemOpen, setItemOpen] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [room, setRoom] = useState('all')
  const [error, setError] = useState('')
  const [retryCount, setRetryCount] = useState(0)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newRoom, setNewRoom] = useState('Living room')
  const [newNotes, setNewNotes] = useState('')
  const [newPhoto, setNewPhoto] = useState<File | null>(null)
  const [photoErrors, setPhotoErrors] = useState<Record<string, string>>({})
  const [createError, setCreateError] = useState('')
  const imageFiles = files.filter(isImageFile)
  const currentScope = scope ?? (demoRequested ? 'sample' : 'yours')
  const isSample = currentScope === 'sample'

  // A new explicit sample request can arrive from Overview or Evidence.
  if (demoRequestId !== lastDemoRequestId) {
    setLastDemoRequestId(demoRequestId)
    setScope('sample')
    setSearch('')
    setRoom('all')
    setExpandedId(null)
  }

  useEffect(() => {
    if (!demoRequested) return
    const controller = new AbortController()
    async function loadDemo() {
      try {
        const response = await fetch('/demo-result.json', { signal: controller.signal })
        if (!response.ok) throw new Error('The sample could not be loaded.')
        const data: Result = await response.json()
        if (!Array.isArray(data.items) || !Array.isArray(data.evidence) ||
          !Array.isArray(data.findings) || !Array.isArray(data.questions)) {
          throw new Error('The sample is missing inventory records.')
        }
        if (!controller.signal.aborted) setResult(data)
      } catch {
        if (!controller.signal.aborted) setError('We couldn’t load the sample inventory. Please try again.')
      }
    }
    void loadDemo()
    return () => controller.abort()
  }, [demoRequested, retryCount])

  const records: Result | null = isSample ? result : { items: userItems, evidence: userEvidence, findings: [], questions: [] }
  const loading = isSample && demoRequested && !result && !error
  const displayedItems = records?.items.map(item => ({
    ...item,
    ...(corrections[item.item_id] ?? {}),
    user_confirmed_fields: [...new Set([...item.user_confirmed_fields, ...Object.keys(corrections[item.item_id] ?? {})])],
  })) ?? []
  const rooms = [...new Set(displayedItems.map(item => item.room))]
  const query = search.trim().toLowerCase()
  const filteredItems = displayedItems.filter(item =>
    (room === 'all' || item.room === room) &&
    (!query || [item.name, item.room, item.brand, item.model].some(value => value?.toLowerCase().includes(query))),
  )
  const selected = displayedItems.find(item => item.item_id === selectedId)
  const isResolved = (id: string, status: string) => status === 'resolved' || Boolean(resolutions[id])
  const openFindings = records?.findings.filter(finding => !isResolved(finding.finding_id, finding.status)) ?? []
  const openQuestions = records?.questions.filter(question => !isResolved(question.question_id, question.status)) ?? []
  const openTaskCount = openFindings.length + openQuestions.length
  const confirmedFieldCount = displayedItems.reduce((count, item) => count + item.user_confirmed_fields.length, 0)
  const itemTaskCount = (id: string) => openFindings.filter(finding => finding.item_ids.includes(id)).length + openQuestions.filter(question => question.item_id === id).length

  function photoFor(item: Item): File | null {
    if (Object.hasOwn(assignedPhotos, item.item_id)) return assignedPhotos[item.item_id]
    const filenames = item.supporting_evidence_ids.map(id => records?.evidence.find(entry => entry.evidence_id === id)?.filename)
    return imageFiles.find(file => filenames.includes(file.name)) ?? null
  }

  function sampleImageFor(item: Item) {
    if (!isSample || photoFor(item) || Object.hasOwn(assignedPhotos, item.item_id)) return undefined
    return sampleImages[item.item_id]
  }

  const selectedPhoto = selected ? photoFor(selected) : null
  const selectedSampleImage = selected ? sampleImageFor(selected) : undefined

  function chooseScope(next: 'yours' | 'sample') {
    setScope(next)
    setSearch('')
    setRoom('all')
    setExpandedId(null)
    if (next === 'sample') onDemoRequest()
  }

  function openItem(item: Item) {
    setSelectedId(item.item_id)
    setItemOpen(true)
    setPanel(activeView === 'review' && isSample ? 'review' : 'details')
  }

  function confirmField(field: string, value: string) {
    if (!selected) return
    const id = selected.item_id
    setCorrections(current => ({ ...current, [id]: { ...(current[id] ?? {}), [field]: value } }))
  }

  function undoResolution(id: string) {
    setResolutions(current => {
      const next = { ...current }
      delete next[id]
      return next
    })
  }

  function assignPhoto(item: Item, file: File | null) {
    const error = file ? validatePhoto(file) : ''
    setPhotoErrors(current => ({ ...current, [item.item_id]: error }))
    if (error) return
    setAssignedPhotos(current => ({ ...current, [item.item_id]: file }))
    if (!item.item_id.startsWith('local_')) return
    const evidenceId = `${item.item_id}_photo`
    setUserItems(current => current.map(entry => entry.item_id === item.item_id ? { ...entry, supporting_evidence_ids: file ? [evidenceId] : [] } : entry))
    setUserEvidence(current => [...current.filter(entry => entry.evidence_id !== evidenceId), ...(file ? [{ evidence_id: evidenceId, filename: file.name, evidence_type: 'photo', description: 'Photo explicitly selected by you for this item. No automatic observations or verification have been created.' }] : [])])
  }

  function chooseNewPhoto(file: File | null) {
    const error = file ? validatePhoto(file) : ''
    setCreateError(error)
    if (!error) setNewPhoto(file)
  }

  function createItem() {
    if (!newName.trim() || !newRoom.trim() || !newPhoto || validatePhoto(newPhoto)) return
    const id = `local_${Date.now().toString(36)}`
    const evidenceId = `${id}_photo`
    const item: Item = {
      item_id: id, name: newName.trim(), room: newRoom.trim(),
      location_in_image: 'You selected the attached photo for this item. A location within the photo has not been recorded.',
      visible_observations: newNotes.trim() || 'You added this item manually. No automatic photo analysis has been performed.',
      supporting_evidence_ids: [evidenceId], brand: null, model: null, purchase_date: null,
      purchase_price: null, currency: null, user_confirmed_fields: ['name', 'room', ...(newNotes.trim() ? ['visible_observations'] : [])],
    }
    setUserItems(current => [...current, item])
    setUserEvidence(current => [...current, { evidence_id: evidenceId, filename: newPhoto.name, evidence_type: 'photo', description: 'Photo explicitly selected by you for this item. No automatic observations or verification have been created.' }])
    setAssignedPhotos(current => ({ ...current, [id]: newPhoto }))
    setScope('yours')
    setRoom('all')
    setSearch('')
    setCreating(false)
    setExpandedId(id)
    setNewName('')
    setNewNotes('')
    setNewPhoto(null)
  }

  function photoPicker(item: Item, inModal = false) {
    const photo = photoFor(item)
    const sampleImage = sampleImageFor(item)
    const selectedKey = photo ? fileKey(photo) : ''
    const isLocalPhoto = photo && !imageFiles.some(file => fileKey(file) === selectedKey)
    return (
      <div className={`inventory-photo-controls${inModal ? ' in-modal' : ''}`}>
        {imageFiles.length > 0 && <label className="inventory-photo-select"><span className="inventory-sr-only">Choose a photo for {item.name}</span><select value={selectedKey} onChange={event => assignPhoto(item, imageFiles.find(file => fileKey(file) === event.target.value) ?? null)}><option value="">Choose a photo</option>{isLocalPhoto && <option value={selectedKey}>{photo.name}</option>}{imageFiles.map(file => <option key={fileKey(file)} value={fileKey(file)}>{file.name}</option>)}</select></label>}
        <label className="inventory-photo-upload"><span aria-hidden="true">＋</span>{photo ? 'Change photo' : sampleImage ? 'Replace sample photo' : 'Add a photo'}<input className="inventory-sr-only" type="file" accept="image/*" aria-label={`${photo || sampleImage ? 'Change' : 'Add'} photo for ${item.name}`} onChange={event => {
          const file = event.target.files?.[0]
          if (file) assignPhoto(item, file)
          event.target.value = ''
        }} /></label>
      </div>
    )
  }

  return (
    <section className="inventory-workspace" aria-labelledby="inventory-title">
      <div className="inventory-heading"><div><p className="eyebrow">{activeView === 'review' ? 'REVIEW' : 'INVENTORY'}</p><h1 id="inventory-title">{activeView === 'review' ? 'Review your items.' : 'Your recovery record.'}</h1></div><button type="button" className="button-primary inventory-create-button" onClick={() => setCreating(true)}><span aria-hidden="true">＋</span> Create item</button></div>

      <div className="inventory-scope-tabs" aria-label="Inventory source"><button type="button" aria-pressed={!isSample} onClick={() => chooseScope('yours')}>Your items <span>{userItems.length}</span></button><button type="button" aria-pressed={isSample} onClick={() => chooseScope('sample')}>Sample inventory <span>8</span></button></div>

      {displayedItems.length > 0 && <div className="inventory-stats" aria-label="Inventory summary"><div className="inventory-stat"><span className="inventory-stat-icon"><InventoryIcon name="layers" /></span><div><span>Total items</span><strong>{displayedItems.length}<small>item records</small></strong></div></div><div className="inventory-stat"><span className="inventory-stat-icon"><InventoryIcon name="file" /></span><div><span>{isSample ? 'Sample evidence' : 'Photos attached'}</span><strong>{records?.evidence.length ?? 0}<small>{isSample ? 'source descriptions' : 'selected by you'}</small></strong></div></div><div className="inventory-stat"><span className="inventory-stat-icon inventory-stat-icon-warm"><InventoryIcon name={isSample ? 'review' : 'check'} /></span><div><span>{isSample ? 'Open review tasks' : 'Confirmed fields'}</span><strong>{isSample ? openTaskCount : confirmedFieldCount}<small>{isSample ? 'across this sample case' : 'details you provided'}</small></strong></div></div></div>}

      <div className="inventory-sample-banner"><InventoryIcon name="info" /><p>{isSample ? 'Sample records — separate from your uploads.' : 'Manually added items. Automatic photo analysis is not connected.'}</p></div>

      {displayedItems.length === 0 ? (
        <div className="inventory-welcome-card"><div className="inventory-empty-art" aria-hidden="true"><div className="inventory-paper inventory-paper-back" /><div className="inventory-paper"><InventoryIcon name="layers" /><span /><span /><span /></div><div className="inventory-art-check"><InventoryIcon name="check" /></div></div><p className="eyebrow">A PLACE FOR THE THINGS THAT MATTER</p><h2>{isSample ? 'Explore a clearer picture.' : 'Start with one belonging.'}</h2><p>{isSample ? 'See how item details, supporting evidence, and review questions fit together in a sample living room inventory.' : 'Choose one of your photos, name the item, and add what you know. You can come back to the rest.'}</p><button className="button-primary inventory-demo-button" type="button" onClick={isSample ? error ? () => { setError(''); setRetryCount(count => count + 1) } : onDemoRequest : () => setCreating(true)} disabled={loading}>{loading ? 'Opening sample inventory…' : isSample ? error ? 'Try again' : 'Explore sample inventory' : 'Create your first item'}{!loading && <InventoryIcon name="arrow" />}</button>{error && isSample && <p className="inventory-error" role="alert">{error}</p>}<small>{isSample ? 'Sample records only. No automated analysis of your files.' : imageFiles.length ? `${imageFiles.length} selected ${imageFiles.length === 1 ? 'photo is' : 'photos are'} ready to use.` : 'Add a photo directly, or choose files from Evidence.'}</small></div>
      ) : (
        <>
          <div className="inventory-toolbar"><label className="inventory-search"><InventoryIcon name="search" /><span className="inventory-sr-only">Search inventory</span><input type="search" placeholder="Find a belonging…" value={search} onChange={event => setSearch(event.target.value)} /></label><div className="inventory-toolbar-end"><span>{filteredItems.length} {filteredItems.length === 1 ? 'item' : 'items'}</span><label className="inventory-room-filter"><span className="inventory-sr-only">Filter by room</span><select value={room} onChange={event => setRoom(event.target.value)}><option value="all">All rooms</option>{rooms.map(value => <option key={value} value={value}>{value}</option>)}</select></label></div></div>
          <div className="inventory-card-grid">
            {filteredItems.map((item, index) => {
              const taskCount = itemTaskCount(item.item_id)
              const expanded = expandedId === item.item_id
              const photo = photoFor(item)
              const sampleImage = sampleImageFor(item)
              return (
                <Disclosure
                  key={item.item_id}
                  open={expanded}
                  onOpenChange={open => setExpandedId(open ? item.item_id : null)}
                  className={`inventory-card inventory-card-${index % 3}${expanded ? ' is-expanded' : ''}`}
                  variants={{ expanded: { y: 0 }, collapsed: { y: 12 } }}
                  transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
                >
                  <div className={`inventory-card-media${photo || sampleImage ? ' has-photo' : ''}`}>
                    <DisclosureTrigger className="inventory-image-trigger" aria-label={`${expanded ? 'Hide' : 'Show'} details for ${item.name}`}>
                      {photo
                        ? <PhotoPreview file={photo} className="inventory-card-image" />
                        : sampleImage ? <img src={sampleImage.src} alt={sampleImage.alt} className="inventory-card-image" loading="lazy" decoding="async" />
                        : <span className="inventory-photo-placeholder"><InventoryIcon name="image" /><span>No photo attached</span></span>}
                      <span className="inventory-card-room">{item.room}</span><span className="inventory-record-index" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
                      {sampleImage && <span className="inventory-sample-image-badge">Sample photo</span>}
                    </DisclosureTrigger>
                    <div className="inventory-card-sheet">
                      <button type="button" className="inventory-card-trigger" aria-expanded={expanded} onClick={() => setExpandedId(expanded ? null : item.item_id)}>
                        <span><strong>{item.name}</strong><span className={`inventory-status ${taskCount ? 'inventory-status-review' : 'inventory-status-neutral'}`}><span />{taskCount ? `${taskCount} review ${taskCount === 1 ? 'task' : 'tasks'}` : isSample ? 'Details to confirm' : 'Added by you'}</span></span>
                        <span className="inventory-card-toggle" aria-hidden="true">{expanded ? '−' : '+'}</span>
                      </button>
                      <DisclosureContent className="inventory-card-disclosure">
                        <div>
                          <p>{item.visible_observations}</p>
                          <div className="inventory-card-summary"><span>{item.user_confirmed_fields.length} fields confirmed</span><span>{item.supporting_evidence_ids.length} {isSample ? 'sources' : item.supporting_evidence_ids.length === 1 ? 'photo' : 'photos'}</span></div>
                          <button type="button" className="button-primary inventory-review-button" onClick={() => openItem(item)}>View more<InventoryIcon name="arrow" /></button>
                        </div>
                      </DisclosureContent>
                    </div>
                  </div>
                </Disclosure>
              )
            })}
          </div>
          {!filteredItems.length && <div className="inventory-no-results"><InventoryIcon name="search" /><h3>No matching belongings</h3><p>Try a different name or room.</p><button type="button" onClick={() => { setSearch(''); setRoom('all') }}>Clear filters</button></div>}
        </>
      )}

      <Dialog open={itemOpen && Boolean(selected)} onOpenChange={setItemOpen}>
        <DialogContent className="inventory-dialog">
          {selected && records && <>
            <div className="inventory-modal-heading"><div><span className="inventory-modal-eyebrow">{isSample ? 'SAMPLE ITEM' : 'YOUR ITEM'} <i>·</i> {selected.room}</span><DialogTitle className="inventory-modal-title">{selected.name}</DialogTitle><DialogDescription className="inventory-modal-description">{isSample ? 'Check the evidence, confirm details, and work through questions.' : 'An item you added manually. Confirm the details you know.'}</DialogDescription></div><DialogClose className="inventory-modal-close"><span aria-hidden="true">×</span><span className="inventory-sr-only">Close item</span></DialogClose></div>
            <div className={`inventory-modal-media${selectedPhoto || selectedSampleImage ? ' has-photo' : ''}`}>
              {selectedPhoto ? <PhotoPreview file={selectedPhoto} />
                : selectedSampleImage ? <img src={selectedSampleImage.src} alt={selectedSampleImage.alt} decoding="async" />
                : <div className="inventory-photo-placeholder"><InventoryIcon name="image" /><span>Add a photo to bring this item into focus</span></div>}
            </div>
            <div className="inventory-modal-photo-row">
              {photoPicker(selected, true)}
              {photoErrors[selected.item_id] && <p className="inventory-photo-error" role="alert">{photoErrors[selected.item_id]}</p>}
              {selectedSampleImage
                ? <p>Illustrative sample · <a href={selectedSampleImage.source} target="_blank" rel="noopener noreferrer">{selectedSampleImage.photographer} / Unsplash</a></p>
                : <p>{isSample ? 'Chosen photos are previews. Sample evidence stays separate.' : selectedPhoto ? 'This photo was explicitly selected by you.' : 'No photo attached. Choose one to add it to this item.'}</p>}
            </div>
            <div className="inventory-tabs" aria-label="Item information">{panelNames.map(value => <button type="button" key={value} aria-pressed={panel === value} onClick={() => setPanel(value)}>{value === 'details' ? 'Item details' : value === 'evidence' ? 'Evidence' : 'Review'}{value === 'review' && itemTaskCount(selected.item_id) > 0 && <span>{itemTaskCount(selected.item_id)}</span>}</button>)}</div>
            <TransitionPanel keepMounted activeIndex={panelNames.indexOf(panel)} className="inventory-modal-panels" transition={{ duration: 0.22, ease: 'easeInOut' }}>
              <div className="inventory-detail-body"><div className="inventory-section-heading"><h3>{isSample ? 'What the sample describes' : 'Your notes'}</h3><span>{isSample ? 'From sample evidence' : 'Added by you'}</span></div><div className="inventory-observation"><p>{selected.visible_observations}</p>{isSample && <details><summary>Where it appears in the sample image</summary><p>{selected.location_in_image}</p></details>}</div><div className="inventory-section-heading inventory-fields-heading"><div><h3>Item details</h3></div></div><div className="inventory-fields">{editableFields.map(field => <FieldEditor key={selected.item_id + field} label={field} value={selected[field]} confirmed={selected.user_confirmed_fields.includes(field)} onConfirm={value => confirmField(field, value)} />)}</div></div>
              <div className="inventory-detail-body inventory-evidence-panel"><div className="inventory-section-heading"><div><h3>Supporting evidence</h3><p>{isSample ? 'Descriptions from the sample case. Original sample files aren’t included.' : 'Photos you selected for this item. No automated analysis has been performed.'}</p></div></div>{selected.supporting_evidence_ids.length === 0 && <p className="inventory-muted">No photo is attached yet. Add one using the photo controls above.</p>}{selected.supporting_evidence_ids.map(id => { const evidence = records.evidence.find(entry => entry.evidence_id === id); return <article className="inventory-evidence-card" key={id}><div className="inventory-evidence-file"><span><InventoryIcon name="file" /></span><div><strong>{evidence?.filename ?? 'File unavailable'}</strong><small>{isSample ? 'Sample source description' : 'Selected photo'}</small></div></div>{!isSample && photoFor(selected) && <PhotoPreview file={photoFor(selected)!} className="inventory-evidence-photo" />}<p>{evidence?.description ?? 'No description is available for this source.'}</p></article> })}</div>
              <div className="inventory-detail-body inventory-review-panel">{isSample ? <ReviewFindings itemId={selected.item_id} findings={records.findings} questions={records.questions} resolutions={resolutions} onResolve={(id, reason) => setResolutions(current => ({ ...current, [id]: reason }))} onUndo={undoResolution} /> : <div className="inventory-manual-review"><InventoryIcon name="check" /><h3>Let’s start with what you know.</h3><p>This item was added by you. Automatic review isn’t connected yet, so there are no generated questions or findings.</p><button type="button" className="button-primary" onClick={() => setPanel('details')}>Confirm item details<InventoryIcon name="arrow" /></button><small>{selected.user_confirmed_fields.length} fields confirmed · Saved in this tab</small></div>}</div>
            </TransitionPanel>
            <div className="inventory-modal-footer"><InventoryIcon name="info" /><p>Changes stay in this browser tab and reset on refresh.</p><DialogClose className="button-secondary">Done for now</DialogClose></div>
          </>}
        </DialogContent>
      </Dialog>

      <Dialog open={creating} onOpenChange={setCreating}><DialogContent className="inventory-dialog inventory-create-dialog"><div className="inventory-modal-heading"><div><span className="inventory-modal-eyebrow">ONE BELONGING AT A TIME</span><DialogTitle className="inventory-modal-title">Add a belonging.</DialogTitle><DialogDescription className="inventory-modal-description">Name an item and choose its photo. You’re in control of what’s recorded.</DialogDescription></div><DialogClose className="inventory-modal-close"><span aria-hidden="true">×</span><span className="inventory-sr-only">Close create item</span></DialogClose></div><form className="inventory-create-form" onSubmit={event => { event.preventDefault(); createItem() }}><label>Item name<input value={newName} onChange={event => setNewName(event.target.value)} placeholder="e.g. Brown leather armchair" required autoFocus /></label><label>Room<input value={newRoom} onChange={event => setNewRoom(event.target.value)} placeholder="e.g. Living room" required /></label><div className="inventory-create-photo"><span>Choose this item’s photo</span>{imageFiles.length > 0 && <label className="inventory-create-select"><span className="inventory-sr-only">Choose from selected photos</span><select value={newPhoto && imageFiles.some(file => fileKey(file) === fileKey(newPhoto)) ? fileKey(newPhoto) : ''} onChange={event => chooseNewPhoto(imageFiles.find(file => fileKey(file) === event.target.value) ?? null)}><option value="">Choose from your selected photos…</option>{imageFiles.map(file => <option key={fileKey(file)} value={fileKey(file)}>{file.name}</option>)}</select></label>}<label className="inventory-create-upload"><InventoryIcon name="file" /><span>{newPhoto ? newPhoto.name : 'Choose an image from your device'}<small>{newPhoto ? 'Choose a different image' : 'Image files up to 10 MB' }</small></span><input className="inventory-sr-only" type="file" accept="image/*" aria-label="Choose this item’s photo from your device" onChange={event => { const file = event.target.files?.[0]; if (file) chooseNewPhoto(file); event.target.value = '' }} /></label>{createError && <p className="inventory-create-error" role="alert">{createError}</p>}{newPhoto && <PhotoPreview file={newPhoto} className="inventory-create-preview" />}{onAddEvidence && !imageFiles.length && <button type="button" className="button-quiet" onClick={() => { setCreating(false); onAddEvidence() }}>Or add files in Evidence <InventoryIcon name="arrow" /></button>}</div><label>A note to remember <span>(optional)</span><textarea value={newNotes} onChange={event => setNewNotes(event.target.value)} placeholder="A description, a detail, or anything you know about this item." /></label><p className="inventory-create-note"><InventoryIcon name="info" />You’re choosing the photo and item association. This doesn’t run automatic recognition. Your record stays in this tab.</p><button type="submit" className="button-primary inventory-create-submit" disabled={!newName.trim() || !newRoom.trim() || !newPhoto}>Create item <InventoryIcon name="arrow" /></button></form></DialogContent></Dialog>
    </section>
  )
}

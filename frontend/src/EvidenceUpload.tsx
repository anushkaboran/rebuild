import InvestigationRunner from './InvestigationRunner'
import { useEffect, useReducer, useRef, useState } from 'react'
import FilePreview from './FilePreview'
import './Evidence.css'

type EvidenceUploadProps = {
  caseId?: string
  room?: string
  onExploreDemo?: () => void
  onCountChange?: (count: number) => void
  onFilesChange?: (files: File[]) => void
  onCreateItems?: () => void
}

type SelectionState = { files: File[]; errors: string[] }
type SelectionAction =
  | { type: 'add'; files: File[] }
  | { type: 'remove'; key: string }

const MAX_FILES = 10
const MAX_FILE_SIZE = 10 * 1024 * 1024

function fileKey(file: File) {
  return `${file.name}:${file.size}:${file.lastModified}`
}

function fileType(file: File) {
  const types: Record<string, string> = {
    'image/jpeg': 'JPG',
    'image/png': 'PNG',
    'application/pdf': 'PDF',
  }
  if (file.type) return types[file.type] ?? null
  const extension = file.name.split('.').pop()?.toLowerCase()
  if (extension === 'jpg' || extension === 'jpeg') return 'JPG'
  if (extension === 'png') return 'PNG'
  if (extension === 'pdf') return 'PDF'
  return null
}

function formatSize(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function selectionReducer(state: SelectionState, action: SelectionAction): SelectionState {
  if (action.type === 'remove') {
    return { files: state.files.filter(file => fileKey(file) !== action.key), errors: [] }
  }

  const files = [...state.files]
  const keys = new Set(files.map(fileKey))
  const errors: string[] = []

  for (const file of action.files) {
    if (!fileType(file)) {
      errors.push(`${file.name}: choose a JPG, PNG, or PDF file.`)
    } else if (file.size > MAX_FILE_SIZE) {
      errors.push(`${file.name}: this file is larger than 10 MB.`)
    } else if (keys.has(fileKey(file))) {
      errors.push(`${file.name}: this file is already selected.`)
    } else if (files.length >= MAX_FILES) {
      errors.push(`${file.name}: you can select up to 10 files. Remove one to add another.`)
    } else {
      files.push(file)
      keys.add(fileKey(file))
    }
  }

  return { files, errors }
}

function UploadIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M4.5 15v4a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export default function EvidenceUpload({ caseId, room = 'Unassigned room', onExploreDemo, onCountChange, onFilesChange, onCreateItems }: EvidenceUploadProps) {
  const [{ files, errors }, dispatch] = useReducer(selectionReducer, { files: [], errors: [] })
  const [isDragging, setIsDragging] = useState(false)
  const [kinds, setKinds] = useState<Record<string, 'photo' | 'receipt'>>({})
  const [uploaded, setUploaded] = useState<Record<string, string>>({})
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [locked, setLocked] = useState(false)
  const busy = useRef(false)
  const pending = files.filter(file => !uploaded[fileKey(file)])

  async function uploadFiles() {
    if (!caseId || busy.current || locked) return
    busy.current = true
    setUploading(true)
    setUploadError('')
    try {
      for (const file of pending) {
        const key = fileKey(file)
        const body = new FormData()
        body.append('file', file)
        body.append('kind', kinds[key] ?? 'photo')
        body.append('uploaded_by', 'Lasya')
        const response = await fetch('/api/cases/' + encodeURIComponent(caseId) + '/uploads', { method: 'POST', body })
        if (!response.ok) throw new Error(file.name + ': upload failed (' + response.status + ').')
        const data = await response.json()
        if (typeof data.id !== 'string') throw new Error('Missing upload ID.')
        setUploaded(current => ({ ...current, [key]: data.id }))
      }
    } catch (cause) {
      setUploadError(cause instanceof Error ? cause.message : 'Upload failed. Retry the remaining files.')
    } finally { busy.current = false; setUploading(false) }
  }

  const inputRef = useRef<HTMLInputElement>(null)
  const dragDepth = useRef(0)
  const lastReportedCount = useRef<number | null>(null)
  const lastReportedFiles = useRef<File[] | null>(null)

  useEffect(() => {
    if (lastReportedCount.current !== files.length) {
      lastReportedCount.current = files.length
      onCountChange?.(files.length)
    }
  }, [files.length, onCountChange])

  useEffect(() => {
    const previous = lastReportedFiles.current
    if (previous === null || previous.length !== files.length || files.some((file, index) => previous[index] !== file)) {
      lastReportedFiles.current = files
      onFilesChange?.(files)
    }
  }, [files, onFilesChange])

  return (
    <section className="evidence-workspace" aria-labelledby="evidence-heading">
      <div className="evidence-section-heading">
        <div>
          <h2 id="evidence-heading">Photos &amp; receipts</h2>
        </div>
        <span className="evidence-room-badge">
          <svg width="15" height="15" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m3 8 7-5 7 5v8a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /><path d="M8 17v-6h4v6" stroke="currentColor" strokeWidth="1.4" /></svg>
          {room}
        </span>
      </div>

      <div className="evidence-layout">
        <div className="evidence-main-card">
          <div className="evidence-card-heading">
            <h3>Add your evidence</h3>
            <span className="evidence-count" aria-live="polite">{files.length} of {MAX_FILES} files</span>
          </div>

          <div
            className={`evidence-dropzone${isDragging ? ' is-dragging' : ''}`}
            onDragEnter={event => {
              if (!event.dataTransfer.types.includes('Files')) return
              event.preventDefault()
              dragDepth.current += 1
              setIsDragging(true)
            }}
            onDragOver={event => {
              if (!event.dataTransfer.types.includes('Files')) return
              event.preventDefault()
              event.dataTransfer.dropEffect = 'copy'
            }}
            onDragLeave={event => {
              event.preventDefault()
              dragDepth.current = Math.max(0, dragDepth.current - 1)
              if (dragDepth.current === 0) setIsDragging(false)
            }}
            onDrop={event => {
              event.preventDefault()
              dragDepth.current = 0
              setIsDragging(false)
              if (!busy.current && !locked) dispatch({ type: 'add', files: Array.from(event.dataTransfer.files) })
            }}
          >
            <span className="evidence-upload-icon"><UploadIcon /></span>
            <h4>{isDragging ? 'Drop your files here' : 'Select your files'}</h4>
            <p>Drag and drop photos or receipts here</p>
            <input
              ref={inputRef}
              type="file"
              accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
              multiple
              disabled={uploading || locked}
              hidden
              aria-label="Choose evidence files"
              onChange={event => {
                dispatch({ type: 'add', files: Array.from(event.target.files ?? []) })
                event.target.value = ''
              }}
            />
            <button type="button" className="button-primary evidence-browse-button" disabled={uploading || locked} onClick={() => inputRef.current?.click()}>
              <svg width="16" height="16" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 4v12M4 10h12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
              Browse files
            </button>
            <span className="evidence-file-requirements">JPG, PNG or PDF · Up to 10 MB each · 10 files max</span>
          </div>

          {errors.length > 0 && (
            <div className="evidence-feedback" role="alert">
              <strong>Some files couldn’t be added</strong>
              <ul>{errors.map((error, index) => <li key={`${error}-${index}`}>{error}</li>)}</ul>
            </div>
          )}

          {files.length > 0 && (
            <div className="evidence-selected">
              <div className="evidence-selected-heading">
                <h4>Selected files</h4>
                <span>Click a thumbnail to preview</span>
              </div>
              <ul className="evidence-file-list">
                {files.map(file => (
                  <li className="evidence-file-row" key={fileKey(file)}>
                    <FilePreview file={file} />
                    <div className="evidence-file-info">
                      <strong title={file.name}>{file.name}</strong>
                      <span>{fileType(file)} <span aria-hidden="true">·</span> {formatSize(file.size)}</span>
                    </div>
                    <label className="evidence-kind"><span className="inventory-sr-only">Type for {file.name}</span><select
                      value={kinds[fileKey(file)] ?? 'photo'} disabled={uploading || locked || Boolean(uploaded[fileKey(file)])}
                      onChange={event => { const kind = event.target.value as 'photo' | 'receipt'; setKinds(current => ({ ...current, [fileKey(file)]: kind })) }}>
                      <option value="photo">Room photo / close-up</option><option value="receipt">Receipt</option>
                    </select></label>
                    <span className="evidence-local-label">{uploaded[fileKey(file)] ? 'Uploaded ✓' : 'Local file'}</span>
                    <button
                      type="button"
                      className="evidence-remove-button" disabled={uploading || locked || Boolean(uploaded[fileKey(file)])}
                      aria-label={`Remove ${file.name}`}
                      onClick={() => dispatch({ type: 'remove', key: fileKey(file) })}
                    >
                      <svg width="17" height="17" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m6 6 8 8M14 6l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
                    </button>
                  </li>
                ))}
              </ul>
              {onCreateItems && !caseId && (
                <div className="evidence-create-items">
                  <p>Name an item and choose its photo in your inventory.</p>
                  <button type="button" className="button-primary" onClick={onCreateItems}>
                    Create item cards <span aria-hidden="true">→</span>
                  </button>
                </div>
              )}
            </div>
          )}

          <div className="evidence-local-note">
            <svg width="17" height="17" viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="3" y="4" width="14" height="10" rx="1.7" stroke="currentColor" strokeWidth="1.3" /><path d="M7 17h6M10 14v3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /></svg>
            <p>{Object.keys(uploaded).length} files uploaded to this case. Selected files stay local until you click Upload.</p>
          </div>
        </div>


      </div>
      {!caseId && <p className="notice">Create a case in Room setup before uploading or investigating these files.</p>}
      {caseId && files.length > 0 && <div className="backend-upload-actions">
        <button type="button" className="button-primary" disabled={uploading || locked || pending.length === 0} onClick={() => void uploadFiles()}>
          {uploading ? 'Uploading…' : pending.length ? 'Upload ' + pending.length + ' files' : 'All files uploaded'}
        </button>
        {uploadError && <p role="alert">{uploadError}</p>}
      </div>}
      {caseId && files.length > 0 && pending.length === 0 && !uploading && (
        <InvestigationRunner caseId={caseId} room={room} uploadIds={files.map(file => uploaded[fileKey(file)])} onStarted={() => setLocked(true)} />
      )}
      {onExploreDemo && <button type="button" className="button-quiet evidence-sample-link" onClick={onExploreDemo}>Open sample inventory <span aria-hidden="true">↗</span></button>}
    </section>
  )
}

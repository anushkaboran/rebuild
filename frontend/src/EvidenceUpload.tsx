import { useEffect, useReducer, useRef, useState } from 'react'
import FilePreview from './FilePreview'
import './Evidence.css'

type EvidenceUploadProps = {
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

function TipIcon({ kind }: { kind: 'photo' | 'detail' | 'receipt' }) {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      {kind === 'photo' && <><rect x="3" y="4" width="18" height="16" rx="3" /><circle cx="8" cy="9" r="1.5" /><path d="m3 17 5-5 4 4 3-3 6 6" /></>}
      {kind === 'detail' && <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4.5 4.5M8 10.5h5M10.5 8v5" /></>}
      {kind === 'receipt' && <><path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Z" /><path d="M9 7h6M9 11h6M9 15h3" /></>}
    </svg>
  )
}

export default function EvidenceUpload({ room = 'Unassigned room', onExploreDemo, onCountChange, onFilesChange, onCreateItems }: EvidenceUploadProps) {
  const [{ files, errors }, dispatch] = useReducer(selectionReducer, { files: [], errors: [] })
  const [isDragging, setIsDragging] = useState(false)
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
          <h2 id="evidence-heading">Every detail helps tell the story.</h2>
          <p>Bring photos and receipts together, one room at a time.</p>
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
              dispatch({ type: 'add', files: Array.from(event.dataTransfer.files) })
            }}
          >
            <span className="evidence-upload-icon"><UploadIcon /></span>
            <h4>{isDragging ? 'Drop your files here' : 'Start with what you have'}</h4>
            <p>Drag and drop photos or receipts here</p>
            <input
              ref={inputRef}
              type="file"
              accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf"
              multiple
              hidden
              aria-label="Choose evidence files"
              onChange={event => {
                dispatch({ type: 'add', files: Array.from(event.target.files ?? []) })
                event.target.value = ''
              }}
            />
            <button type="button" className="button-primary evidence-browse-button" onClick={() => inputRef.current?.click()}>
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
                    <span className="evidence-local-label">Local file</span>
                    <button
                      type="button"
                      className="evidence-remove-button"
                      aria-label={`Remove ${file.name}`}
                      onClick={() => dispatch({ type: 'remove', key: fileKey(file) })}
                    >
                      <svg width="17" height="17" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m6 6 8 8M14 6l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
                    </button>
                  </li>
                ))}
              </ul>
              {onCreateItems && (
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
            <p>Files are selected locally. Nothing is uploaded or processed. Refreshing this page clears your selections.</p>
          </div>
        </div>

        <aside className="evidence-sidebar" aria-label="Evidence tips">
          <div className="evidence-tips-card">
            <span className="evidence-tip-eyebrow">A GOOD PLACE TO START</span>
            <h3>A few helpful details</h3>
            <p>There’s no perfect starting point. Add what you can, when you can.</p>
            <ul className="evidence-tip-list">
              <li><span className="evidence-tip-icon"><TipIcon kind="photo" /></span><div><strong>The bigger picture</strong><p>A wide photo helps document the room and what’s in it.</p></div></li>
              <li><span className="evidence-tip-icon"><TipIcon kind="detail" /></span><div><strong>The small details</strong><p>Close-ups of labels, serial numbers, or damage add context.</p></div></li>
              <li><span className="evidence-tip-icon"><TipIcon kind="receipt" /></span><div><strong>A paper trail</strong><p>Receipts and invoices can help you record purchase details.</p></div></li>
            </ul>
          </div>

          {onExploreDemo && (
            <div className="evidence-demo-card">
              <span className="evidence-demo-badge">SAMPLE WORKSPACE</span>
              <h3>Take a look around first.</h3>
              <p>Explore an example inventory with sample items and evidence.</p>
              <button type="button" className="evidence-demo-button" onClick={onExploreDemo}>
                Explore sample inventory <span aria-hidden="true">↗</span>
              </button>
              <small>Sample data is separate from your files.</small>
            </div>
          )}
        </aside>
      </div>
    </section>
  )
}

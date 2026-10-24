import InvestigationRunner from './InvestigationRunner'
import { useRef, useState } from 'react'
import FilePreview from './FilePreview'

type Entry = {
  id: string
  file: File
  kind: 'photo' | 'receipt'
  uploadId?: string
}

export default function EvidenceUpload({ caseId, room }: { caseId: string; room: string }) {
  const [files, setFiles] = useState<Entry[]>([])
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)
  const busy = useRef(false)

  function addFiles(selected: FileList | null) {
    if (!selected || busy.current) return

    const incoming = Array.from(selected)
    const allowed = ['image/jpeg', 'image/png', 'application/pdf']

    if (incoming.some(file => !allowed.includes(file.type))) {
      setError('Choose JPG, PNG, or PDF files only.')
      return
    }
    if (incoming.some(file => file.size > 10 * 1024 * 1024)) {
      setError('Each file must be 10 MB or smaller.')
      return
    }
    if (files.length + incoming.length > 10) {
      setError('You can select up to 10 files in total.')
      return
    }

    setFiles(current => [
      ...current,
      ...incoming.map(file => ({
        id: crypto.randomUUID(),
        file,
        kind: 'photo' as const,
      })),
    ])
    setError('')
  }

  async function uploadFiles() {
    if (busy.current || !caseId) return

    busy.current = true
    setUploading(true)
    setError('')
    let currentName = ''

    try {
      for (const entry of files.filter(file => !file.uploadId)) {
        currentName = entry.file.name

        const body = new FormData()
        body.append('file', entry.file)
        body.append('kind', entry.kind)
        body.append('uploaded_by', 'Lasya')

        const response = await fetch(
          `/api/cases/${encodeURIComponent(caseId)}/uploads`,
          { method: 'POST', body },
        )

        if (!response.ok) {
          throw new Error(`Upload failed (${response.status}).`)
        }

        const uploaded = await response.json()
        if (typeof uploaded.id !== 'string') {
          throw new Error('The server did not return an upload ID.')
        }

        setFiles(current =>
          current.map(file =>
            file.id === entry.id
              ? { ...file, uploadId: uploaded.id }
              : file,
          ),
        )
      }
    } catch (cause) {
      setError(
        `${currentName}: ${
          cause instanceof Error ? cause.message : 'Could not upload.'
        } Successfully uploaded files are kept. Try again for the remaining files.`,
      )
    } finally {
      busy.current = false
      setUploading(false)
    }
  }

  const uploadedCount = files.filter(file => file.uploadId).length
  const pendingCount = files.length - uploadedCount

  return (
    <section className="case-form" aria-labelledby="evidence-heading">
      <h2 id="evidence-heading">Bring your evidence together</h2>
      <p className="intro">
        Add your files, then identify each as a room photo or receipt.
      </p>

      <label htmlFor="evidence-files">Choose evidence files</label>
      <input
        id="evidence-files"
        type="file"
        accept=".jpg,.jpeg,.png,.pdf"
        multiple
        disabled={uploading}
        onChange={event => {
          addFiles(event.target.files)
          event.target.value = ''
        }}
      />

      <p className="helper">
        {files.length}/10 files selected · JPG, PNG, PDF · Up to 10 MB each
      </p>

      {error && <p role="alert">{error}</p>}

      <ul className="file-list">
        {files.map(entry => (
          <li key={entry.id}>
            <FilePreview file={entry.file} />
            <span>{entry.file.name}</span>

            <select
              aria-label={`Evidence type for ${entry.file.name}`}
              value={entry.kind}
              disabled={uploading || Boolean(entry.uploadId)}
              onChange={event => {
                const kind = event.target.value as Entry['kind']
                setFiles(current =>
                  current.map(file =>
                    file.id === entry.id ? { ...file, kind } : file,
                  ),
                )
              }}
            >
              <option value="photo">Room photo / close-up</option>
              <option value="receipt">Receipt</option>
            </select>

            {entry.uploadId ? (
              <span>Uploaded ✓</span>
            ) : (
              <button
                type="button"
                className="remove-file"
                disabled={uploading}
                aria-label={`Remove ${entry.file.name}`}
                onClick={() => {
                  setFiles(current =>
                    current.filter(file => file.id !== entry.id),
                  )
                  setError('')
                }}
              >
                Remove
              </button>
            )}
          </li>
        ))}
      </ul>

      <button
        type="button"
        disabled={uploading || pendingCount === 0}
        onClick={() => void uploadFiles()}
      >
        {uploading
          ? 'Uploading…'
          : pendingCount > 0
            ? `Upload ${pendingCount} file${pendingCount === 1 ? '' : 's'}`
            : uploadedCount > 0
              ? 'All files uploaded'
              : 'Select files to upload'}
      </button>

      <p className="helper" role="status">
        {uploadedCount > 0
          ? `${uploadedCount} file${uploadedCount === 1 ? '' : 's'} uploaded to this case.`
          : 'Selected files stay on your computer until you click Upload.'}
        {' '}Upload all evidence before starting an investigation.
      </p>

      {uploadedCount > 0 && pendingCount === 0 && !uploading && (
        <InvestigationRunner
          caseId={caseId}
          room={room}
          uploadIds={files.flatMap(file => file.uploadId ? [file.uploadId] : [])}
        />
      )}
    </section>
  )
}

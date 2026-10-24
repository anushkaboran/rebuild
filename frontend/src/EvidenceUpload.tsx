import FilePreview from './FilePreview'

import { useState } from 'react'

export default function EvidenceUpload() {
  const [files, setFiles] = useState<File[]>([])
  const [error, setError] = useState('')

  function addFiles(selected: FileList | null) {
    if (!selected) return

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

    setFiles(current => [...current, ...incoming])
    setError('')
  }

  return (
    <section className="case-form" aria-labelledby="evidence-heading">
      <h2 id="evidence-heading">Bring your evidence together</h2>
      <p className="intro">
        Add room photos, close-ups, and receipts.
      </p>

      <label htmlFor="evidence-files">Choose evidence files</label>
      <input
        id="evidence-files"
        type="file"
        accept=".jpg,.jpeg,.png,.pdf"
        multiple
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
        {files.map((file, index) => (
          <li key={`${file.name}-${index}`}>
            <FilePreview file={file} /><span>{file.name}</span>
            <button
              type="button"
              className="remove-file"
              aria-label={`Remove ${file.name}`}
              onClick={() => {
                setFiles(current => current.filter((_, i) => i !== index))
                setError('')
              }}
            >
              Remove
            </button>
          </li>
        ))}
      </ul>

      <p className="helper">
        Files are selected locally. Nothing has been uploaded yet.
      </p>
    </section>
  )
}

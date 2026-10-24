import { useRef, useState } from 'react'

export default function DownloadPdf({ caseId }: { caseId: string }) {
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState('')
  const busy = useRef(false)

  async function download() {
    if (busy.current) return
    busy.current = true
    setDownloading(true)
    setError('')

    try {
      const response = await fetch(
        `/api/cases/${encodeURIComponent(caseId)}/export.pdf`,
      )
      if (!response.ok) {
        throw new Error(`Could not export PDF (${response.status}).`)
      }

      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `rebuild-${caseId}.pdf`
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 60000)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Download failed.')
    } finally {
      busy.current = false
      setDownloading(false)
    }
  }

  return (
    <div>
      <button className="button-primary" type="button" disabled={downloading}
        onClick={() => void download()}>
        {downloading ? 'Preparing PDF…' : 'Download recovery record PDF ↓'}
      </button>
      {error && <p role="alert">{error}</p>}
    </div>
  )
}

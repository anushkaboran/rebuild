import { useEffect, useState } from 'react'

export default function FilePreview({ file }: { file: File }) {
  const [url, setUrl] = useState('')

  useEffect(() => {
    if (!file.type.startsWith('image/')) return

    const objectUrl = URL.createObjectURL(file)
    setUrl(objectUrl)

    return () => URL.revokeObjectURL(objectUrl)
  }, [file])

  if (!file.type.startsWith('image/')) {
    return <span className="pdf-preview" aria-label="PDF document">PDF</span>
  }

  return url ? <img className="file-preview" src={url} alt={`Preview of ${file.name}`} /> : null
}

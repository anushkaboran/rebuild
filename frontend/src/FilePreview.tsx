import { useEffect, useId, useRef } from 'react'

export default function FilePreview({ file }: { file: File }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const thumbnailRef = useRef<HTMLImageElement>(null)
  const imageRef = useRef<HTMLImageElement>(null)
  const documentRef = useRef<HTMLObjectElement>(null)
  const linkRef = useRef<HTMLAnchorElement>(null)
  const headingId = useId()
  const isImage = file.type ? file.type.startsWith('image/') : /\.(jpe?g|png)$/i.test(file.name)

  useEffect(() => {
    const url = URL.createObjectURL(file)
    if (thumbnailRef.current) thumbnailRef.current.src = url
    if (imageRef.current) imageRef.current.src = url
    if (documentRef.current) documentRef.current.data = url
    if (linkRef.current) linkRef.current.href = url

    return () => {
      URL.revokeObjectURL(url)
    }
  }, [file])

  return (
    <>
      <button
        type="button"
        className={`evidence-thumbnail${isImage ? '' : ' evidence-thumbnail-pdf'}`}
        aria-label={`Preview ${file.name}`}
        aria-haspopup="dialog"
        onClick={() => dialogRef.current?.showModal()}
      >
        {isImage ? <img ref={thumbnailRef} alt="" /> : <><svg width="20" height="23" viewBox="0 0 20 23" fill="none" aria-hidden="true"><path d="M4 1.5h7l5 5v14H4V1.5Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /><path d="M11 1.5v5h5M7 11h6M7 14h6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" /></svg><span>PDF</span></>}
      </button>

      <dialog
        ref={dialogRef}
        className="evidence-preview-dialog"
        aria-labelledby={headingId}
        onClick={event => {
          if (event.target !== event.currentTarget) return
          const bounds = event.currentTarget.getBoundingClientRect()
          if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) {
            event.currentTarget.close()
          }
        }}
      >
        <div className="evidence-preview-heading">
          <div><span>LOCAL FILE PREVIEW</span><h2 id={headingId}>{file.name}</h2></div>
          <button type="button" className="evidence-preview-close" aria-label="Close preview" onClick={() => dialogRef.current?.close()}>
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
          </button>
        </div>
        <div className="evidence-preview-content">
          {isImage ? (
            <img ref={imageRef} className="evidence-preview-image" alt={`Full preview of ${file.name}`} />
          ) : (
            <object ref={documentRef} type="application/pdf" className="evidence-preview-document" aria-label={`PDF preview of ${file.name}`}>
              <p>Your browser can’t display this PDF here. Use the link below to open it.</p>
            </object>
          )}
        </div>
        <div className="evidence-preview-footer">
          <span>Selected locally · Nothing has been uploaded</span>
          <a ref={linkRef} target="_blank" rel="noopener noreferrer">Open file in new tab <span aria-hidden="true">↗</span></a>
        </div>
      </dialog>
    </>
  )
}

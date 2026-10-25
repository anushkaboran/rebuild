import { useEffect, useState } from 'react'

type Crop = { zoom: number; x: number; y: number }
const initial: Crop = { zoom: 1, x: 50, y: 50 }
export default function ItemPhoto({ itemId, src, alt, controls = false }: {
  itemId: string; src: string; alt: string; controls?: boolean
}) {
  const key = 'rebuild-item-crop:' + itemId + ':' + src
  function read(): Crop {
    try {
      const value = JSON.parse(localStorage.getItem(key) || 'null')
      if (value && Number.isFinite(value.zoom) && Number.isFinite(value.x) && Number.isFinite(value.y)) {
        return { zoom: Math.max(1, Math.min(5, value.zoom)), x: Math.max(0, Math.min(100, value.x)), y: Math.max(0, Math.min(100, value.y)) }
      }
    } catch { /* Use the full image when storage is unavailable. */ }
    return initial
  }
  const [crop, setCrop] = useState<Crop>(read)
  useEffect(() => {
    const update = () => setCrop(read())
    window.addEventListener('rebuild-crop-change', update)
    return () => window.removeEventListener('rebuild-crop-change', update)
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  function change(next: Crop) {
    setCrop(next)
    try { localStorage.setItem(key, JSON.stringify(next)) } catch { /* Preview still works. */ }
    window.dispatchEvent(new Event('rebuild-crop-change'))
  }
  return <div className={controls ? 'item-photo-editor' : 'item-photo-card'}>
    <div style={{ overflow: 'hidden', width: '100%', height: controls ? 240 : '100%' }}>
      <img src={src} alt={alt} loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover', transform: `scale(${crop.zoom})`, transformOrigin: `${crop.x}% ${crop.y}%`, display: 'block' }} />
    </div>
    {controls && <details style={{ padding: 12, background: 'white', color: '#252538' }}>
      <summary>Adjust item crop</summary>
      <p>Frame this item using the controls. Preview saved in this browser only.</p>
      {(['zoom', 'x', 'y'] as const).map(field => <label key={field} style={{ display: 'block', marginTop: 10 }}>
        {field === 'zoom' ? 'Zoom' : field === 'x' ? 'Horizontal focus' : 'Vertical focus'}
        <input aria-label={field === 'zoom' ? 'Zoom' : field === 'x' ? 'Horizontal focus' : 'Vertical focus'} type="range" min={field === 'zoom' ? 1 : 0} max={field === 'zoom' ? 5 : 100} step={field === 'zoom' ? 0.1 : 1} value={crop[field]} onChange={event => change({ ...crop, [field]: Number(event.target.value) })} />
      </label>)}
      <button type="button" onClick={() => change(initial)}>Reset crop</button>
    </details>}
  </div>
}

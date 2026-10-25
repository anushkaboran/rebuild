import { useEffect, useRef } from 'react'
import { useReducedMotion } from 'motion/react'

const CELL_SIZE = 32
const FADE_MS = 850

type Cell = { column: number; row: number; time: number }

/** A decorative trail; the static grid remains visible without motion or a mouse. */
export default function RoomGrid() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const reducedMotion = useReducedMotion()

  useEffect(() => {
    const canvas = canvasRef.current
    const surface = canvas?.parentElement
    const context = canvas?.getContext('2d')
    if (!canvas || !surface || !context || reducedMotion) return

    const finePointer = window.matchMedia('(any-hover: hover) and (any-pointer: fine)')
    const cells = new Map<string, Cell>()
    let width = 0
    let height = 0
    let frame = 0
    let previous: { x: number; y: number } | null = null

    function paint(now: number) {
      frame = 0
      context!.clearRect(0, 0, width, height)
      for (const [key, cell] of cells) {
        const progress = (now - cell.time) / FADE_MS
        if (progress >= 1) {
          cells.delete(key)
          continue
        }
        const opacity = (1 - progress) ** 2
        const x = cell.column * CELL_SIZE
        const y = cell.row * CELL_SIZE
        context!.fillStyle = (cell.column + cell.row) % 5 === 0 ? '#ff623c' : '#2445e4'
        context!.globalAlpha = opacity * 0.26
        context!.fillRect(x + 1, y + 1, CELL_SIZE - 1, CELL_SIZE - 1)
        context!.globalAlpha = opacity * 0.48
        context!.strokeStyle = context!.fillStyle
        context!.strokeRect(x + 0.5, y + 0.5, CELL_SIZE, CELL_SIZE)
      }
      context!.globalAlpha = 1
      // Sleep completely once the last square fades; no idle animation loop.
      if (cells.size) frame = requestAnimationFrame(paint)
    }

    function move(event: PointerEvent) {
      if (event.pointerType === 'touch' || !finePointer.matches) return
      const bounds = surface!.getBoundingClientRect()
      const point = { x: event.clientX - bounds.left, y: event.clientY - bounds.top }
      const now = performance.now()
      // Sample between events so fast mouse movements still leave a continuous trail.
      const start = previous ?? point
      const steps = Math.max(1, Math.ceil(Math.hypot(point.x - start.x, point.y - start.y) / (CELL_SIZE / 2)))
      for (let step = 1; step <= steps; step++) {
        const column = Math.floor((start.x + (point.x - start.x) * step / steps) / CELL_SIZE)
        const row = Math.floor((start.y + (point.y - start.y) * step / steps) / CELL_SIZE)
        if (column < 0 || row < 0 || column * CELL_SIZE >= width || row * CELL_SIZE >= height) continue
        cells.set(`${column}:${row}`, { column, row, time: now })
      }
      previous = point
      if (!frame && cells.size) frame = requestAnimationFrame(paint)
    }

    function leave() { previous = null }

    function reset() {
      cancelAnimationFrame(frame)
      frame = 0
      cells.clear()
      previous = null
      context!.clearRect(0, 0, width, height)
    }

    function resize() {
      reset()
      const bounds = surface!.getBoundingClientRect()
      width = bounds.width
      height = bounds.height
      const ratio = Math.min(window.devicePixelRatio || 1, 2)
      canvas!.width = Math.round(width * ratio)
      canvas!.height = Math.round(height * ratio)
      context!.setTransform(ratio, 0, 0, ratio, 0, 0)
    }

    const observer = new ResizeObserver(resize)
    observer.observe(surface)
    resize()
    surface.addEventListener('pointermove', move, { passive: true })
    surface.addEventListener('pointerleave', leave)
    surface.addEventListener('pointercancel', leave)
    // Scrolling or switching windows must not connect distant points with a trail.
    window.addEventListener('scroll', reset, { passive: true, capture: true })
    window.addEventListener('blur', reset)
    document.addEventListener('visibilitychange', reset)

    return () => {
      reset()
      observer.disconnect()
      surface.removeEventListener('pointermove', move)
      surface.removeEventListener('pointerleave', leave)
      surface.removeEventListener('pointercancel', leave)
      window.removeEventListener('scroll', reset, true)
      window.removeEventListener('blur', reset)
      document.removeEventListener('visibilitychange', reset)
    }
  }, [reducedMotion])

  return <canvas ref={canvasRef} className="room-grid-trail" aria-hidden="true" />
}

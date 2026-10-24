// Adapted from Motion Primitives by ibelick (MIT).
// Upstream: https://github.com/ibelick/motion-primitives/blob/main/components/core/dialog.tsx
import { createContext, useCallback, useContext, useId, useLayoutEffect, useRef, useState } from 'react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion, MotionConfig, useReducedMotion } from 'motion/react'
import type { Transition, Variants } from 'motion/react'

type DialogContextType = {
  isOpen: boolean
  setIsOpen: (open: boolean) => void
  variants: Variants
  transition: Transition
  className?: string
  ids: { dialog: string; title: string; description: string }
}

const DialogContext = createContext<DialogContextType | null>(null)
const defaultVariants: Variants = {
  initial: { opacity: 0, scale: 0.96, y: 10 },
  animate: { opacity: 1, scale: 1, y: 0 },
  exit: { opacity: 0, scale: 0.98, y: 7 },
}

function useDialog() {
  const context = useContext(DialogContext)
  if (!context) throw new Error('Dialog components must be used within Dialog')
  return context
}

export type DialogProps = {
  children: ReactNode
  open?: boolean
  defaultOpen?: boolean
  onOpenChange?: (open: boolean) => void
  variants?: Variants
  transition?: Transition
  className?: string
}

export function Dialog({ children, open, defaultOpen = false, onOpenChange, variants = defaultVariants,
  transition = { ease: 'easeOut', duration: 0.2 }, className }: DialogProps) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen)
  const isOpen = open ?? internalOpen
  const baseId = useId()
  const setIsOpen = useCallback((value: boolean) => {
    if (open === undefined) setInternalOpen(value)
    onOpenChange?.(value)
  }, [open, onOpenChange])

  return (
    <MotionConfig reducedMotion="user">
      <DialogContext.Provider value={{ isOpen, setIsOpen, variants, transition, className,
        ids: { dialog: `${baseId}-dialog`, title: `${baseId}-title`, description: `${baseId}-description` } }}>
        {children}
      </DialogContext.Provider>
    </MotionConfig>
  )
}

export function DialogTrigger({ children, onClick, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  const { setIsOpen } = useDialog()
  return <button {...props} type="button" onClick={event => {
    onClick?.(event)
    if (!event.defaultPrevented) setIsOpen(true)
  }}>{children}</button>
}

export type DialogContentProps = { children: ReactNode; className?: string; container?: HTMLElement | null; 'aria-label'?: string }

function DialogSurface({ children, className, 'aria-label': ariaLabel = 'Dialog' }: Omit<DialogContentProps, 'container'>) {
  const { setIsOpen, variants, transition, ids, className: rootClassName } = useDialog()
  const dialogRef = useRef<HTMLDialogElement>(null)
  const pointerStartedOutside = useRef(false)
  const reduceMotion = useReducedMotion()

  // showModal supplies the browser's focus trap, inert background, and Escape event.
  // Keep the native modal open until Motion finishes its exit animation.
  useLayoutEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    // Only reference labels that are actually present in this dialog.
    if (dialog.querySelector('[data-dialog-title]')) dialog.setAttribute('aria-labelledby', ids.title)
    if (dialog.querySelector('[data-dialog-description]')) dialog.setAttribute('aria-describedby', ids.description)
    if (!dialog.open) dialog.showModal()
    return () => {
      if (dialog.open) dialog.close()
      document.body.style.overflow = previousOverflow
      if (opener?.isConnected) opener.focus({ preventScroll: true })
    }
  }, [ids.title, ids.description])

  const isOutside = (clientX: number, clientY: number) => {
    const bounds = dialogRef.current?.getBoundingClientRect()
    return Boolean(bounds && (clientX < bounds.left || clientX > bounds.right || clientY < bounds.top || clientY > bounds.bottom))
  }

  return (
    <motion.dialog ref={dialogRef} id={ids.dialog} aria-label={ariaLabel}
      aria-modal="true" className={['motion-primitives-dialog', rootClassName, className].filter(Boolean).join(' ')}
      style={{ maxHeight: '90dvh', maxWidth: 'calc(100vw - 32px)', overflowY: 'auto', padding: 0 }}
      onCancel={event => { event.preventDefault(); setIsOpen(false) }}
      onPointerDown={event => { pointerStartedOutside.current = event.target === event.currentTarget && isOutside(event.clientX, event.clientY) }}
      onClick={event => {
        if (event.target === event.currentTarget && pointerStartedOutside.current && isOutside(event.clientX, event.clientY)) setIsOpen(false)
        pointerStartedOutside.current = false
      }}
      initial="initial" animate="animate" exit="exit"
      variants={reduceMotion ? { initial: { opacity: 1 }, animate: { opacity: 1 }, exit: { opacity: 1 } } : variants}
      transition={reduceMotion ? { duration: 0 } : transition}
      onAnimationComplete={definition => { if (definition === 'exit') dialogRef.current?.close() }}>
      <div style={{ width: '100%' }}>{children}</div>
    </motion.dialog>
  )
}

export function DialogContent({ children, className, container, 'aria-label': ariaLabel }: DialogContentProps) {
  const { isOpen } = useDialog()
  if (typeof document === 'undefined') return null
  return createPortal(
    <AnimatePresence>
      {isOpen && <DialogSurface key="dialog-content" className={className} aria-label={ariaLabel}>{children}</DialogSurface>}
    </AnimatePresence>,
    container ?? document.body,
  )
}

export function DialogHeader({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={className}>{children}</div>
}

export function DialogTitle({ children, className }: { children: ReactNode; className?: string }) {
  const { ids } = useDialog()
  return <h2 id={ids.title} data-dialog-title className={className}>{children}</h2>
}

export function DialogDescription({ children, className }: { children: ReactNode; className?: string }) {
  const { ids } = useDialog()
  return <p id={ids.description} data-dialog-description className={className}>{children}</p>
}

export function DialogClose({ children, onClick, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  const { setIsOpen } = useDialog()
  return <button {...props} type="button" aria-label={props['aria-label'] ?? 'Close dialog'} onClick={event => {
    onClick?.(event)
    if (!event.defaultPrevented) setIsOpen(false)
  }}>{children ?? <span aria-hidden="true">×</span>}</button>
}

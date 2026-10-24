// Adapted from Motion Primitives by ibelick (MIT).
// Upstream: https://github.com/ibelick/motion-primitives/blob/main/components/core/disclosure.tsx
import {
  Children, cloneElement, createContext, isValidElement, useContext, useId, useState,
} from 'react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { AnimatePresence, motion, MotionConfig, useReducedMotion } from 'motion/react'
import type { TargetAndTransition, Transition, Variant } from 'motion/react'

type DisclosureVariants = { expanded: Variant; collapsed: Variant }
type DisclosureContextType = {
  open: boolean
  toggle: () => void
  variants?: DisclosureVariants
  triggerId: string
  contentId: string
}

const DisclosureContext = createContext<DisclosureContextType | null>(null)

function useDisclosure() {
  const context = useContext(DisclosureContext)
  if (!context) throw new Error('Disclosure components must be used within Disclosure')
  return context
}

export type DisclosureProps = {
  open?: boolean
  onOpenChange?: (open: boolean) => void
  children: ReactNode
  className?: string
  variants?: DisclosureVariants
  transition?: Transition
}

export function Disclosure({ open: controlledOpen, onOpenChange, children, className, transition, variants }: DisclosureProps) {
  const [internalOpen, setInternalOpen] = useState(false)
  const open = controlledOpen ?? internalOpen
  const id = useId()
  const reduceMotion = useReducedMotion()
  const toggle = () => {
    if (controlledOpen === undefined) setInternalOpen(!open)
    onOpenChange?.(!open)
  }

  return (
    <MotionConfig reducedMotion="user" transition={reduceMotion ? { duration: 0 } : transition ?? { duration: 0.25, ease: 'easeInOut' }}>
      <DisclosureContext.Provider value={{ open, toggle, variants, triggerId: `${id}-trigger`, contentId: `${id}-content` }}>
        <div className={className} data-state={open ? 'open' : 'closed'}>{children}</div>
      </DisclosureContext.Provider>
    </MotionConfig>
  )
}

export type DisclosureTriggerProps = ButtonHTMLAttributes<HTMLButtonElement> & { children: ReactNode }

export function DisclosureTrigger({ children, className, onClick, ...props }: DisclosureTriggerProps) {
  const { toggle, open, triggerId, contentId } = useDisclosure()
  const child = Children.count(children) === 1 ? Children.toArray(children)[0] : null
  const sharedProps = {
    ...props,
    id: triggerId,
    'aria-expanded': open,
    'aria-controls': contentId,
    'data-state': open ? 'open' : 'closed',
  }

  // The documented native-button child is cloned, never wrapped in another button.
  // Native keyboard activation supplies exactly one click for Enter/Space.
  if (isValidElement<ButtonHTMLAttributes<HTMLButtonElement>>(child) && child.type === 'button') {
    return cloneElement(child, {
      ...sharedProps,
      type: child.props.type ?? 'button',
      className: [className, child.props.className].filter(Boolean).join(' ') || undefined,
      onClick: event => {
        child.props.onClick?.(event)
        if (!event.defaultPrevented) onClick?.(event)
        if (!event.defaultPrevented) toggle()
      },
    })
  }

  return (
    <button {...sharedProps} type="button" className={className} onClick={event => {
      onClick?.(event)
      if (!event.defaultPrevented) toggle()
    }}>{children}</button>
  )
}

function mergeVariant(base: TargetAndTransition, override?: Variant): Variant {
  return typeof override === 'function'
    ? (custom, current, velocity) => {
      const resolved = override(custom, current, velocity)
      return typeof resolved === 'string' ? resolved : { ...base, ...resolved }
    }
    : { ...base, ...override }
}

export function DisclosureContent({ children, className }: { children: ReactNode; className?: string }) {
  const { open, variants, triggerId, contentId } = useDisclosure()
  const reduceMotion = useReducedMotion()
  const combinedVariants = {
    expanded: mergeVariant({ height: 'auto', opacity: 1 }, variants?.expanded),
    collapsed: mergeVariant({ height: 0, opacity: 0 }, variants?.collapsed),
  }

  return (
    <div id={contentId} role="region" aria-labelledby={triggerId} aria-hidden={!open}
      inert={!open} className={className} style={{ overflow: 'hidden' }}>
      <AnimatePresence initial={false}>
        {open && <motion.div initial="collapsed" animate="expanded" exit="collapsed"
          variants={combinedVariants} transition={reduceMotion ? { duration: 0 } : undefined}>
          {children}
        </motion.div>}
      </AnimatePresence>
    </div>
  )
}

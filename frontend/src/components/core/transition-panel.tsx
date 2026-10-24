// Adapted from Motion Primitives by ibelick (MIT).
// Upstream: https://github.com/ibelick/motion-primitives/blob/main/components/core/transition-panel.tsx
import { Children } from 'react'
import type { ReactNode } from 'react'
import { AnimatePresence, motion, MotionConfig, useReducedMotion } from 'motion/react'
import type { HTMLMotionProps, Transition, Variant } from 'motion/react'

export type TransitionPanelProps = {
  children: ReactNode
  className?: string
  transition?: Transition
  activeIndex: number
  keepMounted?: boolean
  variants?: { enter: Variant; center: Variant; exit: Variant }
} & Omit<HTMLMotionProps<'div'>, 'children' | 'variants' | 'transition'>

export function TransitionPanel({ children, className, transition, variants, activeIndex, keepMounted = false, ...motionProps }: TransitionPanelProps) {
  const reduceMotion = useReducedMotion()
  const panels = Children.toArray(children)
  const panelVariants = reduceMotion
    ? { enter: { opacity: 1 }, center: { opacity: 1 }, exit: { opacity: 1 } }
    : variants ?? { enter: { opacity: 0, y: 8 }, center: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -5 } }
  const panelTransition = reduceMotion ? { duration: 0 } : transition ?? { duration: 0.18, ease: 'easeOut' as const }

  if (keepMounted) {
    return (
      <MotionConfig reducedMotion="user">
        <div className={className} style={{ position: 'relative', minWidth: 0 }}>
          {panels.map((panel, index) => (
            <motion.div key={index} {...motionProps} variants={panelVariants}
              hidden={index !== activeIndex} inert={index !== activeIndex} aria-hidden={index !== activeIndex}
              initial={false} animate={index === activeIndex ? 'center' : 'enter'} transition={panelTransition}>
              {panel}
            </motion.div>
          ))}
        </div>
      </MotionConfig>
    )
  }

  return (
    <MotionConfig reducedMotion="user">
      <div className={className} style={{ position: 'relative', minWidth: 0 }}>
        <AnimatePresence initial={false} mode="wait" custom={motionProps.custom}>
          <motion.div key={activeIndex} {...motionProps} variants={panelVariants}
            transition={panelTransition}
            initial="enter" animate="center" exit="exit">
            {panels[activeIndex]}
          </motion.div>
        </AnimatePresence>
      </div>
    </MotionConfig>
  )
}

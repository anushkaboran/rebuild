import { useEffect, useState } from 'react'

export default function ConnectionStatus() {
  const [label, setLabel] = useState('Checking API…')

  useEffect(() => {
    const controller = new AbortController()

    async function check() {
      try {
        const response = await fetch('/api/health', {
          signal: controller.signal,
        })
        if (!response.ok) throw new Error('API unavailable')

        const health = await response.json()
        if (health.status !== 'ok') throw new Error('Unexpected response')

        const mode = health.integrations?.creao?.mode
        setLabel(
          mode === 'mock'
            ? 'API connected · Simulated mode'
            : mode === 'live'
              ? 'API connected · Live mode'
              : 'API connected · Mode unknown'
        )
      } catch {
        if (!controller.signal.aborted) setLabel('API disconnected')
      }
    }

    void check()
    return () => controller.abort()
  }, [])

  return <span className="prototype" role="status">{label}</span>
}

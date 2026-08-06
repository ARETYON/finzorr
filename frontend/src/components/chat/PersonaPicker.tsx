// Per-session persona selector (dropdown fed by /api/personas).

import { useEffect, useState } from 'react'
import { listPersonas, setSessionPersona, type PersonaInfo } from '../../api/extras'

export default function PersonaPicker({ sessionId }: { sessionId: string | null }) {
  const [personas, setPersonas] = useState<PersonaInfo[]>([])
  const [selected, setSelected] = useState('')

  useEffect(() => {
    listPersonas()
      .then(setPersonas)
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    setSelected('') // session switch resets the visible selection
  }, [sessionId])

  if (!sessionId || personas.length === 0) return null

  const change = async (personaId: string) => {
    setSelected(personaId)
    try {
      await setSessionPersona(sessionId, personaId || null)
    } catch {
      // non-critical
    }
  }

  return (
    <select
      value={selected}
      onChange={(e) => void change(e.target.value)}
      className="fui-mono rounded border border-line bg-panel px-1.5 py-0.5 text-[11px] text-ink-mid focus:outline-none"
      aria-label="Persona"
    >
      <option value="">Default persona</option>
      {personas.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}
        </option>
      ))}
    </select>
  )
}

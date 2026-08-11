// Container: per-session persona selector, fed by /api/personas. Owns the
// fetch + selection wiring; renders the presentational PersonaSelect.

import { useEffect, useState } from 'react'
import { listPersonas, setSessionPersona, type PersonaInfo } from '../../api/extras'
import PersonaSelect from './PersonaSelect'

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

  const change = (personaId: string) => {
    setSelected(personaId)
    void setSessionPersona(sessionId, personaId || null).catch(() => undefined)
  }

  return <PersonaSelect personas={personas} selected={selected} onChange={change} />
}

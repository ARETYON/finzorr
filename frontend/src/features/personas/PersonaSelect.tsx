// Presentational: persona dropdown. Fetching + session-persona wiring lives
// in the PersonaPicker container.

import type { PersonaInfo } from '../../api/extras'

interface PersonaSelectProps {
  personas: PersonaInfo[]
  selected: string
  onChange: (personaId: string) => void
}

export default function PersonaSelect({ personas, selected, onChange }: PersonaSelectProps) {
  return (
    <select
      value={selected}
      onChange={(e) => onChange(e.target.value)}
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

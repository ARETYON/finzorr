// Settings: custom instructions (server-side) + auto-read toggle (client-side).

import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Trash2, X } from 'lucide-react'
import toast from 'react-hot-toast'
import { API_BASE } from '../api/client'
import { deleteMemory, listMemories, updateMe, type MemoryFact } from '../api/auth'
import { createPersona, deletePersona, listPersonas, type PersonaInfo } from '../api/extras'
import { useAuthStore } from '../store/authStore'
import { useSettingsStore } from '../store/settingsStore'

export default function SettingsModal({ onClose }: { onClose: () => void }) {
  const user = useAuthStore((s) => s.user)
  const { autoRead, setAutoRead } = useSettingsStore()
  const [instructions, setInstructions] = useState('')
  const [saving, setSaving] = useState(false)
  const [memories, setMemories] = useState<MemoryFact[]>([])
  const [personas, setPersonas] = useState<PersonaInfo[]>([])
  const [newPersonaName, setNewPersonaName] = useState('')
  const [newPersonaPrompt, setNewPersonaPrompt] = useState('')
  const dialogRef = useRef<HTMLDivElement>(null)

  // dialog semantics: focus moves in on open and is trapped; Escape closes
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null
    dialogRef.current?.querySelector<HTMLElement>('textarea, button, input')?.focus()
    return () => previouslyFocused?.focus()
  }, [])

  const onDialogKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      onClose()
      return
    }
    if (e.key !== 'Tab' || !dialogRef.current) return
    const focusables = dialogRef.current.querySelectorAll<HTMLElement>(
      'button, [href], input, textarea, select, [tabindex]:not([tabindex="-1"])',
    )
    if (focusables.length === 0) return
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    if (!first || !last) return
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }

  useEffect(() => {
    setInstructions(user?.custom_instructions ?? '')
    listMemories()
      .then(setMemories)
      .catch(() => undefined)
    listPersonas()
      .then(setPersonas)
      .catch(() => undefined)
  }, [user])

  const addPersona = async () => {
    if (!newPersonaName.trim() || !newPersonaPrompt.trim()) return
    try {
      await createPersona(newPersonaName.trim(), newPersonaPrompt.trim())
      setNewPersonaName('')
      setNewPersonaPrompt('')
      setPersonas(await listPersonas())
    } catch {
      toast.error('Could not create persona')
    }
  }

  const removePersona = async (id: string) => {
    try {
      await deletePersona(id)
      setPersonas((prev) => prev.filter((p) => p.id !== id))
    } catch {
      toast.error('Could not delete persona')
    }
  }

  const removeMemory = async (id: string) => {
    try {
      await deleteMemory(id)
      setMemories((prev) => prev.filter((m) => m.id !== id))
    } catch {
      toast.error('Could not delete memory')
    }
  }

  const save = async () => {
    setSaving(true)
    try {
      const updated = await updateMe(instructions)
      useAuthStore.setState({ user: updated })
      toast.success('Settings saved')
      onClose()
    } catch {
      toast.error('Could not save settings')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        ref={dialogRef}
        className="clip-panel fui-brackets w-full max-w-md rounded-2xl border border-line bg-panel p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onDialogKeyDown}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 id="settings-title" className="app-title text-sm font-semibold text-ink-strong">
            Settings
          </h2>
          <button onClick={onClose} className="text-ink-faint hover:text-ink-mid" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <label htmlFor="settings-instructions" className="mb-1 block text-xs font-medium text-ink-mid">
          Custom instructions (applied to every conversation)
        </label>
        <textarea
          id="settings-instructions"
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          rows={4}
          maxLength={2000}
          placeholder="e.g. Always answer briefly. Explain finance terms like I'm a beginner."
          className="clip-panel w-full resize-none rounded-lg border border-line-strong bg-panel px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-accent-strong focus:outline-none"
        />
        <div className="mt-4">
          <span className="mb-1 block text-xs font-medium text-ink-mid">
            What finzorr remembers about you
          </span>
          <ul className="max-h-32 space-y-1 overflow-y-auto">
            {memories.length === 0 && (
              <li className="text-[11px] text-ink-faint">Nothing yet — memories build up as you chat.</li>
            )}
            {memories.map((m) => (
              <li key={m.id} className="group flex items-center gap-2 text-[12px] text-ink-mid">
                <span className="flex-1 truncate" title={m.text}>• {m.text}</span>
                <button
                  onClick={() => void removeMemory(m.id)}
                  className="hidden text-ink-faint hover:text-danger group-hover:block"
                  aria-label="Forget this"
                >
                  <Trash2 size={11} />
                </button>
              </li>
            ))}
          </ul>
        </div>
        <div className="mt-4">
          <span className="mb-1 block text-xs font-medium text-ink-mid">Personas</span>
          <ul className="mb-2 space-y-1">
            {personas.map((p) => (
              <li key={p.id} className="group flex items-center gap-2 text-[12px] text-ink-mid">
                <span className="flex-1 truncate" title={p.system_prompt}>• {p.name}</span>
                <button
                  onClick={() => void removePersona(p.id)}
                  className="hidden text-ink-faint hover:text-danger group-hover:block"
                  aria-label={`Delete ${p.name}`}
                >
                  <Trash2 size={11} />
                </button>
              </li>
            ))}
          </ul>
          <div className="flex gap-1.5">
            <input
              value={newPersonaName}
              onChange={(e) => setNewPersonaName(e.target.value)}
              placeholder="Name"
              className="w-28 rounded border border-line-strong bg-panel px-2 py-1 text-xs text-ink"
            />
            <input
              value={newPersonaPrompt}
              onChange={(e) => setNewPersonaPrompt(e.target.value)}
              placeholder="Persona instructions…"
              className="flex-1 rounded border border-line-strong bg-panel px-2 py-1 text-xs text-ink"
            />
            <button
              onClick={() => void addPersona()}
              className="text-xs font-medium text-accent-strong"
            >
              Add
            </button>
          </div>
        </div>
        <div className="mt-4">
          <span className="mb-1 block text-xs font-medium text-ink-mid">Integrations</span>
          <a
            href={`${API_BASE}/api/integrations/google/authorize`}
            className="clip-btn inline-block rounded-lg border border-line-strong px-3 py-1.5 text-xs font-medium text-ink-mid hover:bg-surface"
          >
            Connect Google (Gmail + Calendar, read-only)
          </a>
          <p className="mt-1 text-[10px] text-ink-faint">
            Requires the server to be configured with a Google OAuth client secret.
          </p>
        </div>
        <label className="mt-4 flex items-center gap-2 text-sm text-ink-mid">
          <input
            type="checkbox"
            checked={autoRead}
            onChange={(e) => setAutoRead(e.target.checked)}
            className="accent-current"
          />
          Read answers aloud automatically
        </label>
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="clip-btn rounded-lg border border-line-strong px-3 py-1.5 text-sm text-ink-mid hover:bg-surface"
          >
            Cancel
          </button>
          <button
            onClick={() => void save()}
            disabled={saving}
            className="clip-btn rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-btn-ink hover:bg-accent-hover disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

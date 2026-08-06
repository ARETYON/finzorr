// Settings: custom instructions (server-side) + auto-read toggle (client-side).

import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import toast from 'react-hot-toast'
import { updateMe } from '../api/auth'
import { useAuthStore } from '../store/authStore'
import { useSettingsStore } from '../store/settingsStore'

export default function SettingsModal({ onClose }: { onClose: () => void }) {
  const user = useAuthStore((s) => s.user)
  const { autoRead, setAutoRead } = useSettingsStore()
  const [instructions, setInstructions] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setInstructions(user?.custom_instructions ?? '')
  }, [user])

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
        className="clip-panel fui-brackets w-full max-w-md rounded-2xl border border-line bg-panel p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="app-title text-sm font-semibold text-ink-strong">Settings</h2>
          <button onClick={onClose} className="text-ink-faint hover:text-ink-mid" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <label className="mb-1 block text-xs font-medium text-ink-mid">
          Custom instructions (applied to every conversation)
        </label>
        <textarea
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          rows={4}
          maxLength={2000}
          placeholder="e.g. Always answer briefly. Explain finance terms like I'm a beginner."
          className="clip-panel w-full resize-none rounded-lg border border-line-strong bg-panel px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-accent-strong focus:outline-none"
        />
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

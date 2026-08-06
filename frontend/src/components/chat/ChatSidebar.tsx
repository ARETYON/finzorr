import { useState } from 'react'
import { Check, LogOut, MessageSquare, Pencil, Plus, Settings, Trash2, X } from 'lucide-react'
import clsx from 'clsx'
import { useNavigate } from 'react-router-dom'
import SettingsModal from '../SettingsModal'
import ThemeToggle from '../ThemeToggle'
import { useAuthStore } from '../../store/authStore'
import { useChatStore } from '../../store/chatStore'
import FileUpload from './FileUpload'
import SearchBox from './SearchBox'
import WatchlistPanel from './WatchlistPanel'

export default function ChatSidebar({ watchlistRefreshKey }: { watchlistRefreshKey: number }) {
  const { sessions, activeSessionId, selectSession, newSession, rename, remove } = useChatStore()
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [showSettings, setShowSettings] = useState(false)

  const startEdit = (id: string, title: string | null) => {
    setEditingId(id)
    setEditTitle(title ?? '')
  }

  const saveEdit = async () => {
    if (editingId && editTitle.trim()) await rename(editingId, editTitle.trim())
    setEditingId(null)
  }

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  return (
    <aside className="flex w-64 flex-col border-r border-line bg-panel">
      <div className="p-3">
        <div className="fui-label mb-2 pl-1">
          finzorr // ops console
        </div>
        <button
          onClick={() => void newSession()}
          className="clip-btn flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-btn-ink hover:bg-accent-hover"
        >
          <Plus size={16} /> New chat
        </button>
      </div>
      <SearchBox />
      <div className="fui-label px-3 pb-1">session log</div>
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2">
        {sessions.map((s) => (
          <div
            key={s.id}
            className={clsx(
              'group flex items-center gap-2 rounded-lg px-2 py-2 text-sm',
              s.id === activeSessionId
                ? 'bg-accent-soft text-accent-strong'
                : 'hover:bg-surface',
            )}
          >
            {editingId === s.id ? (
              <>
                <input
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && void saveEdit()}
                  className="w-full rounded border border-line-strong bg-panel px-1.5 py-0.5 text-xs text-ink"
                  autoFocus
                />
                <button onClick={() => void saveEdit()} aria-label="Save title">
                  <Check size={14} className="text-ok" />
                </button>
                <button onClick={() => setEditingId(null)} aria-label="Cancel rename">
                  <X size={14} className="text-ink-faint" />
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={() => void selectSession(s.id)}
                  className="flex min-w-0 flex-1 items-center gap-2 text-left"
                >
                  <MessageSquare size={14} className="shrink-0 text-ink-faint" />
                  <span className="truncate">{s.title ?? 'New chat'}</span>
                </button>
                <button
                  onClick={() => startEdit(s.id, s.title)}
                  className="hidden shrink-0 text-ink-faint hover:text-ink-mid group-hover:block"
                  aria-label="Rename chat"
                >
                  <Pencil size={13} />
                </button>
                <button
                  onClick={() => void remove(s.id)}
                  className="hidden shrink-0 text-ink-faint hover:text-danger group-hover:block"
                  aria-label="Delete chat"
                >
                  <Trash2 size={13} />
                </button>
              </>
            )}
          </div>
        ))}
      </nav>
      <div className="fui-hatch fui-only h-1.5 w-full" />
      <WatchlistPanel refreshKey={watchlistRefreshKey} />
      <FileUpload />
      <div className="border-t border-line p-3">
        <div className="flex items-center justify-between gap-2 text-xs text-ink-dim">
          <span className="truncate">{user?.name}</span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowSettings(true)}
              className="text-ink-faint hover:text-ink-mid"
              aria-label="Settings"
            >
              <Settings size={14} />
            </button>
            <ThemeToggle compact />
            <button
              onClick={() => void handleLogout()}
              className="text-ink-faint hover:text-ink-mid"
              aria-label="Log out"
            >
              <LogOut size={14} />
            </button>
          </div>
        </div>
      </div>
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
    </aside>
  )
}

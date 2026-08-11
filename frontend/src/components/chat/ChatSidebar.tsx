import { useState } from 'react'
import { Check, LogOut, MessageSquare, Pencil, Plus, Settings, Trash2, X } from 'lucide-react'
import clsx from 'clsx'
import { useNavigate } from 'react-router'
import SettingsModal from '../SettingsModal'
import ThemeToggle from '../ThemeToggle'
import { useAuthStore } from '../../store/authStore'
import { useChatStore } from '../../store/chatStore'
import DocumentsContainer from '../../features/documents/DocumentsContainer'
import SearchContainer from '../../features/search/SearchContainer'
import WatchlistContainer from '../../features/watchlist/WatchlistContainer'

interface ChatSidebarProps {
  watchlistRefreshKey: number
  /** Mobile-only: whether the drawer is open. Always visible at md+ regardless. */
  open: boolean
  onClose: () => void
}

export default function ChatSidebar({ watchlistRefreshKey, open, onClose }: ChatSidebarProps) {
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

  const selectAndClose = (id: string) => {
    void selectSession(id)
    onClose() // a session pick on mobile should dismiss the drawer
  }

  return (
    <>
      {/* Backdrop: mobile only, dismisses the drawer on tap-outside */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}
      <aside
        className={clsx(
          'flex w-64 flex-col border-r border-line bg-panel',
          // Mobile: fixed slide-in drawer, off-canvas by default.
          // md+: back in normal flow, always visible, transform reset.
          'fixed inset-y-0 left-0 z-50 transition-transform duration-200 ease-in-out',
          'md:static md:z-auto md:translate-x-0 md:transition-none',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
      >
      <div className="p-3">
        <div className="mb-2 flex items-center justify-between pl-1">
          <div className="fui-label">
            finzorr // ops console
          </div>
          <button
            onClick={onClose}
            className="text-ink-faint hover:text-ink-mid md:hidden"
            aria-label="Close menu"
          >
            <X size={18} />
          </button>
        </div>
        <button
          onClick={() => {
            void newSession()
            onClose()
          }}
          className="clip-btn flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-btn-ink hover:bg-accent-hover"
        >
          <Plus size={16} /> New chat
        </button>
      </div>
      <SearchContainer />
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
                  ref={(el) => el?.focus()}
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
                  onClick={() => selectAndClose(s.id)}
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
      <WatchlistContainer refreshKey={watchlistRefreshKey} />
      <DocumentsContainer />
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
    </>
  )
}

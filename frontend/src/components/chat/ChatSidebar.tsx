import { useState } from 'react'
import { Check, LogOut, MessageSquare, Pencil, Plus, Trash2, X } from 'lucide-react'
import clsx from 'clsx'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../../store/authStore'
import { useChatStore } from '../../store/chatStore'
import FileUpload from './FileUpload'
import WatchlistPanel from './WatchlistPanel'

export default function ChatSidebar({ watchlistRefreshKey }: { watchlistRefreshKey: number }) {
  const { sessions, activeSessionId, selectSession, newSession, rename, remove } = useChatStore()
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')

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
    <aside className="flex w-64 flex-col border-r border-slate-200 bg-white">
      <div className="p-3">
        <button
          onClick={() => void newSession()}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700"
        >
          <Plus size={16} /> New chat
        </button>
      </div>
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2">
        {sessions.map((s) => (
          <div
            key={s.id}
            className={clsx(
              'group flex items-center gap-2 rounded-lg px-2 py-2 text-sm',
              s.id === activeSessionId ? 'bg-brand-50 text-brand-700' : 'hover:bg-slate-50',
            )}
          >
            {editingId === s.id ? (
              <>
                <input
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && void saveEdit()}
                  className="w-full rounded border border-slate-300 px-1.5 py-0.5 text-xs"
                  autoFocus
                />
                <button onClick={() => void saveEdit()} aria-label="Save title">
                  <Check size={14} className="text-emerald-600" />
                </button>
                <button onClick={() => setEditingId(null)} aria-label="Cancel rename">
                  <X size={14} className="text-slate-400" />
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={() => void selectSession(s.id)}
                  className="flex min-w-0 flex-1 items-center gap-2 text-left"
                >
                  <MessageSquare size={14} className="shrink-0 text-slate-400" />
                  <span className="truncate">{s.title ?? 'New chat'}</span>
                </button>
                <button
                  onClick={() => startEdit(s.id, s.title)}
                  className="hidden shrink-0 text-slate-400 hover:text-slate-600 group-hover:block"
                  aria-label="Rename chat"
                >
                  <Pencil size={13} />
                </button>
                <button
                  onClick={() => void remove(s.id)}
                  className="hidden shrink-0 text-slate-400 hover:text-rose-600 group-hover:block"
                  aria-label="Delete chat"
                >
                  <Trash2 size={13} />
                </button>
              </>
            )}
          </div>
        ))}
      </nav>
      <WatchlistPanel refreshKey={watchlistRefreshKey} />
      <FileUpload />
      <div className="border-t border-slate-200 p-3">
        <div className="flex items-center justify-between text-xs text-slate-500">
          <span className="truncate">{user?.name}</span>
          <button
            onClick={() => void handleLogout()}
            className="text-slate-400 hover:text-slate-600"
            aria-label="Log out"
          >
            <LogOut size={14} />
          </button>
        </div>
      </div>
    </aside>
  )
}

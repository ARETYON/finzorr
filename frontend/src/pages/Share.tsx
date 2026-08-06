// Public read-only shared conversation (no auth).

import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import { Sparkles } from 'lucide-react'
import { fetchShared, type SharedChat } from '../api/extras'

export default function Share() {
  const { token } = useParams<{ token: string }>()
  const [chat, setChat] = useState<SharedChat | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    if (!token) return
    fetchShared(token)
      .then(setChat)
      .catch(() => setError(true))
  }, [token])

  if (error) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-ink-faint">
        This share link is invalid or has been removed.
      </div>
    )
  }
  if (!chat) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-ink-faint">
        Loading…
      </div>
    )
  }
  return (
    <div className="h-full overflow-y-auto">
      <header className="border-b border-line bg-panel px-4 py-3">
        <div className="mx-auto flex max-w-3xl items-center justify-between">
          <h1 className="app-title truncate text-sm font-semibold text-ink-strong">
            {chat.title || 'Shared chat'}
          </h1>
          <span className="inline-flex items-center gap-1 text-xs text-accent-strong">
            <Sparkles size={13} /> finzorr.ai
          </span>
        </div>
      </header>
      <div className="mx-auto max-w-3xl space-y-4 px-4 py-6">
        {chat.messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={
                m.role === 'user'
                  ? 'msg-user clip-panel max-w-[85%] rounded-2xl bg-bubble-user px-4 py-2.5 text-sm text-bubble-user-ink'
                  : 'msg-assistant clip-panel max-w-[85%] rounded-2xl border border-line bg-panel px-4 py-2.5 text-sm text-ink'
              }
            >
              {m.role === 'user' ? (
                <p className="whitespace-pre-wrap">{m.content}</p>
              ) : (
                <div className="prose prose-sm prose-slate max-w-none">
                  <ReactMarkdown>{m.content}</ReactMarkdown>
                </div>
              )}
            </div>
          </div>
        ))}
        <p className="pt-4 text-center text-[11px] text-ink-faint">
          Shared read-only from finzorr.ai — market data may be delayed; not investment advice.
        </p>
      </div>
    </div>
  )
}

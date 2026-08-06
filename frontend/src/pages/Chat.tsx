import { useCallback, useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import ChatSidebar from '../components/chat/ChatSidebar'
import MessageBubble from '../components/chat/MessageBubble'
import MessageInput from '../components/chat/MessageInput'
import { useChatSocket } from '../hooks/useChatSocket'
import { useChatStore } from '../store/chatStore'
import type { ServerFrame } from '../types'

const STREAMING_ID = '__streaming__'

export default function Chat() {
  const { sessions, activeSessionId, messages, loadSessions, newSession, setMessages } =
    useChatStore()
  const [streaming, setStreaming] = useState(false)
  const [thinking, setThinking] = useState(false)
  const [watchlistRefreshKey, setWatchlistRefreshKey] = useState(0)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void loadSessions()
  }, [loadSessions])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, thinking])

  const onFrame = useCallback(
    (frame: ServerFrame) => {
      switch (frame.type) {
        case 'thinking':
          setThinking(true)
          break
        case 'token':
          setThinking(false)
          setStreaming(true)
          setMessages((prev) => {
            const last = prev[prev.length - 1]
            if (last?.id === STREAMING_ID) {
              return [...prev.slice(0, -1), { ...last, content: last.content + frame.delta }]
            }
            return [
              ...prev,
              { id: STREAMING_ID, role: 'assistant', content: frame.delta, streaming: true },
            ]
          })
          break
        case 'response':
          setThinking(false)
          setStreaming(false)
          setMessages((prev) => [
            ...prev.filter((m) => m.id !== STREAMING_ID),
            {
              id: frame.message_id,
              role: 'assistant',
              content: frame.message,
              route: frame.route,
              citations: frame.citations,
              tool_calls: frame.tool_calls,
              data_as_of: frame.data_as_of,
              sources: frame.sources,
            },
          ])
          void useChatStore.getState().loadSessions() // pick up auto-titles
          setWatchlistRefreshKey((k) => k + 1) // reflect chat-driven watchlist edits
          break
        case 'stopped':
          setThinking(false)
          setStreaming(false)
          setMessages((prev) =>
            prev.map((m) => (m.id === STREAMING_ID ? { ...m, id: '', streaming: false } : m)),
          )
          break
        case 'error':
          setThinking(false)
          setStreaming(false)
          toast.error(frame.message)
          break
        default:
          break
      }
    },
    [setMessages],
  )

  const { connected, sendChat, cancel } = useChatSocket(onFrame)

  const handleSend = async (text: string) => {
    let sessionId = activeSessionId
    if (!sessionId) sessionId = await newSession()
    setMessages((prev) => [...prev, { id: `local-${Date.now()}`, role: 'user', content: text }])
    sendChat(sessionId, text)
  }

  const activeTitle = sessions.find((s) => s.id === activeSessionId)?.title ?? 'finzorr.ai'

  return (
    <div className="flex h-full">
      <ChatSidebar watchlistRefreshKey={watchlistRefreshKey} />
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-line bg-panel px-4 py-2.5">
          <div className="flex items-center gap-3">
            <h1 className="app-title fui-glow max-w-md truncate text-sm font-semibold text-ink-strong">
              {activeTitle}
            </h1>
            <span className="fui-label">dossier · active thread</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="fui-only fui-mono items-center gap-2 text-[10px] tracking-[0.2em] text-ink-faint">
              LINK 01 · {connected ? 'SECURE' : 'RELINK'}
            </span>
            <span
              className={`status-dot h-2 w-2 rounded-full ${connected ? 'bg-ok' : 'bg-warn'}`}
              title={connected ? 'Connected' : 'Reconnecting…'}
            />
          </div>
        </header>
        <div className="flex-1 overflow-y-auto px-4 py-4">
          <div className="mx-auto max-w-3xl space-y-4">
            {messages.length === 0 && !thinking && (
              <div className="pt-16 text-center text-sm text-ink-faint">
                Ask about a stock, screen the market, or just chat.
              </div>
            )}
            {messages.map((m, i) => (
              <MessageBubble key={m.id || i} message={m} />
            ))}
            {thinking && (
              <div className="flex justify-start">
                <div className="msg-assistant clip-panel animate-pulse rounded-2xl border border-line bg-panel px-4 py-2.5 text-sm text-ink-faint">
                  Thinking…
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </div>
        <MessageInput
          disabled={!connected}
          streaming={streaming || thinking}
          onSend={(t) => void handleSend(t)}
          onCancel={cancel}
        />
      </main>
    </div>
  )
}

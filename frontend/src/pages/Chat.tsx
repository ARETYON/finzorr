import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, Link2Off, Share2 } from 'lucide-react'
import toast from 'react-hot-toast'
import ArtifactPanel from '../components/chat/ArtifactPanel'
import { extractArtifact, type Artifact } from '../lib/artifact'
import PersonaPicker from '../components/chat/PersonaPicker'
import ChatSidebar from '../components/chat/ChatSidebar'
import { createShareLink, revokeShareLinks } from '../api/extras'
import MessageBubble from '../components/chat/MessageBubble'
import MessageInput from '../components/chat/MessageInput'
import { useChatSocket } from '../hooks/useChatSocket'
import { useChatStore } from '../store/chatStore'
import { speak, useSettingsStore } from '../store/settingsStore'
import type { ServerFrame } from '../types'

const STREAMING_ID = '__streaming__'

export default function Chat() {
  const { sessions, activeSessionId, messages, loadSessions, newSession, setMessages } =
    useChatStore()
  const [streaming, setStreaming] = useState(false)
  const [thinking, setThinking] = useState(false)
  const [watchlistRefreshKey, setWatchlistRefreshKey] = useState(0)
  const [prefill, setPrefill] = useState('')
  const [artifact, setArtifact] = useState<Artifact | null>(null)
  const [hasShareLink, setHasShareLink] = useState(false)
  const lastUserMsgRef = useRef('')
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void loadSessions()
  }, [loadSessions])

  useEffect(() => {
    setHasShareLink(false) // revoke button is per-conversation
  }, [activeSessionId])

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
              chart: frame.chart,
            },
          ])
          void useChatStore.getState().loadSessions() // pick up auto-titles
          setWatchlistRefreshKey((k) => k + 1) // reflect chat-driven watchlist edits
          if (useSettingsStore.getState().autoRead) speak(frame.message)
          {
            const doc = extractArtifact(frame.message)
            if (doc) setArtifact(doc)
          }
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
          // drop any partial streaming bubble — otherwise the next turn's
          // tokens append onto this orphaned fragment
          setMessages((prev) => prev.filter((m) => m.id !== STREAMING_ID))
          toast.error(frame.message)
          break
        default:
          break
      }
    },
    [setMessages],
  )

  const { connected, sendChat, cancel } = useChatSocket(onFrame)

  const handleSend = async (text: string, attachments?: string[]) => {
    let sessionId = activeSessionId
    if (!sessionId) sessionId = await newSession()
    lastUserMsgRef.current = text
    const shown = attachments?.length ? `${text} 🖼️` : text
    setMessages((prev) => [...prev, { id: `local-${Date.now()}`, role: 'user', content: shown }])
    sendChat(sessionId, text, attachments)
  }

  const handleRegenerate = () => {
    const last =
      lastUserMsgRef.current ||
      [...messages].reverse().find((m) => m.role === 'user')?.content ||
      ''
    if (last && activeSessionId) void handleSend(last)
  }

  const shareChat = async () => {
    if (!activeSessionId) return
    try {
      const token = await createShareLink(activeSessionId)
      const url = `${location.origin}/share/${token}`
      await navigator.clipboard.writeText(url)
      setHasShareLink(true)
      toast.success('Share link copied to clipboard')
    } catch {
      toast.error('Could not create share link')
    }
  }

  const revokeShares = async () => {
    if (!activeSessionId) return
    try {
      await revokeShareLinks(activeSessionId)
      setHasShareLink(false)
      toast.success('All share links for this chat revoked')
    } catch {
      toast.error('No share links to revoke')
    }
  }

  const exportChat = () => {
    const title = sessions.find((s) => s.id === activeSessionId)?.title ?? 'finzorr-chat'
    const md = messages
      .map((m) => `**${m.role === 'user' ? 'You' : 'finzorr'}:**\n\n${m.content}`)
      .join('\n\n---\n\n')
    const blob = new Blob([`# ${title}\n\n${md}\n`], { type: 'text/markdown' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${title.replace(/[^a-z0-9-_ ]/gi, '').trim() || 'chat'}.md`
    a.click()
    URL.revokeObjectURL(a.href)
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
            <PersonaPicker sessionId={activeSessionId} />
            {messages.length > 0 && (
              <button
                onClick={() => void shareChat()}
                className="text-ink-faint hover:text-ink-mid"
                aria-label="Share conversation"
                title="Copy public share link"
              >
                <Share2 size={14} />
              </button>
            )}
            {hasShareLink && (
              <button
                onClick={() => void revokeShares()}
                className="text-ink-faint hover:text-danger"
                aria-label="Revoke share links"
                title="Revoke all share links for this chat"
              >
                <Link2Off size={14} />
              </button>
            )}
            {messages.length > 0 && (
              <button
                onClick={exportChat}
                className="text-ink-faint hover:text-ink-mid"
                aria-label="Export conversation"
                title="Export as Markdown"
              >
                <Download size={14} />
              </button>
            )}
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
            {messages.map((m, i) => {
              const isLastAssistant =
                m.role === 'assistant' && i === messages.length - 1 && !m.streaming
              const isLastUser =
                m.role === 'user' && !messages.slice(i + 1).some((x) => x.role === 'user')
              return (
                <MessageBubble
                  key={m.id || i}
                  message={m}
                  onRegenerate={isLastAssistant ? handleRegenerate : undefined}
                  onEdit={isLastUser ? (content) => setPrefill(content) : undefined}
                />
              )
            })}
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
          onSend={(t, a) => void handleSend(t, a)}
          onCancel={cancel}
          prefill={prefill}
          onPrefillConsumed={() => setPrefill('')}
        />
      </main>
      {artifact && <ArtifactPanel artifact={artifact} onClose={() => setArtifact(null)} />}
    </div>
  )
}

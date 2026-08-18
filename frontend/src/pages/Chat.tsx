import { useEffect, useRef, useState } from 'react'
import { Download, Link2Off, Menu, Share2 } from 'lucide-react'
import toast from 'react-hot-toast'
import ArtifactPanel from '../components/chat/ArtifactPanel'
import PersonaPicker from '../features/personas/PersonaPicker'
import ChatSidebar from '../components/chat/ChatSidebar'
import { createShareLink, fetchPendingApproval, revokeShareLinks } from '../api/extras'
import MessageBubble from '../components/chat/MessageBubble'
import MessageInput from '../components/chat/MessageInput'
import { useChatSocket } from '../hooks/useChatSocket'
import { useChatTurn } from '../features/chat/useChatTurn'
import { useChatStore } from '../store/chatStore'

export default function Chat() {
  const { sessions, activeSessionId, messages, loadSessions, newSession, setMessages } =
    useChatStore()
  const {
    streaming,
    thinking,
    routing,
    approval,
    setApproval,
    artifact,
    setArtifact,
    watchlistRefreshKey,
    onFrame,
  } = useChatTurn()
  const [prefill, setPrefill] = useState('')
  const [hasShareLink, setHasShareLink] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const lastUserMsgRef = useRef('')
  const scrollerRef = useRef<HTMLDivElement>(null)
  // stick-to-bottom: auto-scroll only while the user is already at the
  // bottom; scrolling up to re-read pauses following until they return
  const stickToBottomRef = useRef(true)
  const scrollRafRef = useRef(0)
  const prevMsgCountRef = useRef(0)

  useEffect(() => {
    void loadSessions()
  }, [loadSessions])

  useEffect(() => {
    setHasShareLink(false) // revoke button is per-conversation
    setApproval(null)
    if (activeSessionId) {
      // a reload mid-approval must re-surface the parked banner
      void fetchPendingApproval(activeSessionId)
        .then((p) => {
          if (p.pending) setApproval({ tools: p.tools, sessionId: activeSessionId })
          return undefined
        })
        .catch(() => undefined)
    }
  }, [activeSessionId, setApproval])

  const onScrollerScroll = () => {
    const el = scrollerRef.current
    if (!el) return
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  useEffect(() => {
    // smooth only when a NEW bubble appears; token growth scrolls instantly.
    // Restarting a smooth animation per token is what made the UI lurch on
    // long streamed answers, so streaming updates use rAF + instant jumps.
    const isNewMessage = messages.length !== prevMsgCountRef.current
    prevMsgCountRef.current = messages.length
    const el = scrollerRef.current
    if (!el || !stickToBottomRef.current) return
    cancelAnimationFrame(scrollRafRef.current)
    scrollRafRef.current = requestAnimationFrame(() => {
      el.scrollTo({ top: el.scrollHeight, behavior: isNewMessage ? 'smooth' : 'auto' })
    })
    return () => cancelAnimationFrame(scrollRafRef.current)
  }, [messages, thinking])

  const { connected, sendChat, cancel, sendApproval } = useChatSocket(onFrame)

  const handleSend = async (text: string, attachments?: string[]) => {
    let sessionId = activeSessionId
    if (!sessionId) sessionId = await newSession()
    lastUserMsgRef.current = text
    stickToBottomRef.current = true // sending always snaps back to the bottom
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
    <div className="flex h-dvh">
      <ChatSidebar
        watchlistRefreshKey={watchlistRefreshKey}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-line bg-panel px-4 py-2.5">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setSidebarOpen(true)}
              className="text-ink-faint hover:text-ink-mid md:hidden"
              aria-label="Open menu"
            >
              <Menu size={18} />
            </button>
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
        <div ref={scrollerRef} onScroll={onScrollerScroll} className="flex-1 overflow-y-auto px-4 py-4">
          {/* polite live region: screen readers announce streamed replies */}
          <div className="mx-auto max-w-3xl space-y-4" aria-live="polite" aria-busy={streaming}>
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
                  {routing ?? 'Thinking…'}
                </div>
              </div>
            )}
          </div>
        </div>
        {approval && (
          <div
            role="alertdialog"
            aria-label="Tool approval required"
            className="mx-auto mb-2 flex w-full max-w-3xl items-center gap-3 rounded-xl border border-line-strong bg-panel px-4 py-2.5 text-sm"
          >
            <span className="flex-1 text-ink-mid">
              Allow running{' '}
              <strong>{approval.tools.map((t) => t.name).join(', ')}</strong>?
            </span>
            <button
              onClick={() => {
                sendApproval(approval.sessionId, true)
                setApproval(null)
              }}
              className="rounded-lg bg-accent-strong px-3 py-1 text-xs font-semibold text-white"
            >
              Approve
            </button>
            <button
              onClick={() => {
                sendApproval(approval.sessionId, false)
                setApproval(null)
              }}
              className="rounded-lg border border-line-strong px-3 py-1 text-xs font-semibold text-ink-mid"
            >
              Decline
            </button>
          </div>
        )}
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

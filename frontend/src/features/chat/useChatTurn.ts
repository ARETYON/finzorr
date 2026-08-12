// Chat turn state: reduces incoming WS frames (thinking/routing/token/
// approval_required/response/stopped/error) into the render state Chat.tsx
// needs, and hands back the onFrame callback to wire into useChatSocket.

import { useCallback, useState } from 'react'
import toast from 'react-hot-toast'
import { extractArtifact, type Artifact } from '../../lib/artifact'
import { useChatStore } from '../../store/chatStore'
import { speak, useSettingsStore } from '../../store/settingsStore'
import type { ServerFrame } from '../../types'

const STREAMING_ID = '__streaming__'
const TRANSIENT_STEP_PREFIX = '__step__'

export interface ApprovalRequest {
  tools: { name: string }[]
  sessionId: string
}

export function useChatTurn() {
  const setMessages = useChatStore((s) => s.setMessages)
  const [streaming, setStreaming] = useState(false)
  const [thinking, setThinking] = useState(false)
  const [routing, setRouting] = useState<string | null>(null)
  const [approval, setApproval] = useState<ApprovalRequest | null>(null)
  const [artifact, setArtifact] = useState<Artifact | null>(null)
  const [watchlistRefreshKey, setWatchlistRefreshKey] = useState(0)

  const onFrame = useCallback(
    (frame: ServerFrame) => {
      switch (frame.type) {
        case 'thinking':
          setThinking(true)
          setRouting(null)
          break
        case 'routing':
          setThinking(true)
          setRouting(
            frame.of && frame.of > 1
              ? `Step ${frame.step}/${frame.of} — ${frame.route}…`
              : `Routing to ${frame.route}…`,
          )
          // step boundary: seal the current stream into a transient step
          // bubble so the next step (or compose) streams separately instead
          // of accreting into one blob
          if ((frame.step && frame.step > 1) || frame.route === 'compose' || frame.route === 'replan') {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === STREAMING_ID
                  ? { ...m, id: `${TRANSIENT_STEP_PREFIX}${prev.length}`, streaming: false }
                  : m,
              ),
            )
          }
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
        case 'approval_required':
          setThinking(false)
          setStreaming(false)
          setRouting(null)
          setApproval({ tools: frame.tools, sessionId: frame.session_id })
          break
        case 'response':
          setThinking(false)
          setStreaming(false)
          setRouting(null)
          setMessages((prev) => [
            ...prev.filter(
              (m) => m.id !== STREAMING_ID && !m.id.startsWith(TRANSIENT_STEP_PREFIX),
            ),
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
          if (useSettingsStore.getState().autoRead) speak(frame.message, frame.message_id)
          {
            const doc = extractArtifact(frame.message)
            if (doc) setArtifact(doc)
          }
          break
        case 'stopped':
          setThinking(false)
          setStreaming(false)
          setRouting(null)
          setMessages((prev) =>
            prev
              .filter((m) => !m.id.startsWith(TRANSIENT_STEP_PREFIX))
              .map((m) => (m.id === STREAMING_ID ? { ...m, id: '', streaming: false } : m)),
          )
          break
        case 'error':
          setThinking(false)
          setStreaming(false)
          setRouting(null)
          // drop any partial streaming bubble — otherwise the next turn's
          // tokens append onto this orphaned fragment
          setMessages((prev) =>
            prev.filter(
              (m) => m.id !== STREAMING_ID && !m.id.startsWith(TRANSIENT_STEP_PREFIX),
            ),
          )
          toast.error(frame.message)
          break
        default:
          break
      }
    },
    [setMessages],
  )

  return {
    streaming,
    thinking,
    routing,
    approval,
    setApproval,
    artifact,
    setArtifact,
    watchlistRefreshKey,
    onFrame,
  }
}

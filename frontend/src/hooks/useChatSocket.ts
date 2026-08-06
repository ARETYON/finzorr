// WebSocket chat hook: exponential-backoff reconnect (1s -> 30s), 25s ping
// keepalive, offline send queue, mid-stream cancel. One socket per app session.

import { useCallback, useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import { wsUrl } from '../api/client'
import type { ServerFrame } from '../types'

const PING_INTERVAL_MS = 25_000
const MAX_BACKOFF_MS = 30_000

export type FrameHandler = (frame: ServerFrame) => void

export function useChatSocket(onFrame: FrameHandler) {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectAttempt = useRef(0)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const closedByCleanup = useRef(false)
  const pendingSends = useRef<string[]>([])
  const onFrameRef = useRef<FrameHandler>(onFrame)
  const [connected, setConnected] = useState(false)
  onFrameRef.current = onFrame

  const connect = useCallback(() => {
    const ws = new WebSocket(wsUrl('/ws/chat'))
    wsRef.current = ws
    ws.onopen = () => {
      reconnectAttempt.current = 0
      setConnected(true)
      for (const msg of pendingSends.current.splice(0)) ws.send(msg)
    }
    ws.onmessage = (event) => {
      try {
        onFrameRef.current(JSON.parse(event.data) as ServerFrame)
      } catch {
        // malformed frame — ignore
      }
    }
    ws.onclose = () => {
      setConnected(false)
      // Unmount cleanup closes the socket too — reconnecting then would leak
      // an orphan authenticated socket per navigation (and per StrictMode
      // mount cycle in dev).
      if (closedByCleanup.current) return
      const delay = Math.min(1000 * 2 ** reconnectAttempt.current, MAX_BACKOFF_MS)
      reconnectAttempt.current += 1
      reconnectTimer.current = setTimeout(connect, delay)
    }
    ws.onerror = () => ws.close()
  }, [])

  useEffect(() => {
    closedByCleanup.current = false
    connect()
    const ping = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'ping' }))
      }
    }, PING_INTERVAL_MS)
    return () => {
      closedByCleanup.current = true
      clearInterval(ping)
      if (reconnectTimer.current !== null) clearTimeout(reconnectTimer.current)
      reconnectTimer.current = null
      wsRef.current?.close()
      wsRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const sendChat = useCallback((sessionId: string, message: string, attachments?: string[]) => {
    const payload = JSON.stringify({
      type: 'chat',
      session_id: sessionId,
      message,
      ...(attachments?.length ? { attachments } : {}),
    })
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(payload)
    } else {
      pendingSends.current.push(payload)
      toast('Reconnecting… your message will send shortly.')
    }
  }, [])

  const cancel = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'cancel' }))
    }
  }, [])

  return { connected, sendChat, cancel }
}

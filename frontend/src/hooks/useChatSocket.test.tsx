// Regression: unmount must not schedule a reconnect — the old cleanup closed
// the socket, onclose fired, and an orphan authenticated socket reconnected
// per navigation (and per StrictMode mount cycle in dev).

import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useChatSocket } from './useChatSocket'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static OPEN = 1
  readyState = 0
  url: string
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  sent: string[] = []

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    this.readyState = 3
    this.onclose?.()
  }
}

describe('useChatSocket', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('connects on mount and closes without reconnecting on unmount', () => {
    const { unmount } = renderHook(() => useChatSocket(() => undefined))
    expect(FakeWebSocket.instances).toHaveLength(1)

    unmount()
    act(() => {
      vi.advanceTimersByTime(60_000)
    })
    // no reconnect after cleanup — exactly one socket ever created
    expect(FakeWebSocket.instances).toHaveLength(1)
  })

  it('reconnects with backoff after a server-side drop', () => {
    renderHook(() => useChatSocket(() => undefined))
    const first = FakeWebSocket.instances[0]!
    act(() => first.close()) // dropped by the server, not by cleanup
    act(() => {
      vi.advanceTimersByTime(1_100)
    })
    expect(FakeWebSocket.instances.length).toBe(2)
  })

  it('queues sends while disconnected and flushes on open', () => {
    const { result } = renderHook(() => useChatSocket(() => undefined))
    const ws = FakeWebSocket.instances[0]!
    act(() => result.current.sendChat('sid-1', 'hello'))
    expect(ws.sent).toHaveLength(0) // still CONNECTING — queued
    act(() => {
      ws.readyState = FakeWebSocket.OPEN
      ws.onopen?.()
    })
    expect(ws.sent.some((s) => s.includes('hello'))).toBe(true)
  })

  it('delivers parsed frames to the handler', () => {
    const frames: unknown[] = []
    renderHook(() => useChatSocket((f) => frames.push(f)))
    const ws = FakeWebSocket.instances[0]!
    act(() => ws.onmessage?.({ data: '{"type":"pong"}' }))
    act(() => ws.onmessage?.({ data: 'not json' })) // ignored, no crash
    expect(frames).toEqual([{ type: 'pong' }])
  })
})

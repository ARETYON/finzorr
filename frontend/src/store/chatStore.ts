import { create } from 'zustand'
import * as chatApi from '../api/chat'
import type { ChatMessage, ChatSession } from '../types'

interface ChatState {
  sessions: ChatSession[]
  activeSessionId: string | null
  messages: ChatMessage[]
  loadSessions: () => Promise<void>
  selectSession: (id: string) => Promise<void>
  newSession: () => Promise<string>
  rename: (id: string, title: string) => Promise<void>
  remove: (id: string) => Promise<void>
  setMessages: (updater: (prev: ChatMessage[]) => ChatMessage[]) => void
}

export const useChatStore = create<ChatState>((set, get) => ({
  sessions: [],
  activeSessionId: null,
  messages: [],
  loadSessions: async () => {
    set({ sessions: await chatApi.listSessions() })
  },
  selectSession: async (id: string) => {
    set({ activeSessionId: id, messages: [] })
    set({ messages: await chatApi.listMessages(id) })
  },
  newSession: async () => {
    const session = await chatApi.createSession()
    set((s) => ({ sessions: [session, ...s.sessions], activeSessionId: session.id, messages: [] }))
    return session.id
  },
  rename: async (id: string, title: string) => {
    const updated = await chatApi.renameSession(id, title)
    set((s) => ({ sessions: s.sessions.map((x) => (x.id === id ? updated : x)) }))
  },
  remove: async (id: string) => {
    await chatApi.deleteSession(id)
    const { activeSessionId } = get()
    set((s) => ({
      sessions: s.sessions.filter((x) => x.id !== id),
      activeSessionId: activeSessionId === id ? null : activeSessionId,
      messages: activeSessionId === id ? [] : s.messages,
    }))
  },
  setMessages: (updater) => set((s) => ({ messages: updater(s.messages) })),
}))

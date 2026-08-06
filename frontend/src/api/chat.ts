import { api } from './client'
import type { ChatMessage, ChatSession, SearchHit } from '../types'

// v1 lists return the cursor envelope; the UI renders the newest window.
interface PageEnvelope<T> {
  items: T[]
  next_cursor: string | null
  total: number
}

export async function listSessions(): Promise<ChatSession[]> {
  const { data } = await api.get<PageEnvelope<ChatSession>>('/chat/sessions')
  return data.items
}

export async function createSession(): Promise<ChatSession> {
  const { data } = await api.post<ChatSession>('/chat/sessions')
  return data
}

export async function renameSession(id: string, title: string): Promise<ChatSession> {
  const { data } = await api.patch<ChatSession>(`/chat/sessions/${id}`, { title })
  return data
}

export async function deleteSession(id: string): Promise<void> {
  await api.delete(`/chat/sessions/${id}`)
}

export async function listMessages(sessionId: string): Promise<ChatMessage[]> {
  const { data } = await api.get<PageEnvelope<ChatMessage>>(`/chat/sessions/${sessionId}/messages`)
  return data.items
}

export async function sendFeedback(
  messageId: string,
  rating: 1 | -1,
  comment?: string,
): Promise<void> {
  await api.post(`/chat/messages/${messageId}/feedback`, { rating, comment })
}

export async function searchMessages(q: string): Promise<SearchHit[]> {
  const { data } = await api.get<SearchHit[]>('/chat/search', { params: { q } })
  return data
}

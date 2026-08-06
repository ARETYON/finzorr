import { api } from './client'
import type { ChatMessage, ChatSession, SearchHit } from '../types'

export async function listSessions(): Promise<ChatSession[]> {
  const { data } = await api.get<ChatSession[]>('/api/chat/sessions')
  return data
}

export async function createSession(): Promise<ChatSession> {
  const { data } = await api.post<ChatSession>('/api/chat/sessions')
  return data
}

export async function renameSession(id: string, title: string): Promise<ChatSession> {
  const { data } = await api.patch<ChatSession>(`/api/chat/sessions/${id}`, { title })
  return data
}

export async function deleteSession(id: string): Promise<void> {
  await api.delete(`/api/chat/sessions/${id}`)
}

export async function listMessages(sessionId: string): Promise<ChatMessage[]> {
  const { data } = await api.get<ChatMessage[]>(`/api/chat/sessions/${sessionId}/messages`)
  return data
}

export async function sendFeedback(
  messageId: string,
  rating: 1 | -1,
  comment?: string,
): Promise<void> {
  await api.post(`/api/chat/messages/${messageId}/feedback`, { rating, comment })
}

export async function searchMessages(q: string): Promise<SearchHit[]> {
  const { data } = await api.get<SearchHit[]>('/api/chat/search', { params: { q } })
  return data
}

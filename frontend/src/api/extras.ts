import { api } from './client'

export interface PersonaInfo {
  id: string
  name: string
  system_prompt: string
}

export async function listPersonas(): Promise<PersonaInfo[]> {
  const { data } = await api.get<PersonaInfo[]>('/personas')
  return data
}

export async function createPersona(name: string, systemPrompt: string): Promise<void> {
  await api.post('/personas', { name, system_prompt: systemPrompt })
}

export async function deletePersona(id: string): Promise<void> {
  await api.delete(`/personas/${id}`)
}

export async function setSessionPersona(sessionId: string, personaId: string | null): Promise<void> {
  await api.patch(`/chat/sessions/${sessionId}/persona`, { persona_id: personaId })
}

export async function createShareLink(sessionId: string): Promise<string> {
  const { data } = await api.post<{ token: string; expires_at: string | null }>(
    `/chat/sessions/${sessionId}/share`,
  )
  return data.token
}

export async function revokeShareLinks(sessionId: string): Promise<void> {
  await api.delete(`/chat/sessions/${sessionId}/share`)
}

export interface SharedChat {
  title: string
  messages: { role: string; content: string; route: string | null }[]
}

export async function fetchShared(token: string): Promise<SharedChat> {
  const { data } = await api.get<SharedChat>(`/share/${token}`)
  return data
}

export async function fetchPendingApproval(
  sessionId: string,
): Promise<{ pending: boolean; tools: { name: string }[] }> {
  const { data } = await api.get<{ pending: boolean; tools: { name: string }[] }>(
    `/chat/sessions/${sessionId}/pending-approval`,
  )
  return data
}

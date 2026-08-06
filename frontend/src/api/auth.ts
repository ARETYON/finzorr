import { api } from './client'
import type { User } from '../types'

export async function googleLogin(idToken: string): Promise<User> {
  const { data } = await api.post<User>('/api/auth/google', { id_token: idToken })
  return data
}

export async function devLogin(): Promise<User> {
  const { data } = await api.post<User>('/api/auth/dev-login')
  return data
}

export async function fetchMe(): Promise<User> {
  const { data } = await api.get<User>('/api/auth/me')
  return data
}

export async function logout(): Promise<void> {
  await api.post('/api/auth/logout')
}

export async function updateMe(customInstructions: string): Promise<User> {
  const { data } = await api.patch<User>('/api/auth/me', {
    custom_instructions: customInstructions,
  })
  return data
}

export interface MemoryFact {
  id: string
  text: string
}

export async function listMemories(): Promise<MemoryFact[]> {
  const { data } = await api.get<MemoryFact[]>('/api/auth/memories')
  return data
}

export async function deleteMemory(id: string): Promise<void> {
  await api.delete(`/api/auth/memories/${id}`)
}

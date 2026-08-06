import { api } from './client'

export interface DocumentInfo {
  id: string
  filename: string
  status: string
  chunks: number | null
  uploaded_at: string
}

export async function listDocuments(): Promise<DocumentInfo[]> {
  const { data } = await api.get<DocumentInfo[]>('/documents')
  return data
}

export async function uploadDocument(file: File): Promise<{ id: string; status: string }> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<{ id: string; status: string }>('/documents', form)
  return data
}

export async function deleteDocument(id: string): Promise<void> {
  await api.delete(`/documents/${id}`)
}

export interface WatchlistEntry {
  symbol: string
  exchange: string
  added_at: string
}

export async function getWatchlist(): Promise<WatchlistEntry[]> {
  const { data } = await api.get<WatchlistEntry[]>('/watchlist')
  return data
}

export async function addWatchlist(symbol: string): Promise<void> {
  await api.post('/watchlist', { symbol, exchange: 'NSE' })
}

export async function removeWatchlist(symbol: string): Promise<void> {
  await api.delete(`/watchlist/${symbol}`)
}

export async function uploadAttachment(file: File): Promise<{ token: string; mime: string }> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<{ token: string; mime: string }>('/chat/attachments', form)
  return data
}

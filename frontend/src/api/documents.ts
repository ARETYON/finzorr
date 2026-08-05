import { api } from './client'

export interface DocumentInfo {
  id: string
  filename: string
  status: string
  chunks: number | null
  uploaded_at: string
}

export async function listDocuments(): Promise<DocumentInfo[]> {
  const { data } = await api.get<DocumentInfo[]>('/api/documents')
  return data
}

export async function uploadDocument(file: File): Promise<{ id: string; status: string }> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<{ id: string; status: string }>('/api/documents', form)
  return data
}

export async function deleteDocument(id: string): Promise<void> {
  await api.delete(`/api/documents/${id}`)
}

export interface WatchlistEntry {
  symbol: string
  exchange: string
  added_at: string
}

export async function getWatchlist(): Promise<WatchlistEntry[]> {
  const { data } = await api.get<WatchlistEntry[]>('/api/watchlist')
  return data
}

export async function addWatchlist(symbol: string): Promise<void> {
  await api.post('/api/watchlist', { symbol, exchange: 'NSE' })
}

export async function removeWatchlist(symbol: string): Promise<void> {
  await api.delete(`/api/watchlist/${symbol}`)
}

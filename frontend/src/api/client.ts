import axios from 'axios'

// In dev the Vite proxy forwards relative /api paths to :8000.
// In uat/prod VITE_API_BASE_URL points at api(-uat).finzorr.ai and the session
// cookie rides along thanks to withCredentials.
export const API_BASE: string = import.meta.env.VITE_API_BASE_URL ?? ''

export const api = axios.create({
  baseURL: API_BASE,
  withCredentials: true,
})

export function wsUrl(path: string): string {
  if (API_BASE) return API_BASE.replace(/^http/, 'ws') + path
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}${path}`
}

import axios from 'axios'
import { useAuthStore } from '../store/authStore'

// In dev the Vite proxy forwards relative /api paths to :8000.
// In uat/prod VITE_API_BASE_URL points at api(-uat).finzorr.ai and the session
// cookie rides along thanks to withCredentials.
export const API_BASE: string = import.meta.env.VITE_API_BASE_URL ?? ''
// Canonical API version prefix; legacy /api stays server-side for compat.
export const API_PREFIX = '/api/v1'

export const api = axios.create({
  baseURL: `${API_BASE}${API_PREFIX}`,
  withCredentials: true,
})

// Central session-expiry handling: any 401 from any caller clears the auth
// store's user state the same way authStore's own load() catch does, so a
// session expiring mid-app (not just at initial load) is reflected in the
// UI immediately. Callers still do their own try/catch for their own
// request's error UX; this only reacts to the auth state. 5xx passes
// through unchanged.
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      useAuthStore.getState().clearSession()
    }
    return Promise.reject(error)
  },
)

export function wsUrl(path: string): string {
  if (API_BASE) return API_BASE.replace(/^http/, 'ws') + path
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}${path}`
}

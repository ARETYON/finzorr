import { create } from 'zustand'
import * as authApi from '../api/auth'
import type { User } from '../types'

interface AuthState {
  user: User | null
  loading: boolean
  load: () => Promise<void>
  loginGoogle: (idToken: string) => Promise<void>
  loginDev: () => Promise<void>
  logout: () => Promise<void>
  updateUser: (patch: Partial<User>) => void
  clearSession: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  loading: true,
  load: async () => {
    try {
      const user = await authApi.fetchMe()
      set({ user, loading: false })
    } catch {
      set({ user: null, loading: false })
    }
  },
  loginGoogle: async (idToken: string) => {
    const user = await authApi.googleLogin(idToken)
    set({ user })
  },
  loginDev: async () => {
    const user = await authApi.devLogin()
    set({ user })
  },
  logout: async () => {
    await authApi.logout()
    set({ user: null })
  },
  // Patch the current user in place (e.g. after a profile update) without
  // components reaching into the store's internals via setState directly.
  updateUser: (patch: Partial<User>) => {
    set((state) => ({ user: state.user ? { ...state.user, ...patch } : state.user }))
  },
  // Same effect as load()'s catch branch: drop the session. Used centrally
  // by the api client's 401 response interceptor so a session expiring
  // mid-app is handled the same way as a failed initial load.
  clearSession: () => {
    set({ user: null, loading: false })
  },
}))

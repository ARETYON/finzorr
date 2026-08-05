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
}))

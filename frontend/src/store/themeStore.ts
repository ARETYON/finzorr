import { create } from 'zustand'

export type Theme = 'light' | 'ops'

const STORAGE_KEY = 'finzorr_theme'

function initialTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY)
  return stored === 'ops' ? 'ops' : 'light'
}

interface ThemeState {
  theme: Theme
  setTheme: (theme: Theme) => void
}

export const useThemeStore = create<ThemeState>((set) => ({
  theme: initialTheme(),
  setTheme: (theme: Theme) => {
    localStorage.setItem(STORAGE_KEY, theme)
    document.documentElement.dataset.theme = theme
    set({ theme })
  },
}))

// Apply at module load so there is no flash of the wrong theme.
document.documentElement.dataset.theme = initialTheme()

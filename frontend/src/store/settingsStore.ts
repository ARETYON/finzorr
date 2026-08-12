// User preferences that live client-side (auto-read voice) — custom
// instructions live server-side on the user record.

import { create } from 'zustand'

const KEY = 'finzorr_settings'

interface Persisted {
  autoRead: boolean
}

function load(): Persisted {
  try {
    return { autoRead: false, ...JSON.parse(localStorage.getItem(KEY) ?? '{}') }
  } catch {
    return { autoRead: false }
  }
}

interface SettingsState extends Persisted {
  setAutoRead: (v: boolean) => void
  speakingMessageId: string | null
  setSpeaking: (id: string | null) => void
}

export const useSettingsStore = create<SettingsState>((set) => ({
  ...load(),
  setAutoRead: (autoRead: boolean) => {
    set({ autoRead })
    localStorage.setItem(KEY, JSON.stringify({ autoRead }))
  },
  speakingMessageId: null,
  setSpeaking: (speakingMessageId: string | null) => set({ speakingMessageId }),
}))

export function speak(text: string, messageId: string): void {
  if (!('speechSynthesis' in window)) return
  window.speechSynthesis.cancel()
  const utterance = new SpeechSynthesisUtterance(text.slice(0, 2000))
  utterance.rate = 1.05
  const clearIfCurrent = () => {
    if (useSettingsStore.getState().speakingMessageId === messageId) {
      useSettingsStore.getState().setSpeaking(null)
    }
  }
  utterance.onend = clearIfCurrent
  utterance.onerror = clearIfCurrent
  useSettingsStore.getState().setSpeaking(messageId)
  window.speechSynthesis.speak(utterance)
}

export function stopSpeaking(): void {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  useSettingsStore.getState().setSpeaking(null)
}

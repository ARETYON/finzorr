import { beforeEach, describe, expect, it } from 'vitest'
import { useSettingsStore } from './settingsStore'

describe('settingsStore', () => {
  beforeEach(() => localStorage.clear())

  it('defaults autoRead to off', () => {
    expect(useSettingsStore.getState().autoRead).toBe(false)
  })

  it('persists autoRead to localStorage', () => {
    useSettingsStore.getState().setAutoRead(true)
    expect(useSettingsStore.getState().autoRead).toBe(true)
    expect(JSON.parse(localStorage.getItem('finzorr_settings') ?? '{}').autoRead).toBe(true)
  })
})

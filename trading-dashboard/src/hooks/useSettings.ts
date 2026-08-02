import { useCallback, useState } from 'react'
import settingsJson from '../data/settings.json'
import type { AppSettings } from '../types/settings'

/**
 * Central settings state for the prototype.
 * Persists only in memory for now — swap for localStorage / API later.
 */
export function useSettings() {
  const [settings, setSettings] = useState<AppSettings>(
    () => settingsJson as AppSettings,
  )
  const [savedAt, setSavedAt] = useState<string | null>(null)

  const update = useCallback((patch: Partial<AppSettings>) => {
    setSettings((prev) => ({ ...prev, ...patch }))
  }, [])

  const save = useCallback(() => {
    // Mock save — no backend.
    setSavedAt(new Date().toISOString())
    return true
  }, [])

  return { settings, update, save, savedAt }
}

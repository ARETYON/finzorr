// Segmented Light / Ops theme switcher (persisted via themeStore).

import { Radar, Sun } from 'lucide-react'
import clsx from 'clsx'
import { useThemeStore, type Theme } from '../store/themeStore'

const OPTIONS: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'ops', label: 'Ops', icon: Radar },
]

export default function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { theme, setTheme } = useThemeStore()
  return (
    <div className="inline-flex overflow-hidden rounded-lg border border-line clip-btn">
      {OPTIONS.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          onClick={() => setTheme(value)}
          aria-label={`${label} theme`}
          className={clsx(
            'flex items-center gap-1 px-2 py-1 text-[11px] font-medium transition-colors',
            theme === value
              ? 'bg-accent text-btn-ink'
              : 'bg-panel text-ink-dim hover:text-ink-mid',
          )}
        >
          <Icon size={12} />
          {!compact && label}
        </button>
      ))}
    </div>
  )
}

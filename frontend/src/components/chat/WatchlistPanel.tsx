// Always-visible watchlist strip: chat-driven add/remove stays primary, this
// panel is the persistent secondary surface (refreshes after every turn).

import { useCallback, useEffect, useState } from 'react'
import { ListChecks, Plus, X } from 'lucide-react'
import toast from 'react-hot-toast'
import {
  addWatchlist,
  getWatchlist,
  removeWatchlist,
  type WatchlistEntry,
} from '../../api/documents'

export default function WatchlistPanel({ refreshKey }: { refreshKey: number }) {
  const [items, setItems] = useState<WatchlistEntry[]>([])
  const [adding, setAdding] = useState(false)
  const [symbol, setSymbol] = useState('')

  const refresh = useCallback(async () => {
    try {
      setItems(await getWatchlist())
    } catch {
      // panel is non-critical; stay quiet
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh, refreshKey])

  const add = async () => {
    const s = symbol.trim().toUpperCase()
    if (!s) return
    try {
      await addWatchlist(s)
      setSymbol('')
      setAdding(false)
      await refresh()
    } catch {
      toast.error('Could not add symbol')
    }
  }

  const remove = async (s: string) => {
    try {
      await removeWatchlist(s)
      await refresh()
    } catch {
      toast.error('Could not remove symbol')
    }
  }

  return (
    <div className="border-t border-line p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-ink-dim">
          <ListChecks size={13} /> Watchlist
        </span>
        <button
          onClick={() => setAdding(!adding)}
          className="text-ink-faint hover:text-accent-strong"
          aria-label="Add symbol"
        >
          <Plus size={14} />
        </button>
      </div>
      {adding && (
        <div className="mb-2 flex gap-1">
          <input
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void add()}
            placeholder="e.g. INFY"
            className="w-full rounded border border-line-strong bg-panel px-2 py-1 text-xs uppercase text-ink"
            autoFocus
          />
          <button onClick={() => void add()} className="text-xs font-medium text-accent-strong">
            Add
          </button>
        </div>
      )}
      <div className="flex flex-wrap gap-1.5">
        {items.length === 0 && <span className="text-[11px] text-ink-faint">Empty — try “add TCS to my watchlist”</span>}
        {items.map((w) => (
          <span
            key={w.symbol}
            className="inline-flex items-center gap-1 fui-mono rounded-full bg-chip px-2 py-0.5 text-[11px] font-medium text-ink-mid"
          >
            {w.symbol}
            <button
              onClick={() => void remove(w.symbol)}
              className="text-ink-faint hover:text-danger"
              aria-label={`Remove ${w.symbol}`}
            >
              <X size={10} />
            </button>
          </span>
        ))}
      </div>
    </div>
  )
}

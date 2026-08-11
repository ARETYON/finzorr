// Presentational: always-visible watchlist strip. All fetch/add/remove
// wiring lives in WatchlistContainer.

import { ListChecks, Plus, X } from 'lucide-react'
import type { WatchlistEntry } from '../../api/documents'

interface WatchlistViewProps {
  items: WatchlistEntry[]
  adding: boolean
  onToggleAdding: () => void
  symbol: string
  onSymbolChange: (symbol: string) => void
  onAdd: () => void
  onRemove: (symbol: string) => void
}

export default function WatchlistView({
  items,
  adding,
  onToggleAdding,
  symbol,
  onSymbolChange,
  onAdd,
  onRemove,
}: WatchlistViewProps) {
  return (
    <div className="border-t border-line p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-ink-dim">
          <ListChecks size={13} /> Watchlist
        </span>
        <button
          onClick={onToggleAdding}
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
            onChange={(e) => onSymbolChange(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && onAdd()}
            placeholder="e.g. INFY"
            className="w-full rounded border border-line-strong bg-panel px-2 py-1 text-xs uppercase text-ink"
            ref={(el) => el?.focus()}
          />
          <button onClick={onAdd} className="text-xs font-medium text-accent-strong">
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
              onClick={() => onRemove(w.symbol)}
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

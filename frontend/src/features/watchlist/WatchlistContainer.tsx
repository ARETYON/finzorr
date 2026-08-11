// Container: owns watchlist fetch/add/remove + loading state; renders the
// presentational WatchlistView with plain props.

import { useCallback, useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import { addWatchlist, getWatchlist, removeWatchlist, type WatchlistEntry } from '../../api/documents'
import WatchlistView from './WatchlistView'

export default function WatchlistContainer({ refreshKey }: { refreshKey: number }) {
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

  const add = () => {
    void (async () => {
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
    })()
  }

  const remove = (s: string) => {
    void (async () => {
      try {
        await removeWatchlist(s)
        await refresh()
      } catch {
        toast.error('Could not remove symbol')
      }
    })()
  }

  return (
    <WatchlistView
      items={items}
      adding={adding}
      onToggleAdding={() => setAdding(!adding)}
      symbol={symbol}
      onSymbolChange={setSymbol}
      onAdd={add}
      onRemove={remove}
    />
  )
}

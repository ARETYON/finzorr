// Sidebar chat-history search with debounced results dropdown.

import { useEffect, useRef, useState } from 'react'
import { Search, X } from 'lucide-react'
import { searchMessages } from '../../api/chat'
import { useChatStore } from '../../store/chatStore'
import type { SearchHit } from '../../types'

const DEBOUNCE_MS = 300

export default function SearchBox() {
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])
  const [open, setOpen] = useState(false)
  const timerRef = useRef<number>(0)
  const selectSession = useChatStore((s) => s.selectSession)

  useEffect(() => {
    window.clearTimeout(timerRef.current)
    if (!query.trim()) {
      setHits([])
      setOpen(false)
      return
    }
    timerRef.current = window.setTimeout(async () => {
      try {
        setHits(await searchMessages(query))
        setOpen(true)
      } catch {
        // search is non-critical
      }
    }, DEBOUNCE_MS)
    return () => window.clearTimeout(timerRef.current)
  }, [query])

  const jump = async (hit: SearchHit) => {
    setOpen(false)
    setQuery('')
    await selectSession(hit.session_id)
  }

  return (
    <div className="relative px-3 pb-2">
      <div className="flex items-center gap-1.5 rounded-lg border border-line bg-surface px-2 py-1.5">
        <Search size={13} className="shrink-0 text-ink-faint" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search chats…"
          className="w-full bg-transparent text-xs text-ink placeholder:text-ink-faint focus:outline-none"
        />
        {query && (
          <button onClick={() => setQuery('')} aria-label="Clear search">
            <X size={12} className="text-ink-faint" />
          </button>
        )}
      </div>
      {open && (
        <div className="clip-panel absolute left-3 right-3 z-40 mt-1 max-h-64 overflow-y-auto rounded-lg border border-line bg-panel shadow-lg">
          {hits.length === 0 && (
            <p className="px-3 py-2 text-[11px] text-ink-faint">No matches.</p>
          )}
          {hits.map((hit, i) => (
            <button
              key={i}
              onClick={() => void jump(hit)}
              className="block w-full border-b border-line px-3 py-2 text-left last:border-0 hover:bg-surface"
            >
              <span className="block truncate text-[11px] font-medium text-ink-mid">
                {hit.session_title}
              </span>
              <span className="block truncate text-[11px] text-ink-faint">{hit.snippet}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

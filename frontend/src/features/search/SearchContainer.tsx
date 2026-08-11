// Container: owns the debounced chat search query + results fetch; renders
// the presentational SearchResults with plain props.

import { useEffect, useRef, useState } from 'react'
import { searchMessages } from '../../api/chat'
import { useChatStore } from '../../store/chatStore'
import type { SearchHit } from '../../types'
import SearchResults from './SearchResults'

const DEBOUNCE_MS = 300

export default function SearchContainer() {
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

  const jump = (hit: SearchHit) => {
    setOpen(false)
    setQuery('')
    void selectSession(hit.session_id)
  }

  return (
    <SearchResults query={query} onQueryChange={setQuery} hits={hits} open={open} onJump={jump} />
  )
}

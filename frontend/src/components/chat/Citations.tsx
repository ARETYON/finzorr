import { useState } from 'react'
import { ChevronDown, ChevronRight, Link as LinkIcon } from 'lucide-react'
import type { Citation } from '../../types'

export default function Citations({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState(false)
  if (!citations.length) return null
  return (
    <div className="mt-2 text-xs">
      <button
        onClick={() => setOpen(!open)}
        className="fui-mono inline-flex items-center gap-1 text-ink-dim hover:text-ink-mid"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {citations.length} source{citations.length > 1 ? 's' : ''}
      </button>
      {open && (
        <ul className="mt-1 space-y-1 border-l-2 border-line pl-3">
          {citations.map((c, i) => (
            <li key={i} className="text-ink-dim">
              {c.url ? (
                <a
                  href={c.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-accent-strong hover:underline"
                >
                  <LinkIcon size={10} />
                  {c.title || c.url}
                </a>
              ) : (
                <span className="font-medium">{c.marker || c.title}</span>
              )}
              {c.snippet && <p className="mt-0.5 line-clamp-2 text-ink-faint">{c.snippet}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

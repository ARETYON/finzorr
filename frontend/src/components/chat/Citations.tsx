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
        className="inline-flex items-center gap-1 text-slate-500 hover:text-slate-700"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {citations.length} source{citations.length > 1 ? 's' : ''}
      </button>
      {open && (
        <ul className="mt-1 space-y-1 border-l-2 border-slate-200 pl-3">
          {citations.map((c, i) => (
            <li key={i} className="text-slate-500">
              {c.url ? (
                <a
                  href={c.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-brand-600 hover:underline"
                >
                  <LinkIcon size={10} />
                  {c.title || c.url}
                </a>
              ) : (
                <span className="font-medium">{c.marker || c.title}</span>
              )}
              {c.snippet && <p className="mt-0.5 line-clamp-2 text-slate-400">{c.snippet}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

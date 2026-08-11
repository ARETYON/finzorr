// Artifacts-lite: renders a ```document block from a message in a side panel.

import { Copy, Download, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import toast from 'react-hot-toast'
import type { Artifact } from '../../lib/artifact'

export default function ArtifactPanel({
  artifact,
  onClose,
}: {
  artifact: Artifact
  onClose: () => void
}) {
  const copy = async () => {
    await navigator.clipboard.writeText(artifact.content)
    toast.success('Copied')
  }
  const download = () => {
    const blob = new Blob([`# ${artifact.title}\n\n${artifact.content}\n`], {
      type: 'text/markdown',
    })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${artifact.title.replace(/[^a-z0-9-_ ]/gi, '').trim() || 'document'}.md`
    a.click()
    URL.revokeObjectURL(a.href)
  }
  return (
    <aside className="fixed inset-0 z-40 flex flex-col border-l border-line bg-panel md:static md:z-auto md:w-[26rem] md:shrink-0">
      <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
        <h2 className="app-title truncate text-sm font-semibold text-ink-strong">
          {artifact.title}
        </h2>
        <div className="flex items-center gap-2 text-ink-faint">
          <button onClick={() => void copy()} aria-label="Copy" className="hover:text-ink-mid">
            <Copy size={14} />
          </button>
          <button onClick={download} aria-label="Download" className="hover:text-ink-mid">
            <Download size={14} />
          </button>
          <button onClick={onClose} aria-label="Close" className="hover:text-ink-mid">
            <X size={14} />
          </button>
        </div>
      </div>
      <div className="prose prose-sm prose-slate max-w-none flex-1 overflow-y-auto p-4 text-ink">
        <ReactMarkdown>{artifact.content}</ReactMarkdown>
      </div>
    </aside>
  )
}

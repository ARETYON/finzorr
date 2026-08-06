// Artifacts-lite: renders a ```document block from a message in a side panel.

import { Copy, Download, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import toast from 'react-hot-toast'

export interface Artifact {
  title: string
  content: string
}

const DOC_BLOCK = /```document\n(.*?)```/s

export function extractArtifact(content: string): Artifact | null {
  const match = DOC_BLOCK.exec(content)
  if (!match) return null
  const lines = match[1].trim().split('\n')
  return { title: lines[0]?.trim() || 'Document', content: lines.slice(1).join('\n').trim() }
}

export function stripArtifact(content: string): string {
  return content.replace(DOC_BLOCK, '').trim()
}

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
    <aside className="flex w-[26rem] shrink-0 flex-col border-l border-line bg-panel">
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

// Document upload button + user document list (delete supported). Accepts
// PDF/DOCX/PPTX/XLSX/XLS/CSV/TXT/MD; after upload the document is immediately
// queryable in chat (the router knows the user's uploads by name).

import { useCallback, useEffect, useRef, useState } from 'react'
import { FileText, Loader2, Paperclip, Trash2 } from 'lucide-react'
import toast from 'react-hot-toast'
import {
  deleteDocument,
  listDocuments,
  uploadDocument,
  type DocumentInfo,
} from '../../api/documents'

export default function FileUpload() {
  const [docs, setDocs] = useState<DocumentInfo[]>([])
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    try {
      setDocs(await listDocuments())
    } catch {
      // non-critical panel
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const onPick = async (file: File | undefined) => {
    if (!file) return
    const allowed = ['.pdf', '.docx', '.pptx', '.xlsx', '.xls', '.csv', '.txt', '.md']
    if (!allowed.some((ext) => file.name.toLowerCase().endsWith(ext))) {
      toast.error('Supported types: PDF, DOCX, PPTX, XLSX, XLS, CSV, TXT, MD')
      return
    }
    setBusy(true)
    try {
      const result = await uploadDocument(file)
      if (result.status === 'ready') toast.success(`${file.name} ready — ask me about it!`)
      else toast.error(`${file.name}: ingestion ${result.status}`)
      await refresh()
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      toast.error(detail ?? 'Upload failed')
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const remove = async (id: string) => {
    try {
      await deleteDocument(id)
      await refresh()
    } catch {
      toast.error('Could not delete document')
    }
  }

  return (
    <div className="border-t border-line p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-xs font-semibold text-ink-dim">
          <FileText size={13} /> Documents
        </span>
        <button
          onClick={() => inputRef.current?.click()}
          disabled={busy}
          className="text-ink-faint hover:text-accent-strong disabled:opacity-50"
          aria-label="Upload document"
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Paperclip size={14} />}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx,.pptx,.xlsx,.xls,.csv,.txt,.md"
          className="hidden"
          onChange={(e) => void onPick(e.target.files?.[0])}
        />
      </div>
      <ul className="space-y-1">
        {docs.length === 0 && (
          <li className="text-[11px] text-ink-faint">Upload a PDF, Word, PowerPoint, Excel or CSV file, then ask about it.</li>
        )}
        {docs.map((d) => (
          <li key={d.id} className="group flex items-center gap-1.5 text-[11px] text-ink-mid">
            <FileText size={11} className="shrink-0 text-ink-faint" />
            <span className="truncate" title={d.filename}>
              {d.filename}
            </span>
            <span className={d.status === 'ready' ? 'text-ok' : 'text-warn'}>
              {d.status}
            </span>
            <button
              onClick={() => void remove(d.id)}
              className="ml-auto hidden text-ink-faint hover:text-danger group-hover:block"
              aria-label={`Delete ${d.filename}`}
            >
              <Trash2 size={11} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

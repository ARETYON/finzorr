// Container: owns document list fetch/upload/delete + loading state; renders
// the presentational DocumentsList with plain props.

import { useCallback, useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import { deleteDocument, listDocuments, uploadDocument, type DocumentInfo } from '../../api/documents'
import DocumentsList from './DocumentsList'

export default function DocumentsContainer() {
  const [docs, setDocs] = useState<DocumentInfo[]>([])
  const [busy, setBusy] = useState(false)

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

  const onUpload = async (file: File) => {
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
    }
  }

  const onDelete = (id: string) => {
    void (async () => {
      try {
        await deleteDocument(id)
        await refresh()
      } catch {
        toast.error('Could not delete document')
      }
    })()
  }

  return <DocumentsList docs={docs} busy={busy} onUpload={onUpload} onDelete={onDelete} />
}

import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ImagePlus, Mic, MicOff, Send, Square, X } from 'lucide-react'
import clsx from 'clsx'
import toast from 'react-hot-toast'
import { uploadAttachment, uploadDocument } from '../../api/documents'

interface Props {
  disabled: boolean
  streaming: boolean
  onSend: (text: string, attachments?: string[]) => void
  onCancel: () => void
  prefill?: string
  onPrefillConsumed?: () => void
}

type SpeechRecognitionLike = {
  lang: string
  interimResults: boolean
  continuous: boolean
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null
  onend: (() => void) | null
  onerror: (() => void) | null
  start: () => void
  stop: () => void
}

function getRecognition(): SpeechRecognitionLike | null {
  const w = window as unknown as {
    SpeechRecognition?: new () => SpeechRecognitionLike
    webkitSpeechRecognition?: new () => SpeechRecognitionLike
  }
  const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition
  return Ctor ? new Ctor() : null
}

const SPEECH_SUPPORTED =
  typeof window !== 'undefined' &&
  ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window)

export default function MessageInput({
  disabled,
  streaming,
  onSend,
  onCancel,
  prefill,
  onPrefillConsumed,
}: Props) {
  const [text, setText] = useState('')
  const [listening, setListening] = useState(false)
  const [attachment, setAttachment] = useState<{ token: string; name: string } | null>(null)
  const [uploading, setUploading] = useState(false)
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const imageInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (prefill) {
      setText(prefill)
      onPrefillConsumed?.()
    }
  }, [prefill, onPrefillConsumed])

  const submit = () => {
    const trimmed = text.trim()
    if ((!trimmed && !attachment) || disabled || streaming) return
    onSend(trimmed || 'Describe this image.', attachment ? [attachment.token] : undefined)
    setText('')
    setAttachment(null)
  }

  const DOC_EXTENSIONS = ['.pdf', '.docx', '.pptx', '.xlsx', '.xls', '.csv', '.txt', '.md']

  const pickImage = async (file: File | undefined) => {
    if (!file) return
    const isDoc = DOC_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext))
    setUploading(true)
    try {
      if (isDoc) {
        // documents go to the Documents panel + RAG index; the message then
        // references the file by name so routing finds it
        const { status } = await uploadDocument(file)
        if (status !== 'ready') {
          toast.error(`"${file.name}" could not be processed`)
          return
        }
        setText((prev) => (prev ? `${prev} ` : '') + `Regarding "${file.name}": `)
        toast.success(`${file.name} uploaded — ask away`)
      } else {
        const { token } = await uploadAttachment(file)
        setAttachment({ token, name: file.name })
      }
    } catch {
      toast.error(
        isDoc
          ? 'Document upload failed (max 10MB; PDF/DOCX/PPTX/XLSX/XLS/CSV/TXT/MD)'
          : 'Image upload failed (PNG/JPEG, max 5MB)'
      )
    } finally {
      setUploading(false)
      if (imageInputRef.current) imageInputRef.current.value = ''
    }
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  const toggleMic = () => {
    if (listening) {
      recognitionRef.current?.stop()
      setListening(false)
      return
    }
    const recognition = getRecognition()
    if (!recognition) {
      toast.error('Voice input is not supported in this browser')
      return
    }
    recognition.lang = 'en-IN'
    recognition.interimResults = true
    recognition.continuous = false
    recognition.onresult = (event) => {
      const transcript = Array.from(
        { length: event.results.length },
        (_, i) => event.results[i]?.[0]?.transcript ?? '',
      ).join('')
      setText(transcript)
    }
    recognition.onend = () => setListening(false)
    recognition.onerror = () => setListening(false)
    recognitionRef.current = recognition
    recognition.start()
    setListening(true)
  }

  return (
    <div className="border-t border-line bg-panel p-3">
      {attachment && (
        <div className="mx-auto mb-2 flex max-w-3xl">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-chip px-2.5 py-1 text-[11px] text-ink-mid">
            🖼️ {attachment.name}
            <button onClick={() => setAttachment(null)} aria-label="Remove image">
              <X size={11} className="text-ink-faint hover:text-danger" />
            </button>
          </span>
        </div>
      )}
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <span className="fui-only fui-mono pb-2.5 text-sm text-accent-strong">&gt;</span>
        <button
          onClick={() => imageInputRef.current?.click()}
          disabled={uploading}
          className="clip-btn rounded-xl bg-chip p-2.5 text-ink-mid hover:bg-line disabled:opacity-50"
          aria-label="Attach image"
        >
          <ImagePlus size={18} />
        </button>
        <input
          ref={imageInputRef}
          type="file"
          accept="image/png,image/jpeg,.pdf,.docx,.pptx,.xlsx,.xls,.csv,.txt,.md"
          className="hidden"
          onChange={(e) => void pickImage(e.target.files?.[0])}
        />
        <textarea
          aria-label="Message finzorr"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder={
            listening ? 'Listening…' : 'Ask anything — stocks, your documents, or general questions…'
          }
          className="clip-panel max-h-40 flex-1 resize-none rounded-xl border border-line-strong bg-panel px-4 py-2.5 text-sm text-ink placeholder:text-ink-faint focus:border-accent-strong focus:outline-none"
        />
        {SPEECH_SUPPORTED && (
          <button
            onClick={toggleMic}
            className={clsx(
              'clip-btn rounded-xl p-2.5',
              listening
                ? 'animate-pulse bg-danger/20 text-danger'
                : 'bg-chip text-ink-mid hover:bg-line',
            )}
            aria-label={listening ? 'Stop dictating' : 'Dictate'}
          >
            {listening ? <MicOff size={18} /> : <Mic size={18} />}
          </button>
        )}
        {streaming ? (
          <button
            onClick={onCancel}
            className="clip-btn rounded-xl bg-chip p-2.5 text-ink-mid hover:bg-line"
            aria-label="Stop generating"
          >
            <Square size={18} />
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={disabled || (!text.trim() && !attachment)}
            className="clip-btn rounded-xl bg-accent p-2.5 text-btn-ink hover:bg-accent-hover disabled:opacity-40"
            aria-label="Send"
          >
            <Send size={18} />
          </button>
        )}
      </div>
      <p className="mx-auto mt-1.5 max-w-3xl text-center text-[10px] text-ink-faint">
        finzorr can make mistakes. Market data may be delayed — not investment advice.
      </p>
    </div>
  )
}

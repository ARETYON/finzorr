import { useState, type KeyboardEvent } from 'react'
import { Send, Square } from 'lucide-react'

interface Props {
  disabled: boolean
  streaming: boolean
  onSend: (text: string) => void
  onCancel: () => void
}

export default function MessageInput({ disabled, streaming, onSend, onCancel }: Props) {
  const [text, setText] = useState('')

  const submit = () => {
    const trimmed = text.trim()
    if (!trimmed || disabled || streaming) return
    onSend(trimmed)
    setText('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="border-t border-slate-200 bg-white p-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder="Ask anything — stocks, your documents, or general questions…"
          className="max-h-40 flex-1 resize-none rounded-xl border border-slate-300 px-4 py-2.5 text-sm focus:border-brand-500 focus:outline-none"
        />
        {streaming ? (
          <button
            onClick={onCancel}
            className="rounded-xl bg-slate-200 p-2.5 text-slate-600 hover:bg-slate-300"
            aria-label="Stop generating"
          >
            <Square size={18} />
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={disabled || !text.trim()}
            className="rounded-xl bg-brand-600 p-2.5 text-white hover:bg-brand-700 disabled:opacity-40"
            aria-label="Send"
          >
            <Send size={18} />
          </button>
        )}
      </div>
      <p className="mx-auto mt-1.5 max-w-3xl text-center text-[10px] text-slate-400">
        finzorr can make mistakes. Market data may be delayed — not investment advice.
      </p>
    </div>
  )
}

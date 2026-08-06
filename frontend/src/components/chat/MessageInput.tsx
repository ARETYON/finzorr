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
    <div className="border-t border-line bg-panel p-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <span className="fui-only fui-mono pb-2.5 text-sm text-accent-strong">&gt;</span>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={1}
          placeholder="Ask anything — stocks, your documents, or general questions…"
          className="clip-panel max-h-40 flex-1 resize-none rounded-xl border border-line-strong bg-panel px-4 py-2.5 text-sm text-ink placeholder:text-ink-faint focus:border-accent-strong focus:outline-none"
        />
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
            disabled={disabled || !text.trim()}
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

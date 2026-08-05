import { ThumbsDown, ThumbsUp } from 'lucide-react'
import clsx from 'clsx'
import ReactMarkdown from 'react-markdown'
import toast from 'react-hot-toast'
import { sendFeedback } from '../../api/chat'
import { useChatStore } from '../../store/chatStore'
import type { ChatMessage } from '../../types'
import Citations from './Citations'
import RouteBadge from './RouteBadge'

export default function MessageBubble({ message }: { message: ChatMessage }) {
  const setMessages = useChatStore((s) => s.setMessages)
  const isUser = message.role === 'user'

  const rate = async (rating: 1 | -1) => {
    try {
      await sendFeedback(message.id, rating)
      setMessages((prev) => prev.map((m) => (m.id === message.id ? { ...m, feedback: rating } : m)))
    } catch {
      toast.error('Could not save feedback')
    }
  }

  return (
    <div className={clsx('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={clsx(
          'max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed',
          isUser
            ? 'bg-brand-600 text-white'
            : 'border border-slate-200 bg-white text-slate-800 shadow-sm',
        )}
      >
        {!isUser && message.route && (
          <div className="mb-1.5">
            <RouteBadge route={message.route} />
          </div>
        )}
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : (
          <div className="prose prose-sm prose-slate max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
            <ReactMarkdown>{message.content || '…'}</ReactMarkdown>
          </div>
        )}
        {!isUser && message.citations && <Citations citations={message.citations} />}
        {!isUser && !message.streaming && message.id && (
          <div className="mt-2 flex items-center gap-2 text-slate-400">
            <button
              onClick={() => rate(1)}
              disabled={message.feedback !== undefined}
              className={clsx('hover:text-emerald-600', message.feedback === 1 && 'text-emerald-600')}
              aria-label="Good response"
            >
              <ThumbsUp size={13} />
            </button>
            <button
              onClick={() => rate(-1)}
              disabled={message.feedback !== undefined}
              className={clsx('hover:text-rose-600', message.feedback === -1 && 'text-rose-600')}
              aria-label="Bad response"
            >
              <ThumbsDown size={13} />
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

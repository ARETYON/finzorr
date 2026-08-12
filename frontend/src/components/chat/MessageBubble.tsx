import { Pencil, RefreshCw, ThumbsDown, ThumbsUp, Volume2, VolumeX } from 'lucide-react'
import clsx from 'clsx'
import ReactMarkdown from 'react-markdown'
import toast from 'react-hot-toast'
import { sendFeedback } from '../../api/chat'
import { useChatStore } from '../../store/chatStore'
import { speak, stopSpeaking, useSettingsStore } from '../../store/settingsStore'
import type { ChatMessage } from '../../types'
import Citations from './Citations'
import PriceChart from './PriceChart'
import RouteBadge from './RouteBadge'

interface Props {
  message: ChatMessage
  onRegenerate?: (() => void) | undefined
  onEdit?: ((content: string) => void) | undefined
}

export default function MessageBubble({ message, onRegenerate, onEdit }: Props) {
  const setMessages = useChatStore((s) => s.setMessages)
  const isSpeaking = useSettingsStore((s) => s.speakingMessageId === message.id)
  const isUser = message.role === 'user'

  const toggleSpeak = () => {
    if (isSpeaking) stopSpeaking()
    else speak(message.content, message.id)
  }

  const rate = async (rating: 1 | -1) => {
    try {
      await sendFeedback(message.id, rating)
      setMessages((prev) => prev.map((m) => (m.id === message.id ? { ...m, feedback: rating } : m)))
    } catch {
      toast.error('Could not save feedback')
    }
  }

  return (
    <div className={clsx('group/msg flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={clsx(
          'max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed',
          isUser
            ? 'msg-user clip-panel bg-bubble-user text-bubble-user-ink'
            : 'msg-assistant clip-panel fui-brackets border border-line bg-panel text-ink shadow-sm',
        )}
      >
        {!isUser && message.route && (
          <div className="mb-1.5">
            <RouteBadge route={message.route} />
          </div>
        )}
        {isUser ? (
          <div className="flex items-start gap-2">
            <p className="whitespace-pre-wrap">{message.content}</p>
            {onEdit && (
              <button
                onClick={() => onEdit(message.content)}
                className="mt-0.5 shrink-0 opacity-70 transition-opacity hover:!opacity-100 md:opacity-0 md:group-hover/msg:opacity-70"
                aria-label="Edit and resend"
              >
                <Pencil size={12} />
              </button>
            )}
          </div>
        ) : (
          <div className="prose prose-sm prose-slate max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
            <ReactMarkdown>{message.content || '…'}</ReactMarkdown>
            {message.streaming && <span className="stream-cursor">▊</span>}
          </div>
        )}
        {!isUser && message.chart && <PriceChart chart={message.chart} />}
        {!isUser && message.citations && <Citations citations={message.citations} />}
        {!isUser && !message.streaming && message.id && (
          <div className="mt-2 flex items-center gap-2.5 text-ink-faint">
            <button
              onClick={() => rate(1)}
              disabled={message.feedback !== undefined}
              className={clsx('hover:text-ok', message.feedback === 1 && 'text-ok')}
              aria-label="Good response"
            >
              <ThumbsUp size={13} />
            </button>
            <button
              onClick={() => rate(-1)}
              disabled={message.feedback !== undefined}
              className={clsx('hover:text-danger', message.feedback === -1 && 'text-danger')}
              aria-label="Bad response"
            >
              <ThumbsDown size={13} />
            </button>
            <button
              onClick={toggleSpeak}
              className={clsx('hover:text-accent-strong', isSpeaking && 'text-accent-strong')}
              aria-label={isSpeaking ? 'Stop reading aloud' : 'Read aloud'}
            >
              {isSpeaking ? <VolumeX size={13} /> : <Volume2 size={13} />}
            </button>
            {onRegenerate && (
              <button
                onClick={onRegenerate}
                className="hover:text-accent-strong"
                aria-label="Regenerate response"
              >
                <RefreshCw size={13} />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

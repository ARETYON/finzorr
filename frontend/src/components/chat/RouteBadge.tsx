import { Bot, Database, FileText, Globe, ListChecks, Wrench } from 'lucide-react'
import type { ReactElement } from 'react'

const ROUTE_META: Record<string, { label: string; icon: ReactElement; className: string }> = {
  general_chat: { label: 'Chat', icon: <Bot size={12} />, className: 'bg-chip text-ink-mid' },
  memory: { label: 'Watchlist', icon: <ListChecks size={12} />, className: 'bg-route-memory text-route-memory-ink' },
  rag: { label: 'Documents', icon: <FileText size={12} />, className: 'bg-route-rag text-route-rag-ink' },
  web_search: { label: 'Web', icon: <Globe size={12} />, className: 'bg-route-web text-route-web-ink' },
  nl2sql: { label: 'Screener', icon: <Database size={12} />, className: 'bg-route-nl2sql text-route-nl2sql-ink' },
  tools: { label: 'Market Data', icon: <Wrench size={12} />, className: 'bg-route-tools text-route-tools-ink' },
}

export default function RouteBadge({ route, reason }: { route: string; reason?: string }) {
  const meta = ROUTE_META[route]
  if (!meta) return null
  return (
    <span
      title={reason}
      data-route={route}
      className={`route-badge inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${meta.className}`}
    >
      {meta.icon}
      {meta.label}
    </span>
  )
}

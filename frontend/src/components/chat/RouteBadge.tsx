import { Bot, Database, FileText, Globe, ListChecks, Wrench } from 'lucide-react'
import type { ReactElement } from 'react'

const ROUTE_META: Record<string, { label: string; icon: ReactElement; className: string }> = {
  general_chat: { label: 'Chat', icon: <Bot size={12} />, className: 'bg-slate-100 text-slate-600' },
  memory: { label: 'Watchlist', icon: <ListChecks size={12} />, className: 'bg-violet-100 text-violet-700' },
  rag: { label: 'Documents', icon: <FileText size={12} />, className: 'bg-emerald-100 text-emerald-700' },
  web_search: { label: 'Web', icon: <Globe size={12} />, className: 'bg-sky-100 text-sky-700' },
  nl2sql: { label: 'Screener', icon: <Database size={12} />, className: 'bg-amber-100 text-amber-700' },
  tools: { label: 'Market Data', icon: <Wrench size={12} />, className: 'bg-rose-100 text-rose-700' },
}

export default function RouteBadge({ route, reason }: { route: string; reason?: string }) {
  const meta = ROUTE_META[route]
  if (!meta) return null
  return (
    <span
      title={reason}
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${meta.className}`}
    >
      {meta.icon}
      {meta.label}
    </span>
  )
}

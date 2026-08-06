// Shared domain types mirrored from the backend schemas.

export interface User {
  id: string
  email: string
  name: string
  picture_url: string | null
  custom_instructions?: string | null
}

export interface ChatSession {
  id: string
  title: string | null
  created_at: string
  updated_at: string
}

export interface Citation {
  marker?: string
  title?: string
  url?: string
  snippet?: string
}

export interface ToolCallInfo {
  name: string
  arguments?: Record<string, unknown>
  result?: string
}

export interface PricePoint {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface ChartData {
  symbol: string
  period: string
  points: PricePoint[]
}

export interface SearchHit {
  session_id: string
  session_title: string
  role: string
  snippet: string
  created_at: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  route?: string | null
  tool_calls?: ToolCallInfo[] | null
  citations?: Citation[] | null
  created_at?: string
  streaming?: boolean
  feedback?: 1 | -1
  data_as_of?: string
  sources?: string[]
  chart?: ChartData | null
}

// WebSocket frames (server -> client)
export type ServerFrame =
  | { type: 'thinking' }
  | { type: 'routing'; route: string; reason: string }
  | { type: 'token'; delta: string }
  | { type: 'tool_call'; name: string; arguments?: Record<string, unknown> }
  | {
      type: 'response'
      message_id: string
      message: string
      route: string
      route_reason: string
      citations: Citation[]
      tool_calls: ToolCallInfo[]
      actions: unknown[]
      data_as_of: string
      sources: string[]
      chart: ChartData | null
      session_id: string
    }
  | { type: 'stopped' }
  | { type: 'error'; message: string }
  | { type: 'pong' }

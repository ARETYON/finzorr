// Inline price chart rendered when a turn fetched historical prices.
// Colors follow the theme via CSS variables so it looks right in Light and Ops.

import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { ChartData } from '../../types'

export default function PriceChart({ chart }: { chart: ChartData }) {
  if (!chart.points?.length) return null
  const first = chart.points[0].close
  const last = chart.points[chart.points.length - 1].close
  const up = last >= first
  const stroke = up ? 'rgb(var(--ok))' : 'rgb(var(--danger))'
  const changePct = first ? (((last - first) / first) * 100).toFixed(2) : '0'

  return (
    <div className="clip-panel mt-2 rounded-lg border border-line bg-surface/60 p-2">
      <div className="fui-mono mb-1 flex items-baseline justify-between px-1 text-[11px]">
        <span className="font-semibold text-ink-strong">
          {chart.symbol} · {chart.period}
        </span>
        <span style={{ color: stroke }}>
          ₹{last.toLocaleString('en-IN')} ({up ? '+' : ''}
          {changePct}%)
        </span>
      </div>
      <ResponsiveContainer width="100%" height={160}>
        <AreaChart data={chart.points} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={`fill-${chart.symbol}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.25} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="date"
            tick={{ fontSize: 9, fill: 'rgb(var(--ink-faint))' }}
            tickLine={false}
            axisLine={{ stroke: 'rgb(var(--line))' }}
            minTickGap={48}
          />
          <YAxis
            domain={['auto', 'auto']}
            tick={{ fontSize: 9, fill: 'rgb(var(--ink-faint))' }}
            tickLine={false}
            axisLine={false}
            width={48}
            tickFormatter={(v: number) => `₹${v.toLocaleString('en-IN')}`}
          />
          <Tooltip
            contentStyle={{
              background: 'rgb(var(--panel))',
              border: '1px solid rgb(var(--line))',
              borderRadius: 6,
              fontSize: 11,
              color: 'rgb(var(--ink))',
            }}
            formatter={(value) => [`₹${Number(value).toLocaleString('en-IN')}`, 'Close']}
          />
          <Area
            type="monotone"
            dataKey="close"
            stroke={stroke}
            strokeWidth={1.5}
            fill={`url(#fill-${chart.symbol})`}
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { EquityPoint } from '../../types/trade'
import { formatMoney, formatTimestamp } from '../../utils/format'

interface EquityChartProps {
  data: EquityPoint[]
  /** Opens the full analysis view */
  onOpenDetails?: () => void
}

function labelFor(t: string): string {
  const d = new Date(t)
  const withTime = Boolean(d.getHours() || d.getMinutes())
  return formatTimestamp(Math.floor(d.getTime() / 1000), withTime)
}

export function EquityChart({ data, onOpenDetails }: EquityChartProps) {
  const chartData = data.map((p) => ({
    ...p,
    label: labelFor(p.t),
  }))

  const start = data[0]?.equity ?? 0
  const end = data[data.length - 1]?.equity ?? 0
  const up = end >= start
  // Brand gold line; PnL direction stays in the end-value color.
  const stroke = '#c9a24d'
  const fillId = 'equityFillGold'

  return (
    <section
      onClick={onOpenDetails}
      className={[
        'panel panel-brand p-4 transition-colors sm:p-5',
        onOpenDetails ? 'cursor-pointer hover:bg-[var(--color-surface-hover)]/40' : '',
      ].join(' ')}
    >
      <div className="mb-4 flex items-end justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-[var(--color-text)]">Equity Curve</h2>
          <p className="mt-0.5 text-xs text-[var(--color-text-muted)]">
            {onOpenDetails ? 'Klicken für die vollständige Analyse' : 'Mark-to-Market Verlauf'}
          </p>
        </div>
        <p className={`tabular text-sm font-medium ${up ? 'text-[var(--color-long)]' : 'text-[var(--color-short)]'}`}>
          {formatMoney(end)}
        </p>
      </div>

      <div className="h-[260px] w-full sm:h-[300px]">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="equityFillGold" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#c9a24d" stopOpacity={0.26} />
                <stop offset="100%" stopColor="#c9a24d" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#2a3a4f" strokeDasharray="3 6" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fill: '#7a8ba3', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              minTickGap={28}
            />
            <YAxis
              tick={{ fill: '#7a8ba3', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={56}
              tickFormatter={(v: number) => `$${Math.round(v)}`}
              domain={['dataMin - 80', 'dataMax + 80']}
            />
            <Tooltip
              contentStyle={{
                background: '#162234',
                border: '1px solid #2a3a4f',
                borderRadius: 10,
                fontSize: 12,
              }}
              labelStyle={{ color: '#9aabbd' }}
              formatter={(value) => [formatMoney(Number(value ?? 0)), 'Equity']}
            />
            <Area
              type="monotone"
              dataKey="equity"
              stroke={stroke}
              strokeWidth={2}
              fill={`url(#${fillId})`}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 0 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

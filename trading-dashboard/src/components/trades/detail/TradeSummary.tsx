import type { Trade } from '../../../types/trade'
import { formatMoney, formatPrice, formatR, formatSignedMoney } from '../../../utils/format'
import { PnLCell } from '../PnLCell'
import { ScoreBadge } from '../ScoreBadge'
import { SideBadge } from '../SideBadge'
import { DetailCard, DetailField } from './DetailField'

interface TradeSummaryProps {
  trade: Trade
}

/** Contract, pricing and risk facts of a single trade. */
export function TradeSummary({ trade }: TradeSummaryProps) {
  const risk = Math.abs(trade.entry - trade.stop)
  const finalTp = trade.takeProfits?.[trade.takeProfits.length - 1]
  const reward = finalTp ? Math.abs(finalTp.price - trade.entry) : null

  return (
    <DetailCard
      title="Trade Summary"
      actions={
        <span className="rounded-md border border-[var(--color-border-subtle)] px-2 py-0.5 text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
          {trade.status}
        </span>
      }
    >
      <dl className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-3">
        <DetailField label="Symbol" strong>
          {trade.symbol}
        </DetailField>

        <div className="min-w-0">
          <dt className="text-[10px] font-medium uppercase tracking-[0.09em] text-[var(--color-text-muted)]">
            Side
          </dt>
          <dd className="mt-1">
            <SideBadge side={trade.side} />
          </dd>
        </div>

        <DetailField label="Strategy">{trade.strategy ?? '—'}</DetailField>

        <DetailField label="Entry">{formatPrice(trade.entry)}</DetailField>
        <DetailField label="Mark Price">{formatPrice(trade.mark)}</DetailField>
        <DetailField label="Exit">{formatPrice(trade.exit)}</DetailField>

        <DetailField label="Stop Loss">
          <span className="text-[var(--color-short)]">{formatPrice(trade.stop)}</span>
        </DetailField>
        {trade.currentStop != null &&
        Math.abs(trade.currentStop - trade.stop) > 1e-12 ? (
          <DetailField label="Current Stop">
            <span className="text-[var(--color-warn)]">{formatPrice(trade.currentStop)}</span>
          </DetailField>
        ) : null}
        <DetailField label="Risk / Unit">{formatPrice(risk)}</DetailField>
        <DetailField label="Reward / Unit">{reward != null ? formatPrice(reward) : '—'}</DetailField>

        <DetailField label="Notional">
          {trade.notional != null
            ? formatMoney(trade.notional)
            : trade.positionSize != null && trade.entry > 0
              ? formatMoney(trade.positionSize * trade.entry)
              : '—'}
        </DetailField>
        <DetailField label="Margin">{formatMoney(trade.margin)}</DetailField>
        <DetailField label="Leverage">
          {trade.leverage != null ? `${trade.leverage}×` : '—'}
        </DetailField>
        <DetailField label="Quantity">
          {trade.positionSize != null
            ? trade.positionSize.toLocaleString('en-US', { maximumFractionDigits: 4 })
            : '—'}
        </DetailField>

        <div className="min-w-0">
          <dt className="text-[10px] font-medium uppercase tracking-[0.09em] text-[var(--color-text-muted)]">
            Open PnL
          </dt>
          <dd className="mt-1">
            <PnLCell value={trade.upnl} />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-[10px] font-medium uppercase tracking-[0.09em] text-[var(--color-text-muted)]">
            Realized PnL
          </dt>
          <dd className="mt-1">
            <PnLCell value={trade.realized} />
          </dd>
        </div>
        <DetailField label="R-Multiple">{formatR(trade.r)}</DetailField>

        <DetailField label="Fees">
          {trade.fees != null ? formatSignedMoney(-trade.fees) : '—'}
        </DetailField>
        <div className="min-w-0">
          <dt className="text-[10px] font-medium uppercase tracking-[0.09em] text-[var(--color-text-muted)]">
            Score
          </dt>
          <dd className="mt-1">
            <ScoreBadge score={trade.score} />
          </dd>
        </div>
        <DetailField label="Trade ID">{trade.id}</DetailField>
      </dl>

      {trade.takeProfits && trade.takeProfits.length > 0 ? (
        <div className="mt-5 border-t border-[var(--color-border-subtle)] pt-4">
          <p className="mb-3 text-[10px] font-medium uppercase tracking-[0.09em] text-[var(--color-text-muted)]">
            Take Profits
          </p>
          <div className="flex flex-wrap gap-2">
            {trade.takeProfits.map((tp) => (
              <span
                key={tp.label}
                className={[
                  'flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs tabular',
                  tp.hit
                    ? 'border-[var(--color-long)]/40 bg-[var(--color-long-soft)] text-[var(--color-long)]'
                    : 'border-[var(--color-border-subtle)] bg-[var(--color-surface)] text-[var(--color-text-secondary)]',
                ].join(' ')}
              >
                <span className="font-semibold">{tp.label}</span>
                {formatPrice(tp.price)}
                {tp.size != null ? (
                  <span className="text-[var(--color-text-muted)]">
                    {Math.round(tp.size * 100)}%
                  </span>
                ) : null}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </DetailCard>
  )
}

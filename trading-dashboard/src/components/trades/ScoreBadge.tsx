import { scoreTone, scoreToneClass } from '../../utils/score'

export function ScoreBadge({ score }: { score: number }) {
  const tone = scoreTone(score)
  return (
    <span
      className={[
        'inline-flex min-w-12 items-center justify-center rounded-md border px-2 py-0.5 text-[11px] font-semibold tabular',
        scoreToneClass[tone],
      ].join(' ')}
      title={`Score ${score.toFixed(1)} / 100`}
    >
      {score.toFixed(0)}
    </span>
  )
}

/** Score badge color ramp 0–100 → red → orange → yellow → light green → green */

export type ScoreTone = 'red' | 'orange' | 'yellow' | 'lime' | 'green'

export function scoreTone(score: number): ScoreTone {
  if (score < 20) return 'red'
  if (score < 40) return 'orange'
  if (score < 60) return 'yellow'
  if (score < 80) return 'lime'
  return 'green'
}

export const scoreToneClass: Record<ScoreTone, string> = {
  red: 'bg-[color-mix(in_srgb,#f07178_18%,transparent)] text-[#f07178] border-[#f07178]/40',
  orange: 'bg-[color-mix(in_srgb,#e6a05c_18%,transparent)] text-[#e6a05c] border-[#e6a05c]/40',
  yellow: 'bg-[color-mix(in_srgb,#e6b35c_18%,transparent)] text-[#e6b35c] border-[#e6b35c]/40',
  lime: 'bg-[color-mix(in_srgb,#a3cf3d_18%,transparent)] text-[#b7db5a] border-[#a3cf3d]/40',
  green: 'bg-[color-mix(in_srgb,#3dcf8e_18%,transparent)] text-[#3dcf8e] border-[#3dcf8e]/40',
}

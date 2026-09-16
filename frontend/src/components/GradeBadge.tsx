import { gradeColor, prettifyLevel } from '../lib/gradeColor'
import type { GradingScale } from '../api/types'

export default function GradeBadge({ level, scale }: { level: string; scale?: GradingScale }) {
  const step = gradeColor(level, scale)

  // Fallback for an unrecognized level (e.g. one that no longer exists in
  // the rubric's current scale) -- neutral, matches this app's muted-ink
  // convention elsewhere, rather than defaulting into a random ramp step.
  const fallbackClassName = step ? '' : 'bg-slate-100 text-slate-600'

  return (
    <span
      className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${fallbackClassName}`}
      style={step ? { backgroundColor: step.hex, color: step.text === 'white' ? '#ffffff' : '#0b0b0b' } : undefined}
      title={scale?.[level]}
    >
      {prettifyLevel(level)}
    </span>
  )
}

import type { GradingScale } from '../api/types'

/**
 * A grade level is an **ordinal** value (its rank in the rubric's
 * `grading_scale`), not a fixed small vocabulary -- a rubric can define any
 * number of levels with any names (see `GradingScale`'s doc comment). Per
 * the dataviz color-formula ("Ordinal: position in a sequence -- one hue,
 * monotone lightness steps"), that rules out the previous per-level hue
 * mapping (red/amber/green): a rainbow encodes identity, not position, and
 * breaks down the moment a rubric isn't exactly the original 4 levels. This
 * uses one hue (blue, this project's documented ordinal/sequential default)
 * stepped by the level's position in the scale instead.
 *
 * Steps are the documented sequential-hue ramp's named steps (250..700),
 * restricted to the ones proven legible here: step 250 clears the "ordinal
 * light-end" surface-contrast floor (>=2:1 on white), steps 450 is skipped
 * because *neither* black nor white text clears 4.5:1 text contrast against
 * it (black 4.46, white 4.42) -- every other step here comfortably clears
 * 4.5:1 with the paired text color (verified via the dataviz skill's
 * `contrast()` helper, not eyeballed).
 */
const ORDINAL_STEPS: { hex: string; text: 'black' | 'white' }[] = [
  { hex: '#86b6ef', text: 'black' }, // step 250
  { hex: '#6da7ec', text: 'black' }, // step 300
  { hex: '#5598e7', text: 'black' }, // step 350
  { hex: '#3987e5', text: 'black' }, // step 400
  { hex: '#256abf', text: 'white' }, // step 500
  { hex: '#1c5cab', text: 'white' }, // step 550
  { hex: '#184f95', text: 'white' }, // step 600
  { hex: '#104281', text: 'white' }, // step 650
  { hex: '#0d366b', text: 'white' }, // step 700
]

function stepForPosition(index: number, count: number) {
  // A single-level scale reads as "the" level -- give it the strongest
  // step rather than the ambiguous midpoint a 0/0 division would suggest.
  const fraction = count > 1 ? index / (count - 1) : 1
  return ORDINAL_STEPS[Math.round(fraction * (ORDINAL_STEPS.length - 1))]
}

function prettifyLevel(level: string): string {
  return level
    .replace(/[_-]+/g, ' ')
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export default function GradeBadge({ level, scale }: { level: string; scale?: GradingScale }) {
  const levelIds = scale ? Object.keys(scale) : []
  const index = levelIds.indexOf(level)
  const step = index === -1 ? null : stepForPosition(index, levelIds.length)

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

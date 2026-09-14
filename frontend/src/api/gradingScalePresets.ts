import type { GradingScale } from './types'

/**
 * Quick-pick starting points for the "Build manually" rubric form's grading
 * scale editor (`GradingScaleEditor` in `NewRubricForm.tsx`) -- loads into a
 * free-form, fully editable id/description list, not a constraint. Any
 * `{id: description}` dict is a valid `Rubric.grading_scale` server-side;
 * this is just convenience, not an enum of "allowed" scales.
 *
 * The `coarse4` preset mirrors the backend's `DEFAULT_GRADING_SCALE`
 * (`src/aitana/grading/models.py`) verbatim -- kept in sync by hand, there's
 * no shared source of truth across the language boundary. If that default
 * ever changes, update both.
 *
 * Order matters in every preset (and in whatever a TA edits it into): worst
 * -> best. See `GradingScale`'s doc comment in `./types.ts`.
 */
export const GRADING_SCALE_PRESETS: { label: string; scale: GradingScale }[] = [
  {
    label: 'Coarse (4 levels) -- default',
    scale: {
      not_attempted: 'Blank, off-topic, or shows no understanding of the question.',
      some_effort: 'On-topic but misses most key points or has major misconceptions.',
      almost_there: 'Covers most key points with minor gaps or imprecision.',
      solid: 'Covers the key points correctly and shows clear understanding.',
    },
  },
  {
    label: 'Pass / fail (2 levels)',
    scale: {
      fail: 'Does not meet the bar: missing or fundamentally wrong.',
      pass: 'Meets the bar: correct and shows understanding.',
    },
  },
  {
    label: 'Fine-grained (5 levels)',
    scale: {
      not_attempted: 'Blank or off-topic.',
      poor: 'Attempted, but shows a fundamental misunderstanding.',
      partial: 'On-topic but misses several key points.',
      good: 'Covers most key points with minor gaps.',
      excellent: 'Fully correct and shows clear, precise understanding.',
    },
  },
]

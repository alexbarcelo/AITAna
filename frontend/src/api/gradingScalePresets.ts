import type { GradingScale } from './types'

/**
 * Quick-pick starting points for the rubric form's grading scale editor
 * (`GradingScaleEditor` in `RubricForm.tsx`) -- loaded into a fully editable
 * id/description/score list, not a constraint: any scale satisfying the
 * backend's rules (unique ids, exactly one level scored 0) is valid.
 *
 * The first preset mirrors the backend's `DEFAULT_GRADING_SCALE`
 * (`src/aitana/grading/models.py`) -- kept in sync by hand, there's no
 * shared source of truth across the language boundary. If that default ever
 * changes, update both.
 */
export const GRADING_SCALE_PRESETS: { label: string; scale: GradingScale }[] = [
  {
    label: 'Coarse (4 levels) -- default',
    scale: [
      { id: 'not_attempted', description: 'Blank, off-topic, or shows no understanding of the question.', score: 0 },
      { id: 'some_effort', description: 'On-topic but misses most key points or has major misconceptions.', score: 1 },
      { id: 'almost_there', description: 'Covers most key points with minor gaps or imprecision.', score: 2 },
      { id: 'solid', description: 'Covers the key points correctly and shows clear understanding.', score: 3 },
    ],
  },
  {
    label: 'Pass / fail (2 levels)',
    scale: [
      { id: 'fail', description: 'Does not meet the bar: missing or fundamentally wrong.', score: 0 },
      { id: 'pass', description: 'Meets the bar: correct and shows understanding.', score: 1 },
    ],
  },
  {
    label: 'Fine-grained (5 levels)',
    scale: [
      { id: 'not_attempted', description: 'Blank or off-topic.', score: 0 },
      { id: 'poor', description: 'Attempted, but shows a fundamental misunderstanding.', score: 1 },
      { id: 'partial', description: 'On-topic but misses several key points.', score: 2 },
      { id: 'good', description: 'Covers most key points with minor gaps.', score: 3 },
      { id: 'excellent', description: 'Fully correct and shows clear, precise understanding.', score: 4 },
    ],
  },
  {
    label: 'Permissive (first-contact labs)',
    scale: [
      {
        id: 'caution',
        description:
          'Blank, off-topic, or reflects a fundamental misunderstanding of the concept -- not just rough or imprecise, but pointing in the wrong direction entirely.',
        score: 0,
      },
      {
        id: 'good',
        description:
          "On the right track and not far from reality. This is a student's first contact with the material, so the bar is 'grasped the core idea', not 'polished or complete answer'.",
        score: 1,
      },
      {
        id: 'perfect',
        description:
          'Complete, correct, and well-phrased. The answer is clear, precise, and fully addresses the question.',
        score: 2,
      },
    ],
  },
]

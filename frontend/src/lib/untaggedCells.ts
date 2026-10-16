import type { AnsweredQuestion } from '../api/types'

/** Total `untagged_cells` across a submission's answers -- see `components/UntaggedCells.tsx`. */
export function totalUntaggedCells(answers: AnsweredQuestion[]): number {
  return answers.reduce((sum, a) => sum + a.untagged_cells, 0)
}

import { effectiveGrade, type AnsweredQuestion, type GradingScale, type Question } from '../api/types'
import { findLevel, maxScore, orderedLevels } from './gradingScale'

/**
 * The score of a grade level in this rubric's scale. A level not in `scale`
 * (e.g. removed after grading) scores `null` and is left out of every
 * statistic, same as an ungraded answer.
 */
export function levelScore(level: string, scale: GradingScale): number | null {
  return findLevel(scale, level)?.score ?? null
}

export interface GradeStats {
  /** Effective-grade level -> number of answers at that level, in scale order (every level present, possibly 0). */
  counts: Record<string, number>
  /** Answers with an effective grade (LLM or manual). */
  graded: number
  /** Answers with no effective grade yet. */
  ungraded: number
  /** Answers whose effective grade is a TA's manual override. */
  manual: number
  /** Answers whose extracted text is blank. */
  blank: number
  /** Sum of the graded answers' scores over the best possible sum (every one
   * at the scale's top level): 1 = all top marks. `null` if nothing is
   * scoreable. */
  meanScore: number | null
}

export function gradeStats(answers: AnsweredQuestion[], scale: GradingScale): GradeStats {
  const counts: Record<string, number> = Object.fromEntries(orderedLevels(scale).map((level) => [level.id, 0]))
  let graded = 0
  let manual = 0
  let blank = 0
  let scoreSum = 0
  let scored = 0
  for (const answer of answers) {
    if (!answer.student_answer.trim()) blank++
    const grade = effectiveGrade(answer)
    if (!grade) continue
    graded++
    if (answer.manual_grade) manual++
    counts[grade.level] = (counts[grade.level] ?? 0) + 1
    const score = levelScore(grade.level, scale)
    if (score !== null) {
      scoreSum += score
      scored++
    }
  }
  return {
    counts,
    graded,
    ungraded: answers.length - graded,
    manual,
    blank,
    meanScore: scored > 0 && maxScore(scale) > 0 ? scoreSum / (scored * maxScore(scale)) : null,
  }
}

/** One question's contribution to a submission's score (`SubmissionScore.questions`). */
export interface QuestionScore {
  question: Question
  weight: number
  /** The answer's effective grade level, `null` if not graded (or no answer). */
  level: string | null
  /** True when that grade is a TA's manual override. */
  manual: boolean
  /** The level's score in the scale, `null` if ungraded or the level is no longer in the scale. */
  levelScore: number | null
  /** `weight x levelScore` (0 when `levelScore` is `null`). */
  points: number
  /** `weight x` the scale's top score. */
  maxPoints: number
}

export interface SubmissionScore {
  /** Per question, in rubric order -- weight-0 questions included (they add nothing). */
  questions: QuestionScore[]
  /** Sum over the rubric's questions of weight x effective level score (ungraded = 0). */
  raw: number
  /** `raw` with every question at the scale's top level. */
  max: number
  /** `raw / max` (1 = all top marks), `null` if nothing can be scored. */
  score: number | null
  /** Every question that counts (weight > 0) has a grade the scale knows. */
  complete: boolean
}

/**
 * One submission's weighted score -- mirrors the backend's `weighted_score`
 * (`grading/scoring.py`), which the general feedback CSV uses; keep both in
 * sync. The maximum always covers every question of the rubric, so an
 * unfinished grading shows a running total that only goes up (`complete`
 * says whether it's final).
 */
export function submissionScore(
  answers: AnsweredQuestion[],
  questions: Question[],
  scale: GradingScale,
): SubmissionScore {
  const top = maxScore(scale)
  let raw = 0
  let max = 0
  let complete = true
  const rows: QuestionScore[] = []
  for (const question of questions) {
    const weight = question.weight ?? 1
    const answer = answers.find((a) => a.question_id === question.id)
    const grade = answer ? effectiveGrade(answer) : null
    const score = grade ? levelScore(grade.level, scale) : null
    const points = score === null ? 0 : weight * score
    rows.push({
      question,
      weight,
      level: grade?.level ?? null,
      manual: Boolean(grade && answer?.manual_grade),
      levelScore: score,
      points,
      maxPoints: weight * top,
    })
    if (weight === 0) continue
    max += weight * top
    if (score === null) complete = false
    raw += points
  }
  return { questions: rows, raw, max, score: max > 0 ? raw / max : null, complete }
}

export function formatRawScore(value: number): string {
  return String(Math.round(value * 100) / 100)
}

export function median(values: number[]): number | null {
  if (values.length === 0) return null
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

export function formatScore(score: number | null): string {
  return score === null ? '—' : `${Math.round(score * 100)}%`
}

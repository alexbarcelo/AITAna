import type { GradingLevel, GradingScale } from '../api/types'

/**
 * Helpers for a rubric's grading scale. Levels are ranked by `score`
 * (lowest = worst), never by their position in the list -- mirrors the
 * backend's `ordered_levels`/`blank_level` (`grading/models.py`).
 */

/** Levels from worst to best (ascending score; ties keep list order). */
export function orderedLevels(scale: GradingScale): GradingLevel[] {
  return scale
    .map((level, index) => ({ level, index }))
    .sort((a, b) => a.level.score - b.level.score || a.index - b.index)
    .map(({ level }) => level)
}

export function findLevel(scale: GradingScale | undefined, id: string): GradingLevel | undefined {
  return scale?.find((level) => level.id === id)
}

/** The best achievable score in this scale. */
export function maxScore(scale: GradingScale): number {
  return Math.max(...scale.map((level) => level.score))
}

export function formatLevelScore(score: number): string {
  return Number.isInteger(score) ? String(score) : score.toFixed(2).replace(/0+$/, '')
}

/**
 * Why `scale` would be rejected by the backend (`GradingScale`'s rules), or
 * `null` if it's valid: at least one level, unique non-blank ids, and
 * exactly one level scored 0 (the level a blank answer gets).
 */
export function gradingScaleProblem(scale: GradingScale): string | null {
  if (scale.length === 0) return 'Add at least one level.'
  if (scale.some((level) => !level.id.trim())) return 'Every level needs an id.'
  const ids = scale.map((level) => level.id.trim())
  if (new Set(ids).size !== ids.length) return 'Level ids must be unique.'
  if (scale.some((level) => !Number.isFinite(level.score))) return 'Every level needs a numeric score.'
  const zeros = scale.filter((level) => level.score === 0).length
  if (zeros !== 1) return 'Exactly one level must score 0 -- the level a blank answer gets.'
  return null
}

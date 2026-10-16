import { gradeColor, prettifyLevel } from '../lib/gradeColor'
import { formatScore, gradeStats, median, type GradeStats } from '../lib/gradeStats'
import { effectiveGrade, type Submission, type SubmissionRubric } from '../api/types'

/**
 * Stacked horizontal bar of how many answers sit at each grade level,
 * worst -> best left to right, colored with the same ordinal ramp as
 * `GradeBadge`/`GradingGrid` cells, plus a trailing neutral segment for
 * not-yet-graded answers. Each segment carries its own hover tooltip.
 */
export function GradeDistributionBar({
  stats,
  scale,
  className = 'h-2.5',
}: {
  stats: GradeStats
  scale: SubmissionRubric['grading_scale']
  className?: string
}) {
  const total = stats.graded + stats.ungraded
  if (total === 0) return null
  const pct = (n: number) => `${Math.round((n / total) * 100)}%`
  const segments = [
    ...Object.entries(stats.counts).map(([level, count]) => ({
      key: level,
      count,
      label: prettifyLevel(level),
      color: gradeColor(level, scale)?.hex,
    })),
    { key: '__ungraded', count: stats.ungraded, label: 'Not graded', color: undefined },
  ].filter((s) => s.count > 0)

  return (
    <div className={`flex w-full gap-[2px] overflow-hidden rounded ${className}`}>
      {segments.map((s) => (
        <div
          key={s.key}
          title={`${s.label}: ${s.count} (${pct(s.count)})`}
          className={s.color ? '' : 'bg-slate-100 ring-1 ring-inset ring-slate-200'}
          style={{ flexGrow: s.count, flexBasis: 0, backgroundColor: s.color }}
        />
      ))}
    </div>
  )
}

const HISTOGRAM_BINS = 5

/**
 * Whole-batch/rubric summary above the grading grid: headline numbers, the
 * overall grade distribution, how student scores spread out, and which
 * questions went worst/best. "Score" is the mean position in the grading
 * scale (see `levelScore`), not a mark.
 */
export function GradingStatsSummary({ submissions, rubric }: { submissions: Submission[]; rubric: SubmissionRubric }) {
  const scale = rubric.grading_scale
  const questionIds = new Set(rubric.questions.map((q) => q.id))
  const allAnswers = submissions.flatMap((s) => s.answers.filter((a) => questionIds.has(a.question_id)))
  const overall = gradeStats(allAnswers, scale)
  const expectedAnswers = submissions.length * rubric.questions.length

  const studentScores = submissions
    .map((s) => gradeStats(s.answers, scale).meanScore)
    .filter((score): score is number => score !== null)
  const fullyGraded = submissions.filter(
    (s) => rubric.questions.every((q) => {
      const a = s.answers.find((ans) => ans.question_id === q.id)
      return a && effectiveGrade(a)
    }),
  ).length

  const questionScores = rubric.questions
    .map((q) => ({
      question: q,
      score: gradeStats(
        submissions.flatMap((s) => s.answers.filter((a) => a.question_id === q.id)),
        scale,
      ).meanScore,
    }))
    .filter((row): row is { question: typeof row.question; score: number } => row.score !== null)
    .sort((a, b) => a.score - b.score)

  const bins = Array.from({ length: HISTOGRAM_BINS }, () => 0)
  for (const score of studentScores) bins[Math.min(HISTOGRAM_BINS - 1, Math.floor(score * HISTOGRAM_BINS))]++
  const maxBin = Math.max(1, ...bins)
  const histogramColor = gradeColor(Object.keys(scale).at(-1) ?? '', scale)?.hex ?? '#256abf'

  const pctOfAll = (n: number) => (overall.graded + overall.ungraded > 0 ? Math.round((n / (overall.graded + overall.ungraded)) * 100) : 0)

  return (
    <div className="space-y-4 rounded-lg border border-slate-200 bg-white p-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatTile label="Submissions" value={String(submissions.length)} detail={`${fullyGraded} fully graded`} />
        <StatTile
          label="Answers graded"
          value={`${overall.graded} / ${expectedAnswers}`}
          detail={expectedAnswers > 0 ? `${Math.round((overall.graded / expectedAnswers) * 100)}%` : undefined}
        />
        <StatTile
          label="Mean score"
          value={formatScore(overall.meanScore)}
          detail="position in the scale"
          title="Mean position of every graded answer in the grading scale: 0% = all at the lowest level, 100% = all at the highest. A relative indicator, not a mark."
        />
        <StatTile
          label="Median student"
          value={formatScore(median(studentScores))}
          detail={studentScores.length > 0 ? `range ${formatScore(Math.min(...studentScores))}–${formatScore(Math.max(...studentScores))}` : undefined}
        />
        <StatTile label="Manual overrides" value={String(overall.manual)} detail={`${pctOfAll(overall.manual)}% of answers`} />
        <StatTile label="Blank answers" value={String(overall.blank)} detail={`${pctOfAll(overall.blank)}% of answers`} />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-2 lg:col-span-2">
          <h3 className="text-xs font-medium uppercase text-slate-500">Grade distribution (all answers)</h3>
          <GradeDistributionBar stats={overall} scale={scale} className="h-4" />
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
            {Object.entries(overall.counts).map(([level, count]) => (
              <span key={level} className="inline-flex items-center gap-1.5">
                <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: gradeColor(level, scale)?.hex }} />
                {prettifyLevel(level)}
                <span className="tabular-nums text-slate-900">{count}</span>
                <span className="tabular-nums text-slate-400">({pctOfAll(count)}%)</span>
              </span>
            ))}
            {overall.ungraded > 0 && (
              <span className="inline-flex items-center gap-1.5">
                <span className="inline-block h-2.5 w-2.5 rounded-sm bg-slate-100 ring-1 ring-inset ring-slate-200" />
                Not graded
                <span className="tabular-nums text-slate-900">{overall.ungraded}</span>
                <span className="tabular-nums text-slate-400">({pctOfAll(overall.ungraded)}%)</span>
              </span>
            )}
          </div>

          {questionScores.length > 1 && (
            <div className="grid gap-x-6 gap-y-1 pt-2 text-xs sm:grid-cols-2">
              <QuestionScoreList title="Hardest questions" rows={questionScores.slice(0, 3)} />
              <QuestionScoreList title="Easiest questions" rows={[...questionScores].reverse().slice(0, 3)} />
            </div>
          )}
        </div>

        <div className="space-y-2">
          <h3 className="text-xs font-medium uppercase text-slate-500">Student scores</h3>
          {studentScores.length === 0 ? (
            <p className="text-xs text-slate-400">No graded submissions yet.</p>
          ) : (
            <div>
              <div className="flex h-24 items-end gap-[2px] border-b border-slate-200">
                {bins.map((count, i) => (
                  <div
                    key={i}
                    className="flex h-full flex-1 flex-col justify-end"
                    title={`${binLabel(i)}: ${count} student(s)`}
                  >
                    {count > 0 && <span className="mb-0.5 text-center text-[10px] tabular-nums text-slate-500">{count}</span>}
                    <div
                      className="rounded-t"
                      style={{ height: `${(count / maxBin) * 80}%`, backgroundColor: count > 0 ? histogramColor : undefined }}
                    />
                  </div>
                ))}
              </div>
              <div className="mt-1 flex gap-[2px] text-[10px] text-slate-400">
                {bins.map((_, i) => (
                  <span key={i} className="flex-1 text-center tabular-nums">
                    {binLabel(i)}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function binLabel(i: number): string {
  const width = 100 / HISTOGRAM_BINS
  return `${Math.round(i * width)}–${Math.round((i + 1) * width)}%`
}

function StatTile({ label, value, detail, title }: { label: string; value: string; detail?: string; title?: string }) {
  return (
    <div className="rounded-md bg-slate-50 px-3 py-2" title={title}>
      <p className="text-xs text-slate-500">{label}</p>
      <p className="text-lg font-semibold tabular-nums text-slate-900">{value}</p>
      {detail && <p className="text-xs text-slate-400">{detail}</p>}
    </div>
  )
}

function QuestionScoreList({
  title,
  rows,
}: {
  title: string
  rows: { question: SubmissionRubric['questions'][number]; score: number }[]
}) {
  return (
    <div>
      <p className="font-medium text-slate-600">{title}</p>
      <ul>
        {rows.map(({ question, score }) => (
          <li key={question.id} className="flex justify-between gap-2 text-slate-600">
            <span className="truncate">{question.title}</span>
            <span className="tabular-nums text-slate-900">{formatScore(score)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

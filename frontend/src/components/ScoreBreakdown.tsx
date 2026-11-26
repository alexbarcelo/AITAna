import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import GradeBadge from './GradeBadge'
import { formatRawScore, formatScore, type SubmissionScore } from '../lib/gradeStats'
import { formatLevelScore } from '../lib/gradingScale'
import type { GradingScale } from '../api/types'

/**
 * A submission's weighted score, and the click-to-open breakdown behind it:
 * one row per rubric question (weight, effective grade, level score,
 * weighted points out of the question's maximum) plus the totals. Shared by
 * the submission page's header and the grading overview's Score/Raw cells,
 * from the same `submissionScore` (`lib/gradeStats.ts`).
 *
 * The popover is portalled to `<body>` with fixed positioning, so the grading
 * grid's scroll container doesn't clip it; it closes on an outside click,
 * Escape, or any scroll/resize outside it (rather than drifting away from
 * its trigger).
 */

export const PARTIAL_NOTE = 'Partial: some question is not graded yet (it counts as 0 until it is)'

export function ScoreBreakdownTable({ score, scale }: { score: SubmissionScore; scale: GradingScale }) {
  return (
    <div className="text-xs">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-slate-200 text-left text-[10px] uppercase tracking-wide text-slate-400">
            <th className="py-1 pr-3 font-medium">Question</th>
            <th className="px-2 py-1 text-right font-medium">Weight</th>
            <th className="px-2 py-1 font-medium">Grade</th>
            <th className="px-2 py-1 text-right font-medium">Level score</th>
            <th className="py-1 pl-2 text-right font-medium">Points</th>
          </tr>
        </thead>
        <tbody>
          {score.questions.map((row) => {
            const ignored = row.weight === 0
            return (
              <tr
                key={row.question.id}
                className={`border-b border-slate-100 ${ignored ? 'text-slate-400' : 'text-slate-700'}`}
                title={ignored ? 'Weight 0: not counted in the score' : undefined}
              >
                <td className="max-w-56 truncate py-1 pr-3" title={row.question.title}>
                  {row.question.title}
                </td>
                <td className="px-2 py-1 text-right tabular-nums">{formatRawScore(row.weight)}</td>
                <td className="whitespace-nowrap px-2 py-1">
                  {row.level ? (
                    <>
                      <GradeBadge level={row.level} scale={scale} />
                      {row.manual && (
                        <span className="ml-1 text-slate-400" title="Grade set manually by a TA">
                          edited
                        </span>
                      )}
                    </>
                  ) : (
                    <span className="text-slate-400">Not graded</span>
                  )}
                </td>
                <td className="px-2 py-1 text-right tabular-nums">
                  {row.levelScore === null ? (
                    <span className="text-slate-400" title={row.level ? 'Level not in the current scale' : undefined}>
                      —
                    </span>
                  ) : (
                    formatLevelScore(row.levelScore)
                  )}
                </td>
                <td className="whitespace-nowrap py-1 pl-2 text-right tabular-nums">
                  {ignored ? (
                    <span>—</span>
                  ) : (
                    <>
                      <span className={row.levelScore === null ? 'text-slate-400' : 'font-medium text-slate-900'}>
                        {formatRawScore(row.points)}
                      </span>
                      <span className="text-slate-400"> / {formatRawScore(row.maxPoints)}</span>
                    </>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
        <tfoot>
          <tr className="font-medium text-slate-900">
            <td className="pt-1.5 pr-3" colSpan={4}>
              Total
            </td>
            <td className="whitespace-nowrap pt-1.5 pl-2 text-right tabular-nums">
              {formatRawScore(score.raw)}
              <span className="font-normal text-slate-400"> / {formatRawScore(score.max)}</span>
            </td>
          </tr>
          <tr className="font-medium text-slate-900">
            <td className="pr-3" colSpan={4}>
              Score
            </td>
            <td className="pl-2 text-right tabular-nums">{formatScore(score.score)}</td>
          </tr>
        </tfoot>
      </table>
      <p className="mt-2 text-slate-500">
        Points = weight x level score. Score = total over the best possible total (every question at the top level).
      </p>
      {!score.complete && <p className="mt-1 text-amber-700">{PARTIAL_NOTE}.</p>}
    </div>
  )
}

/** A button (`children` is its face) that toggles the breakdown popover. */
export function ScoreBreakdownButton({
  score,
  scale,
  className = '',
  children,
}: {
  score: SubmissionScore
  scale: GradingScale
  className?: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)

  // Place below the trigger (above if it doesn't fit), kept inside the viewport.
  useLayoutEffect(() => {
    if (!open || !triggerRef.current || !popoverRef.current) return
    const trigger = triggerRef.current.getBoundingClientRect()
    const popover = popoverRef.current.getBoundingClientRect()
    const margin = 8
    const below = trigger.bottom + 4
    const top =
      below + popover.height > window.innerHeight - margin && trigger.top - 4 - popover.height > margin
        ? trigger.top - 4 - popover.height
        : below
    const left = Math.max(margin, Math.min(trigger.left, window.innerWidth - popover.width - margin))
    setPosition({ top, left })
  }, [open])

  useEffect(() => {
    if (!open) return
    const inside = (target: EventTarget | null) =>
      target instanceof Node && (popoverRef.current?.contains(target) || triggerRef.current?.contains(target))
    const close = () => setOpen(false)
    const onPointerDown = (e: PointerEvent) => !inside(e.target) && close()
    const onScroll = (e: Event) => !inside(e.target) && close()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close()
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', close)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', close)
    }
  }, [open])

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        onClick={() => {
          setPosition(null)
          setOpen((v) => !v)
        }}
        title={`Click for the score breakdown${score.complete ? '' : ` -- ${PARTIAL_NOTE}`}`}
        className={`rounded hover:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 ${className}`}
      >
        {children}
      </button>
      {open &&
        createPortal(
          <div
            ref={popoverRef}
            role="dialog"
            aria-label="Score breakdown"
            className="fixed z-50 max-h-[70vh] w-max min-w-80 max-w-[min(36rem,calc(100vw-1rem))] overflow-auto rounded-md border border-slate-200 bg-white p-3 shadow-lg"
            // Hidden until measured, so it never flashes at the wrong place.
            style={position ? { top: position.top, left: position.left } : { top: 0, left: 0, visibility: 'hidden' }}
          >
            <ScoreBreakdownTable score={score} scale={scale} />
          </div>,
          document.body,
        )}
    </>
  )
}

/** "78% · 23.5 / 30", greyed out while partial -- the face of the submission page's button. */
export function ScoreSummary({ score }: { score: SubmissionScore }) {
  const tone = score.complete ? 'text-slate-900' : 'text-slate-400'
  return (
    <span className="inline-flex items-baseline gap-2 tabular-nums">
      <span className={`text-lg font-semibold ${tone}`}>{formatScore(score.score)}</span>
      <span className="text-sm">
        <span className={tone}>{formatRawScore(score.raw)}</span>
        <span className="text-slate-400"> / {formatRawScore(score.max)}</span>
      </span>
    </span>
  )
}

import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { API_URL } from '../api/client'
import { useSaveSegments, useSubmission, useSubmissionDocument, useSubmissions, useSuggestSegments } from '../api/hooks'
import type { AnswerSegment, Question, Submission } from '../api/types'
import { IN_PROGRESS_STATUSES, submissionName } from '../api/types'
import StatusBadge from '../components/StatusBadge'

/**
 * Side-by-side editor for a generic document's segmentation (AGENTS.md
 * "Generic-document submissions"): the PDF (a .docx upload was already
 * converted to one) on the left, the Markdown it was converted to on
 * the right -- numbered lines, exactly what the grading LLM will see. Pick a
 * question, click the line where its answer starts (shift-click: where it
 * ends, to leave out dead lines). Segments are line numbers into that
 * Markdown, same as the backend stores them.
 */

// Literal class names (Tailwind only generates classes it can see verbatim).
const COLORS = [
  { bg: 'bg-sky-50', border: 'border-sky-400', dot: 'bg-sky-400' },
  { bg: 'bg-amber-50', border: 'border-amber-400', dot: 'bg-amber-400' },
  { bg: 'bg-emerald-50', border: 'border-emerald-400', dot: 'bg-emerald-400' },
  { bg: 'bg-rose-50', border: 'border-rose-400', dot: 'bg-rose-400' },
  { bg: 'bg-violet-50', border: 'border-violet-400', dot: 'bg-violet-400' },
  { bg: 'bg-lime-50', border: 'border-lime-500', dot: 'bg-lime-500' },
  { bg: 'bg-orange-50', border: 'border-orange-400', dot: 'bg-orange-400' },
  { bg: 'bg-teal-50', border: 'border-teal-400', dot: 'bg-teal-400' },
]

/** `undefined` = not decided yet; `start: null` = not answered. */
type Draft = Record<string, { start: number | null; end: number | null } | undefined>

function draftFrom(segments: AnswerSegment[] | null | undefined): Draft {
  const draft: Draft = {}
  for (const s of segments ?? []) draft[s.question_id] = { start: s.start_line ?? null, end: s.end_line ?? null }
  return draft
}

function draftToSegments(draft: Draft, questions: Question[]): AnswerSegment[] {
  return questions.flatMap((q) => {
    const d = draft[q.id]
    return d ? [{ question_id: q.id, start_line: d.start, end_line: d.start === null ? null : d.end }] : []
  })
}

/** Mirrors the backend's `resolve_segments`: implicit end = line before the
 * next start (or the last line). Returns `{questionId: [start, end]}`. */
function resolveSpans(draft: Draft, lineCount: number): Map<string, [number, number]> {
  const placed = Object.entries(draft)
    .filter((e): e is [string, { start: number; end: number | null }] => e[1] != null && e[1].start !== null)
    .sort((a, b) => a[1].start - b[1].start)
  const spans = new Map<string, [number, number]>()
  placed.forEach(([qid, d], i) => {
    const end = d.end ?? (i + 1 < placed.length ? placed[i + 1][1].start - 1 : lineCount)
    spans.set(qid, [d.start, end])
  })
  return spans
}

const IMAGE_LINE_RE = /!\[[^\]]*\]\((img-[^)\s]+)\)/

export default function SegmentationEditorPage() {
  const { id } = useParams<{ id: string }>()
  const { data: submission, isLoading, error } = useSubmission(id)
  const { data: document } = useSubmissionDocument(id, Boolean(submission?.document))

  if (isLoading) return <p className="text-sm text-slate-500">Loading...</p>
  if (error || !submission) return <p className="text-sm text-red-600">Failed to load this submission.</p>
  if (!submission.document) {
    return (
      <div className="space-y-2">
        <StatusBadge status={submission.status} />
        <p className="text-sm text-slate-500">
          {IN_PROGRESS_STATUSES.includes(submission.status)
            ? 'The PDF is being converted -- this page refreshes automatically.'
            : (submission.error ?? 'This submission has no converted document.')}
        </p>
      </div>
    )
  }
  if (!document) return <p className="text-sm text-slate-500">Loading document...</p>

  return <Editor submission={submission} markdown={document.markdown} pageStarts={document.page_start_lines} images={document.images} />
}

function Editor({
  submission,
  markdown,
  pageStarts,
  images,
}: {
  submission: Submission
  markdown: string
  pageStarts: number[]
  images: { index: number; name: string; line: number }[]
}) {
  const navigate = useNavigate()
  const saveSegments = useSaveSegments()
  const suggest = useSuggestSegments()
  const { data: batchSubmissions } = useSubmissions(submission.batch ? { batch_id: submission.batch._id } : undefined)

  const questions = submission.rubric.questions
  const lines = useMemo(() => markdown.split('\n'), [markdown])
  const imageIndexByName = useMemo(() => new Map(images.map((img) => [img.name, img.index])), [images])
  const colorOf = useMemo(() => new Map(questions.map((q, i) => [q.id, COLORS[i % COLORS.length]])), [questions])

  // Re-initialize the draft whenever the server's segments change (e.g. an
  // LLM suggestion just finished) -- adjusting state during render, keyed by
  // `segmented_at`, rather than in an effect.
  const serverKey = `${submission.segmented_at ?? ''}|${submission.status}`
  const [loadedKey, setLoadedKey] = useState(serverKey)
  const [draft, setDraft] = useState<Draft>(() => draftFrom(submission.segments))
  const [dirty, setDirty] = useState(false)
  if (serverKey !== loadedKey) {
    setLoadedKey(serverKey)
    if (!dirty || submission.segmentation_source === 'llm') {
      setDraft(draftFrom(submission.segments))
      setDirty(false)
    }
  }

  const [active, setActive] = useState<string>(questions.find((q) => !draft[q.id])?.id ?? questions[0]?.id ?? '')
  const [pdfPage, setPdfPage] = useState(1)
  const [hint, setHint] = useState<string | null>(null)

  const busy = IN_PROGRESS_STATUSES.includes(submission.status)
  const spans = resolveSpans(draft, lines.length)
  const firstStart = Math.min(...[...spans.values()].map(([s]) => s), Infinity)
  const lineOwner = new Map<number, string>()
  for (const [qid, [start, end]] of spans) for (let l = start; l <= end; l++) lineOwner.set(l, qid)
  const undecided = questions.filter((q) => !draft[q.id])
  const overlaps = [...spans.entries()]
    .sort((a, b) => a[1][0] - b[1][0])
    .some(([, [, end]], i, arr) => i + 1 < arr.length && end >= arr[i + 1][1][0])

  const pageOfLine = (line: number) => {
    let page = 1
    pageStarts.forEach((start, i) => {
      if (start <= line) page = i + 1
    })
    return page
  }

  function update(qid: string, value: Draft[string]) {
    setDraft((d) => ({ ...d, [qid]: value }))
    setDirty(true)
    setHint(null)
  }

  function handleLineClick(line: number, shift: boolean) {
    setPdfPage(pageOfLine(line))
    if (!active) return
    const current = draft[active]
    if (shift) {
      if (!current || current.start === null || line < current.start) {
        setHint('Shift-click sets where the answer ends -- set its start first, and pick a line after it.')
        return
      }
      update(active, { start: current.start, end: line })
      return
    }
    // A line starts at most one answer: take it away from whoever had it.
    const next: Draft = { ...draft }
    for (const [qid, d] of Object.entries(next)) if (qid !== active && d?.start === line) next[qid] = undefined
    next[active] = { start: line, end: current?.end != null && current.end >= line ? current.end : null }
    setDraft(next)
    setDirty(true)
    setHint(null)
    // Move on to the next question nobody placed yet -- the usual flow is
    // clicking through the answers top to bottom.
    const following = questions.find((q) => q.id !== active && !next[q.id])
    if (following) setActive(following.id)
  }

  const nextInBatch = (() => {
    if (!batchSubmissions) return null
    const idx = batchSubmissions.findIndex((s) => s._id === submission._id)
    const ordered = [...batchSubmissions.slice(idx + 1), ...batchSubmissions.slice(0, Math.max(idx, 0))]
    return ordered.find((s) => s._id !== submission._id && s.status === 'awaiting_segmentation') ?? null
  })()

  function save(grade: boolean, goNext = false) {
    saveSegments.mutate(
      { submissionId: submission._id, segments: draftToSegments(draft, questions), grade },
      {
        onSuccess: () => {
          setDirty(false)
          if (goNext && nextInBatch) navigate(`/submissions/${nextInBatch._id}/segments`)
          else if (grade) navigate(`/submissions/${submission._id}`)
        },
      },
    )
  }

  const saveError = saveSegments.error as { detail?: unknown } | null
  const canGrade = undecided.length === 0 && !overlaps && !busy

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col gap-3">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold">
            <Link to={`/submissions/${submission._id}`} className="hover:underline">
              {submissionName(submission) ?? 'Unmatched'}
            </Link>{' '}
            &middot; {submission.rubric.title} &middot; segmentation
          </h1>
          <p className="text-xs text-slate-500">
            Pick a question, then click the line where its answer starts. Shift-click sets where it ends (to leave out
            dead lines). Greyed lines are not graded.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={submission.status} />
          {submission.segmentation_source === 'llm' && !dirty && (
            <span className="text-xs text-violet-700" title="Suggested by the LLM, not reviewed yet">
              LLM suggestion
            </span>
          )}
          {dirty && <span className="text-xs text-amber-700">unsaved changes</span>}
          <button
            onClick={() => {
              if (dirty && !window.confirm('Replace your unsaved segmentation with an LLM suggestion?')) return
              suggest.mutate(submission._id)
            }}
            disabled={busy || suggest.isPending}
            title="Ask the active model where each answer starts (replaces the current segmentation)"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 disabled:opacity-50"
          >
            {submission.status === 'segmenting' ? 'LLM is working...' : 'Suggest with LLM'}
          </button>
          <button
            onClick={() => save(false)}
            disabled={busy || saveSegments.isPending}
            title="Save without grading -- the submission stays waiting for segmentation"
            className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 disabled:opacity-50"
          >
            Save
          </button>
          <button
            onClick={() => save(true)}
            disabled={!canGrade || saveSegments.isPending}
            title={undecided.length ? `Still to place: ${undecided.map((q) => q.title).join(', ')}` : 'Save and grade'}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {submission.status === 'graded' ? 'Save & re-grade' : 'Save & grade'}
          </button>
          {nextInBatch && (
            <button
              onClick={() => save(true, true)}
              disabled={!canGrade || saveSegments.isPending}
              title="Save, grade, and open the next submission of this batch still waiting for segmentation"
              className="rounded-md bg-violet-700 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              Save & grade, next
            </button>
          )}
        </div>
      </div>

      {(hint || saveError || submission.error || overlaps) && (
        <p className="rounded-md bg-red-50 px-3 py-1.5 text-sm text-red-800">
          {hint ??
            (saveError ? String(saveError.detail ?? 'Saving failed.') : null) ??
            (overlaps ? 'Two answers overlap -- an end line is past where the next answer starts.' : submission.error)}
        </p>
      )}

      {/* Question strip */}
      <div className="flex flex-wrap gap-2">
        {questions.map((q) => {
          const d = draft[q.id]
          const span = spans.get(q.id)
          const color = colorOf.get(q.id)!
          const isActive = q.id === active
          return (
            <div
              key={q.id}
              className={`flex items-center gap-2 rounded-md border px-2 py-1 text-xs ${
                isActive ? 'border-slate-900 bg-white shadow-sm' : 'border-slate-200 bg-white'
              }`}
            >
              <button onClick={() => setActive(q.id)} className="flex items-center gap-1.5" title={q.question}>
                <span className={`inline-block h-2.5 w-2.5 rounded-full ${color.dot}`} />
                <span className="font-medium">{q.title}</span>
                <span className="text-slate-500">
                  {!d ? (
                    <span className="text-amber-700">not set</span>
                  ) : d.start === null ? (
                    'no answer'
                  ) : span ? (
                    `L${span[0]}–${span[1]}${d.end != null ? '' : '…'}`
                  ) : (
                    ''
                  )}
                </span>
              </button>
              {isActive && (
                <span className="flex gap-1 border-l border-slate-200 pl-2">
                  {d?.start != null && (
                    <button
                      onClick={() => {
                        const line = d.start as number
                        setPdfPage(pageOfLine(line))
                        window.document.getElementById(`seg-line-${line}`)?.scrollIntoView({ block: 'center' })
                      }}
                      className="text-slate-600 hover:underline"
                    >
                      show
                    </button>
                  )}
                  {d?.end != null && (
                    <button onClick={() => update(q.id, { start: d.start, end: null })} className="text-slate-600 hover:underline">
                      clear end
                    </button>
                  )}
                  <button
                    onClick={() => update(q.id, { start: null, end: null })}
                    className="text-slate-600 hover:underline"
                    title="The student did not answer this question (graded as blank)"
                  >
                    no answer
                  </button>
                  {d && (
                    <button onClick={() => update(q.id, undefined)} className="text-slate-600 hover:underline">
                      unset
                    </button>
                  )}
                </span>
              )}
            </div>
          )
        })}
      </div>

      {/* Side by side */}
      <div className="grid min-h-0 flex-1 grid-cols-2 gap-3">
        <iframe
          // Remounted on page change: the browser's PDF viewer only honours
          // `#page=` on load.
          key={pdfPage}
          title="Submitted PDF"
          src={`${API_URL}/submissions/${submission._id}/file?inline=true#page=${pdfPage}`}
          className="h-full w-full rounded-lg border border-slate-200 bg-white"
        />
        <div className="h-full overflow-y-auto rounded-lg border border-slate-200 bg-white py-2 font-mono text-xs">
          {lines.map((text, i) => {
            const line = i + 1
            const owner = lineOwner.get(line)
            const color = owner ? colorOf.get(owner) : undefined
            const pageIndex = pageStarts.indexOf(line)
            const isStart = owner !== undefined && spans.get(owner)?.[0] === line
            const imageMatch = IMAGE_LINE_RE.exec(text)
            const imageIndex = imageMatch ? imageIndexByName.get(imageMatch[1]) : undefined
            const dead = !owner && line >= Math.min(firstStart, lines.length + 1)
            return (
              <div key={line}>
                {pageIndex >= 0 && (
                  <button
                    onClick={() => setPdfPage(pageIndex + 1)}
                    className="my-1 block w-full border-t border-dashed border-slate-300 text-center text-[10px] text-slate-400 hover:text-slate-700"
                  >
                    page {pageIndex + 1}
                  </button>
                )}
                <div
                  id={`seg-line-${line}`}
                  onClick={(e) => handleLineClick(line, e.shiftKey)}
                  className={`flex cursor-pointer select-none border-l-4 hover:bg-slate-100 ${
                    color ? `${color.bg} ${color.border}` : 'border-transparent'
                  } ${!owner ? 'text-slate-400' : 'text-slate-800'} ${dead ? 'line-through decoration-slate-300' : ''}`}
                >
                  <span className="w-12 shrink-0 pr-2 text-right text-slate-400">{line}</span>
                  <span className="min-w-0 flex-1 whitespace-pre-wrap break-words pr-2">
                    {isStart && owner && (
                      <span className={`mr-2 rounded px-1 font-sans text-[10px] font-semibold text-white ${color?.dot}`}>
                        {questions.find((q) => q.id === owner)?.title}
                      </span>
                    )}
                    {imageIndex !== undefined ? (
                      <img
                        src={`${API_URL}/submissions/${submission._id}/document/images/${imageIndex}`}
                        alt={imageMatch?.[1]}
                        className="my-1 max-h-48 rounded border border-slate-200"
                      />
                    ) : (
                      text || ' '
                    )}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

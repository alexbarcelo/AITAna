import { useState } from 'react'
import { useCourses, useCreateRubric, useEditions, useUploadRubricYaml } from '../api/hooks'
import { GRADING_SCALE_PRESETS } from '../api/gradingScalePresets'
import { FORMAT_FILE_INFO } from '../api/types'
import type { Question, SubmissionFormat } from '../api/types'

const FORMAT_OPTIONS = Object.keys(FORMAT_FILE_INFO) as SubmissionFormat[]

type GradingScaleRow = { id: string; description: string }

function presetToRows(scale: Record<string, string>): GradingScaleRow[] {
  return Object.entries(scale).map(([id, description]) => ({ id, description }))
}

/**
 * Free-form editor for a rubric's grading_scale: an ordered id/description
 * list (order matters -- worst to best, see GradingScale's doc comment),
 * seeded from a quick-pick preset (GRADING_SCALE_PRESETS) but fully
 * editable from there -- picking a preset is a starting point, not a lock-in.
 */
function GradingScaleEditor({ rows, onChange }: { rows: GradingScaleRow[]; onChange: (rows: GradingScaleRow[]) => void }) {
  function updateRow(index: number, patch: Partial<GradingScaleRow>) {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  return (
    <div className="space-y-2 rounded-md border border-slate-100 bg-slate-50 p-3">
      <div className="flex items-center justify-between">
        <label className="block text-xs font-medium text-slate-600">
          Grading levels (worst first, best last -- the LLM assigns exactly one of these)
        </label>
        <select
          value=""
          onChange={(e) => {
            const preset = GRADING_SCALE_PRESETS.find((p) => p.label === e.target.value)
            if (preset) onChange(presetToRows(preset.scale))
          }}
          className="rounded-md border border-slate-300 px-2 py-1 text-xs"
        >
          <option value="" disabled>
            Load a preset...
          </option>
          {GRADING_SCALE_PRESETS.map((preset) => (
            <option key={preset.label} value={preset.label}>
              {preset.label}
            </option>
          ))}
        </select>
      </div>

      <div className="space-y-1.5">
        {rows.map((row, i) => (
          <div key={i} className="flex gap-2">
            <input
              required
              placeholder="level id (e.g. solid)"
              value={row.id}
              onChange={(e) => updateRow(i, { id: e.target.value })}
              className="w-40 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
            <input
              required
              placeholder="Description shown to the LLM"
              value={row.description}
              onChange={(e) => updateRow(i, { description: e.target.value })}
              className="flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
            {rows.length > 1 && (
              <button
                type="button"
                onClick={() => onChange(rows.filter((_, idx) => idx !== i))}
                className="text-xs text-red-600 hover:underline"
              >
                Remove
              </button>
            )}
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={() => onChange([...rows, { id: '', description: '' }])}
        className="text-xs text-slate-600 hover:underline"
      >
        + Add level
      </button>
    </div>
  )
}

function FormatSelect({ value, onChange }: { value: SubmissionFormat; onChange: (format: SubmissionFormat) => void }) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-600">Submission format</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as SubmissionFormat)}
        className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
        title="What file format this rubric's submissions must be uploaded as -- fixed once, can't be changed later."
      >
        {FORMAT_OPTIONS.map((format) => (
          <option key={format} value={format}>
            {FORMAT_FILE_INFO[format].label}
          </option>
        ))}
      </select>
    </div>
  )
}

function CourseSelect({ value, onChange }: { value: string; onChange: (courseId: string) => void }) {
  const { data: courses } = useCourses()
  return (
    <div>
      <label className="block text-xs font-medium text-slate-600">Course</label>
      <select
        required
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
      >
        <option value="" disabled>
          Select a course
        </option>
        {courses?.map((course) => (
          <option key={course._id} value={course._id ?? ''}>
            {course.name}
          </option>
        ))}
      </select>
    </div>
  )
}

function EditionSelect({ value, onChange }: { value: string; onChange: (editionId: string) => void }) {
  // Editions are global (see Edition's doc comment in api/types.ts), so this
  // isn't scoped to whichever course is currently picked -- any edition can
  // apply to any course.
  const { data: editions } = useEditions()
  return (
    <div>
      <label className="block text-xs font-medium text-slate-600">Edition (optional)</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
        title="Leave unset for a rubric reused across editions (e.g. a lab); set it for one unique to a term (e.g. an exam)."
      >
        <option value="">No fixed edition (reusable, e.g. a lab)</option>
        {editions?.map((edition) => (
          <option key={edition._id} value={edition._id ?? ''}>
            {edition.name}
          </option>
        ))}
      </select>
    </div>
  )
}

type DraftQuestion = {
  id: string
  field: string
  title: string
  question: string
  context: string
  rubric: string
  modelAnswer: string
  expectedPointsText: string
  needsPythonSandbox: boolean
}

function emptyQuestion(): DraftQuestion {
  return {
    id: '',
    field: '',
    title: '',
    question: '',
    context: '',
    rubric: '',
    modelAnswer: '',
    expectedPointsText: '',
    needsPythonSandbox: false,
  }
}

function toQuestion(q: DraftQuestion): Question {
  return {
    id: q.id,
    field: q.field || null,
    title: q.title,
    question: q.question,
    context: q.context || null,
    rubric: q.rubric,
    model_answer: q.modelAnswer || null,
    expected_points: q.expectedPointsText
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean),
    needs_python_sandbox: q.needsPythonSandbox,
  }
}

export default function NewRubricForm({ onCreated }: { onCreated?: () => void }) {
  const [mode, setMode] = useState<'upload' | 'manual'>('upload')

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="mb-4 flex gap-1">
        <button
          className={`rounded-md px-3 py-1.5 text-sm font-medium ${mode === 'upload' ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600'}`}
          onClick={() => setMode('upload')}
        >
          Upload YAML
        </button>
        <button
          className={`rounded-md px-3 py-1.5 text-sm font-medium ${mode === 'manual' ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600'}`}
          onClick={() => setMode('manual')}
        >
          Build manually
        </button>
      </div>

      {mode === 'upload' ? <UploadYamlForm onCreated={onCreated} /> : <ManualRubricForm onCreated={onCreated} />}
    </div>
  )
}

function UploadYamlForm({ onCreated }: { onCreated?: () => void }) {
  const uploadRubric = useUploadRubricYaml()
  const [file, setFile] = useState<File | null>(null)
  const [slug, setSlug] = useState('')
  const [courseId, setCourseId] = useState('')
  const [editionId, setEditionId] = useState('')
  const [format, setFormat] = useState<SubmissionFormat>('pdf')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return
    uploadRubric.mutate(
      { file, slug: slug || undefined, courseId: courseId || undefined, editionId: editionId || undefined, format },
      {
        onSuccess: () => {
          setFile(null)
          setSlug('')
          setCourseId('')
          setEditionId('')
          setFormat('pdf')
          onCreated?.()
        },
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <p className="text-sm text-slate-500">
        Upload a rubric YAML file (see README.md for the format -- a top-level <code className="rounded bg-slate-100 px-1">questions</code> list,
        one entry per question). The course can also come from a <code className="rounded bg-slate-100 px-1">course_slug:</code> key in the
        file instead of the dropdown below, and the format from a <code className="rounded bg-slate-100 px-1">format:</code> key instead of
        the dropdown below. Grading levels default to the standard 4-level scale unless the file has its own top-level
        <code className="rounded bg-slate-100 px-1">grading_scale:</code> mapping (no dropdown for this one -- see "Build manually" for a
        quick-pick editor instead).
      </p>
      <div>
        <label className="block text-xs font-medium text-slate-600">YAML file</label>
        <input
          required
          type="file"
          accept=".yaml,.yml"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="mt-1 w-full text-sm"
        />
      </div>
      <div className="flex gap-3">
        <div className="flex-1">
          <label className="block text-xs font-medium text-slate-600">Slug override (optional)</label>
          <input
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            placeholder="defaults to the filename, or a `slug:`/`title:` key in the YAML"
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </div>
        <CourseSelect value={courseId} onChange={setCourseId} />
        <EditionSelect value={editionId} onChange={setEditionId} />
        <FormatSelect value={format} onChange={setFormat} />
      </div>
      <button
        type="submit"
        disabled={uploadRubric.isPending}
        className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
      >
        Upload
      </button>
      {uploadRubric.isError && (
        <p className="text-sm text-red-600">{(uploadRubric.error as { message?: string })?.message ?? 'Upload failed.'}</p>
      )}
    </form>
  )
}

function ManualRubricForm({ onCreated }: { onCreated?: () => void }) {
  const createRubric = useCreateRubric()
  const [title, setTitle] = useState('')
  const [slug, setSlug] = useState('')
  const [courseId, setCourseId] = useState('')
  const [editionId, setEditionId] = useState('')
  const [format, setFormat] = useState<SubmissionFormat>('pdf')
  const [gradingScaleRows, setGradingScaleRows] = useState<GradingScaleRow[]>(presetToRows(GRADING_SCALE_PRESETS[0].scale))
  const [questions, setQuestions] = useState<DraftQuestion[]>([emptyQuestion()])

  function updateQuestion(index: number, patch: Partial<DraftQuestion>) {
    setQuestions((qs) => qs.map((q, i) => (i === index ? { ...q, ...patch } : q)))
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    createRubric.mutate(
      {
        title,
        slug: slug || undefined,
        course_id: courseId,
        edition_id: editionId || undefined,
        format,
        // Row order is preserved into the dict -- see GradingScale's doc
        // comment for why that order (worst -> best) is load-bearing, not
        // cosmetic.
        grading_scale: Object.fromEntries(gradingScaleRows.map((row) => [row.id.trim(), row.description])),
        questions: questions.map(toQuestion),
      },
      {
        onSuccess: () => {
          setTitle('')
          setSlug('')
          setCourseId('')
          setEditionId('')
          setFormat('pdf')
          setGradingScaleRows(presetToRows(GRADING_SCALE_PRESETS[0].scale))
          setQuestions([emptyQuestion()])
          onCreated?.()
        },
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="flex gap-3">
        <div className="flex-1">
          <label className="block text-xs font-medium text-slate-600">Title</label>
          <input
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </div>
        <div className="w-48">
          <label className="block text-xs font-medium text-slate-600">Slug (optional)</label>
          <input
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            placeholder="derived from title"
            className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
          />
        </div>
        <CourseSelect value={courseId} onChange={setCourseId} />
        <EditionSelect value={editionId} onChange={setEditionId} />
        <FormatSelect value={format} onChange={setFormat} />
      </div>

      <GradingScaleEditor rows={gradingScaleRows} onChange={setGradingScaleRows} />

      <div className="space-y-3">
        {questions.map((q, i) => (
          <div key={i} className="space-y-2 rounded-md border border-slate-100 bg-slate-50 p-3">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-slate-500">Question {i + 1}</span>
              {questions.length > 1 && (
                <button
                  type="button"
                  onClick={() => setQuestions((qs) => qs.filter((_, idx) => idx !== i))}
                  className="text-xs text-red-600 hover:underline"
                >
                  Remove
                </button>
              )}
            </div>
            <div className="flex gap-2">
              <input
                required
                placeholder="id (e.g. answer1)"
                value={q.id}
                onChange={(e) => updateQuestion(i, { id: e.target.value })}
                className="w-40 rounded-md border border-slate-300 px-2 py-1 text-sm"
              />
              <input
                placeholder="Answer slot (PDF field / notebook tag; defaults to id)"
                value={q.field}
                onChange={(e) => updateQuestion(i, { field: e.target.value })}
                className="w-48 rounded-md border border-slate-300 px-2 py-1 text-sm"
              />
              <input
                required
                placeholder="Short title (for lists/headers)"
                value={q.title}
                onChange={(e) => updateQuestion(i, { title: e.target.value })}
                className="flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
              />
            </div>
            <textarea
              required
              placeholder="Question: the actual question/task text posed to the student."
              value={q.question}
              onChange={(e) => updateQuestion(i, { question: e.target.value })}
              rows={2}
              className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
            <textarea
              placeholder="Context (optional): environment/state info handy for a lab -- what's already set up at this point."
              value={q.context}
              onChange={(e) => updateQuestion(i, { context: e.target.value })}
              rows={2}
              className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
            <textarea
              required
              placeholder="Grading instructions: what a good answer covers, common misconceptions."
              value={q.rubric}
              onChange={(e) => updateQuestion(i, { rubric: e.target.value })}
              rows={3}
              className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
            <textarea
              placeholder="Model answer (optional): a worked/canonical answer, for questions that have one."
              value={q.modelAnswer}
              onChange={(e) => updateQuestion(i, { modelAnswer: e.target.value })}
              rows={2}
              className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
            <textarea
              placeholder="Expected points, one per line (optional)"
              value={q.expectedPointsText}
              onChange={(e) => updateQuestion(i, { expectedPointsText: e.target.value })}
              rows={2}
              className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
            <label
              className="flex items-center gap-2 text-xs text-slate-600"
              title="Gives the grading LLM a sandboxed Python code-execution tool for this question -- e.g. to check a claimed command's output. Slower, and needs Deno installed wherever grading runs; leave off unless this question genuinely benefits from it."
            >
              <input
                type="checkbox"
                checked={q.needsPythonSandbox}
                onChange={(e) => updateQuestion(i, { needsPythonSandbox: e.target.checked })}
              />
              Corrector needs Python sandbox access to grade this question
            </label>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => setQuestions((qs) => [...qs, emptyQuestion()])}
          className="text-sm text-slate-600 hover:underline"
        >
          + Add question
        </button>
        <button
          type="submit"
          disabled={createRubric.isPending}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Create rubric
        </button>
      </div>
      {createRubric.isError && (
        <p className="text-sm text-red-600">{(createRubric.error as { message?: string })?.message ?? 'Failed to create rubric.'}</p>
      )}
    </form>
  )
}

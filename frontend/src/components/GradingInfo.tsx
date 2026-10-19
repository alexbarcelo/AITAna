import type { ReactNode } from 'react'
import type { AnsweredQuestion } from '../api/types'

/**
 * `AnsweredQuestion.grading_info` is a free-form, provider-dependent dict
 * (built by `grading.py`'s `build_grading_info`): every key is optional and
 * each provider reports a different subset -- only OpenRouter returns a
 * `cost`, Ollama durations, OpenAI a service tier, ... This renders the
 * known keys with friendly labels and anything else as-is, skipping
 * whatever is missing. Audit/debugging aid only, hence tucked behind an
 * info icon.
 */

type Info = Record<string, unknown>

const FEATURE_LABELS: Record<string, string> = {
  python_sandbox: 'Python sandbox',
  cell_outputs: 'Cell outputs',
  images: 'Images',
}

// Keys rendered explicitly below; everything else falls through to the
// generic "other" rows so a new backend key still shows up.
const KNOWN_KEYS = new Set([
  'provider',
  'model',
  'model_reported',
  'reasoning_effort',
  'graded_at',
  'elapsed_seconds',
  'llm_calls',
  'tool_calls',
  'blank_short_circuit',
  'features',
  'image_count',
  'tokens',
  'cost',
  'provider_details',
])

const isRecord = (value: unknown): value is Info => typeof value === 'object' && value !== null && !Array.isArray(value)
const asNumber = (value: unknown): number | undefined => (typeof value === 'number' ? value : undefined)

function formatValue(value: unknown): string {
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString() : String(+value.toFixed(6))
  if (typeof value === 'string' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

// OpenRouter reports cost in USD credits; tiny per-call amounts need more
// than two decimals to be meaningful.
const formatCost = (cost: number) => `$${cost < 0.01 ? cost.toPrecision(2) : cost.toFixed(4)}`

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-slate-400">{label}</dt>
      <dd className="break-all text-slate-100">{children}</dd>
    </>
  )
}

function Section({ title, entries }: { title: string; entries: [string, unknown][] }) {
  if (entries.length === 0) return null
  return (
    <>
      <dt className="col-span-2 mt-1.5 border-t border-slate-700 pt-1.5 text-[10px] uppercase tracking-wide text-slate-400">
        {title}
      </dt>
      {entries.map(([key, value]) => (
        <Row key={key} label={key.replaceAll('_', ' ')}>
          {formatValue(value)}
        </Row>
      ))}
    </>
  )
}

/** The ⓘ trigger + popover; shown on hover and on keyboard focus. */
function InfoPopover({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="group relative inline-flex">
      <button
        type="button"
        aria-label={label}
        className="flex h-4 w-4 items-center justify-center rounded-full border border-slate-300 text-[10px] font-semibold leading-none text-slate-400 hover:border-slate-500 hover:text-slate-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
      >
        i
      </button>
      <div
        role="tooltip"
        className="invisible absolute right-0 top-full z-20 mt-1 w-72 rounded-md bg-slate-800 p-3 text-xs opacity-0 shadow-lg transition-opacity group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100"
      >
        {children}
      </div>
    </span>
  )
}

export function GradingInfoTooltip({ answer }: { answer: AnsweredQuestion }) {
  const info = answer.grading_info
  if (!info || Object.keys(info).length === 0) {
    // Still show the icon for a graded answer, so the feature is
    // discoverable on submissions graded before this info was recorded.
    if (!answer.grade) return null
    return (
      <InfoPopover label="Grading details">
        <p className="text-slate-200">
          No grading details recorded for this answer -- it was graded before they were tracked. Re-grade to capture
          them.
        </p>
      </InfoPopover>
    )
  }

  const tokens = isRecord(info.tokens) ? info.tokens : {}
  const providerDetails = isRecord(info.provider_details) ? info.provider_details : {}
  const features = Array.isArray(info.features) ? info.features.map((f) => FEATURE_LABELS[String(f)] ?? String(f)) : []
  const cost = asNumber(info.cost)
  const elapsed = asNumber(info.elapsed_seconds)
  const gradedAt = typeof info.graded_at === 'string' ? new Date(info.graded_at) : undefined
  const other = Object.entries(info).filter(([key]) => !KNOWN_KEYS.has(key))

  return (
    <InfoPopover label="Grading details">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        {info.provider != null && <Row label="provider">{formatValue(info.provider)}</Row>}
        {info.model != null && <Row label="model">{formatValue(info.model)}</Row>}
        {info.model_reported != null && <Row label="reported as">{formatValue(info.model_reported)}</Row>}
        {/* Key present but null = no effort requested; key absent = graded before this was recorded. */}
        {'reasoning_effort' in info && (
          <Row label="reasoning effort">
            {info.reasoning_effort != null ? formatValue(info.reasoning_effort) : 'provider default'}
          </Row>
        )}
        {info.blank_short_circuit === true && <Row label="LLM">not called (blank answer)</Row>}
        {features.length > 0 && <Row label="features">{features.join(', ')}</Row>}
        {info.image_count != null && <Row label="images">{formatValue(info.image_count)}</Row>}
        {info.llm_calls != null && <Row label="LLM calls">{formatValue(info.llm_calls)}</Row>}
        {info.tool_calls != null && <Row label="tool calls">{formatValue(info.tool_calls)}</Row>}
        {elapsed != null && <Row label="time">{elapsed.toFixed(2)} s</Row>}
        {cost != null && <Row label="cost">{formatCost(cost)}</Row>}
        {gradedAt && !Number.isNaN(gradedAt.getTime()) && <Row label="graded at">{gradedAt.toLocaleString()}</Row>}
        <Section title="Tokens" entries={Object.entries(tokens)} />
        <Section title="Provider details" entries={Object.entries(providerDetails)} />
        <Section title="Other" entries={other} />
      </dl>
    </InfoPopover>
  )
}

/** Header-level totals over every answer that has grading info. */
export function SubmissionGradingInfoTooltip({ answers }: { answers: AnsweredQuestion[] }) {
  const infos = answers.map((a) => a.grading_info).filter(isRecord)
  if (infos.length === 0) return null

  const tokenTotals: Record<string, number> = {}
  let cost: number | undefined
  let elapsed = 0
  const models = new Set<string>()
  for (const info of infos) {
    if (isRecord(info.tokens))
      for (const [key, value] of Object.entries(info.tokens))
        if (typeof value === 'number') tokenTotals[key] = (tokenTotals[key] ?? 0) + value
    const c = asNumber(info.cost)
    if (c != null) cost = (cost ?? 0) + c
    elapsed += asNumber(info.elapsed_seconds) ?? 0
    if (info.provider != null || info.model != null) models.add(`${info.provider ?? '?'} / ${info.model ?? '?'}`)
  }

  return (
    <InfoPopover label="Grading totals">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        {[...models].map((m) => (
          <Row key={m} label="model">
            {m}
          </Row>
        ))}
        <Row label="answers">
          {infos.length} of {answers.length} with details
        </Row>
        <Row label="time">{elapsed.toFixed(2)} s</Row>
        {cost != null && <Row label="cost">{formatCost(cost)}</Row>}
        <Section title="Tokens" entries={Object.entries(tokenTotals)} />
      </dl>
    </InfoPopover>
  )
}

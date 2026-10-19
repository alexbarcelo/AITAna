import { useState } from 'react'
import {
  useCreateLlmModel,
  useDeleteLlmModel,
  useLlmProviders,
  useSetDefaultLlmModel,
  useSetFavoriteLlmModel,
  useUpdateLlmModel,
} from '../api/hooks'
import { REASONING_EFFORTS, type ReasoningEffort } from '../api/types'
import { modelLabel, useActiveModel } from '../lib/activeModel'

// Just a hint of what an identifier looks like for each provider.
const MODEL_PLACEHOLDERS: Record<string, string> = {
  openai: 'gpt-4o-mini',
  anthropic: 'claude-sonnet-5',
  ollama: 'llama3.2',
  openrouter: 'z-ai/glm-4.5',
}

/** `''` <-> `null`: "leave the provider's own default". */
function ReasoningEffortSelect({
  value,
  onChange,
  disabled,
  className,
}: {
  value: ReasoningEffort | null
  onChange: (value: ReasoningEffort | null) => void
  disabled?: boolean
  className?: string
}) {
  return (
    <select
      value={value ?? ''}
      disabled={disabled}
      onChange={(e) => onChange((e.target.value || null) as ReasoningEffort | null)}
      className={`rounded-md border border-slate-300 px-2 py-1 text-sm disabled:opacity-50 ${className ?? ''}`}
    >
      <option value="">provider default</option>
      {REASONING_EFFORTS.map((effort) => (
        <option key={effort} value={effort}>
          {effort}
        </option>
      ))}
    </select>
  )
}

function errorDetail(error: unknown, fallback: string): string {
  const detail = (error as { detail?: unknown })?.detail
  return typeof detail === 'string' ? detail : fallback
}

export default function ModelsPage() {
  const { models, activeModel, setActiveModelId } = useActiveModel()
  const setDefault = useSetDefaultLlmModel()
  const setFavorite = useSetFavoriteLlmModel()
  const deleteModel = useDeleteLlmModel()
  const updateModel = useUpdateLlmModel()

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Models</h1>
        <p className="text-sm text-slate-500">
          LLM models available for grading. The <strong>default</strong> is used whenever no model is picked; the{' '}
          <strong>active</strong> model (top right) is what this browser grades with, and starts out as the default.
          Favorites are listed first in the picker. Reasoning effort asks the model to think harder (more tokens,
          slower, and it's what makes the grading trace's "thinking" available) and can be changed at any time. API
          keys stay in the server's environment.
        </p>
      </div>

      <AddModelForm />

      {!models && <p className="text-sm text-slate-500">Loading...</p>}
      {models && models.length === 0 && <p className="text-slate-400">No models yet -- add one above.</p>}

      {models && models.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 text-left text-xs uppercase text-slate-400">
              <tr>
                <th className="w-10 px-3 py-2" aria-label="Favorite" />
                <th className="px-3 py-2">Provider</th>
                <th className="px-3 py-2">Model</th>
                <th className="px-3 py-2">Reasoning effort</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {models.map((model) => (
                <tr key={model.id} className={model.available ? '' : 'text-slate-400'}>
                  <td className="px-3 py-2">
                    <button
                      type="button"
                      onClick={() => setFavorite.mutate({ modelId: model.id, favorite: !model.is_favorite })}
                      aria-label={model.is_favorite ? 'Remove from favorites' : 'Add to favorites'}
                      aria-pressed={model.is_favorite}
                      className={`text-lg leading-none ${model.is_favorite ? 'text-amber-500' : 'text-slate-300 hover:text-slate-500'}`}
                    >
                      {model.is_favorite ? '★' : '☆'}
                    </button>
                  </td>
                  <td className="px-3 py-2">{model.provider}</td>
                  <td className="px-3 py-2">
                    <span className="font-mono">{model.model}</span>
                    {!model.available && <div className="text-xs text-amber-700">Unavailable: {model.unavailable_reason}</div>}
                  </td>
                  <td className="px-3 py-2">
                    <ReasoningEffortSelect
                      value={model.reasoning_effort ?? null}
                      disabled={updateModel.isPending}
                      onChange={(reasoningEffort) => updateModel.mutate({ modelId: model.id, reasoningEffort })}
                      className="text-xs"
                    />
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      {model.id === activeModel?.id ? (
                        <span className="rounded-full bg-slate-900 px-2 py-0.5 text-xs font-medium text-white">active</span>
                      ) : (
                        model.available && (
                          <button
                            type="button"
                            onClick={() => setActiveModelId(model.id)}
                            className="text-xs text-slate-600 underline"
                          >
                            Use
                          </button>
                        )
                      )}
                      {model.is_default ? (
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">default</span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setDefault.mutate(model.id)}
                          className="text-xs text-slate-600 underline"
                        >
                          Make default
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => {
                          if (confirm(`Delete ${modelLabel(model)}?`)) deleteModel.mutate(model.id)
                        }}
                        className="text-xs text-red-600 underline"
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {deleteModel.isError && <p className="text-sm text-red-600">{errorDetail(deleteModel.error, 'Delete failed.')}</p>}
      {updateModel.isError && <p className="text-sm text-red-600">{errorDetail(updateModel.error, 'Update failed.')}</p>}
    </div>
  )
}

function AddModelForm() {
  const { data: providers } = useLlmProviders()
  const createModel = useCreateLlmModel()
  const available = providers?.filter((p) => p.available) ?? []
  const unavailable = providers?.filter((p) => !p.available) ?? []

  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort | null>(null)
  const [isDefault, setIsDefault] = useState(false)
  const [isFavorite, setIsFavorite] = useState(false)
  // Falls back to the first usable provider until one is picked explicitly.
  const selectedProvider = available.some((p) => p.name === provider) ? provider : (available[0]?.name ?? '')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    createModel.mutate(
      { provider: selectedProvider, model, reasoning_effort: reasoningEffort, is_default: isDefault, is_favorite: isFavorite },
      {
        onSuccess: () => {
          setModel('')
          setReasoningEffort(null)
          setIsDefault(false)
          setIsFavorite(false)
        },
      },
    )
  }

  if (providers && available.length === 0) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        No LLM provider is configured on the server. Install a provider extra and set its key in the environment:
        <ul className="mt-2 list-disc pl-5">
          {unavailable.map((p) => (
            <li key={p.name}>
              <strong>{p.name}</strong>: {p.unavailable_reason}
            </li>
          ))}
        </ul>
      </div>
    )
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4">
      <div>
        <label className="block text-xs font-medium text-slate-600">Provider</label>
        <select
          value={selectedProvider}
          onChange={(e) => setProvider(e.target.value)}
          className="mt-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
        >
          {available.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <div className="min-w-48 flex-1">
        <label className="block text-xs font-medium text-slate-600">Model identifier</label>
        <input
          required
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder={MODEL_PLACEHOLDERS[selectedProvider] ?? ''}
          className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 font-mono text-sm"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-slate-600">Reasoning effort</label>
        <ReasoningEffortSelect value={reasoningEffort} onChange={setReasoningEffort} className="mt-1" />
      </div>
      <label className="flex items-center gap-1.5 text-sm text-slate-600">
        <input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />
        Default
      </label>
      <label className="flex items-center gap-1.5 text-sm text-slate-600">
        <input type="checkbox" checked={isFavorite} onChange={(e) => setIsFavorite(e.target.checked)} />
        Favorite
      </label>
      <button
        type="submit"
        disabled={createModel.isPending || !selectedProvider}
        className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
      >
        Add model
      </button>
      {createModel.isError && (
        <p className="w-full text-sm text-red-600">{errorDetail(createModel.error, 'Failed to add model.')}</p>
      )}
      {unavailable.length > 0 && (
        <details className="w-full text-xs text-slate-500">
          <summary className="cursor-pointer">Why isn't a provider listed?</summary>
          <ul className="mt-1 list-disc pl-5">
            {unavailable.map((p) => (
              <li key={p.name}>
                {p.name}: {p.unavailable_reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </form>
  )
}

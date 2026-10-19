import { createContext, useContext } from 'react'
import type { LlmModel } from '../api/types'

/**
 * The "active model": which registered LLM model this browser grades with.
 * A per-viewer choice (kept in localStorage by `ActiveModelProvider`), unlike
 * the backend-stored default it falls back to -- picking a model here never
 * changes what anyone else grades with.
 */
export interface ActiveModelState {
  models?: LlmModel[]
  activeModel?: LlmModel
  setActiveModelId: (id: string | null) => void
}

export const ActiveModelContext = createContext<ActiveModelState>({ setActiveModelId: () => {} })

export function useActiveModel(): ActiveModelState {
  return useContext(ActiveModelContext)
}

/** What every grading-triggering hook sends as `?model_id=`. `undefined`
 * (models not loaded yet, or none usable) lets the backend fall back to its
 * default, or reject the request if there is none. */
export function useActiveModelId(): string | undefined {
  return useActiveModel().activeModel?.id
}

/** The stored choice if it still exists and is usable, else the default. A
 * stale choice (model deleted, provider key removed) silently falls back
 * rather than making every grading request fail -- the header always shows
 * which model is actually active. */
export function resolveActiveModel(models: LlmModel[] | undefined, storedId: string | null): LlmModel | undefined {
  if (!models) return undefined
  const stored = models.find((m) => m.id === storedId && m.available)
  return stored ?? models.find((m) => m.is_default && m.available)
}

export function modelLabel(model: Pick<LlmModel, 'provider' | 'model' | 'reasoning_effort'>): string {
  const effort = model.reasoning_effort ? ` (${model.reasoning_effort})` : ''
  return `${model.provider} · ${model.model}${effort}`
}

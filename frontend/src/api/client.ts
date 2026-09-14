import createClient from 'openapi-fetch'
import type { paths } from './schema'

export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export const client = createClient<paths>({ baseUrl: API_URL })

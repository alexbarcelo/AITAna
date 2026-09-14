# AITAna frontend

React + TypeScript + Vite, talking to the FastAPI backend in `../src/aitana`.

- **TanStack Query** for server state (`src/api/hooks.ts`), polling a submission's
  status while grading is in progress.
- **React Router** for pages (`src/pages/`).
- **Tailwind v4** for styling.
- **openapi-fetch** for a typed API client (`src/api/client.ts`), using types
  generated from the backend's OpenAPI schema (`src/api/schema.ts`).

## Setup

```bash
npm install
cp .env.example .env   # VITE_API_URL, defaults to http://localhost:8000
npm run dev
```

## Regenerating API types

Whenever backend routes/schemas change, regenerate `src/api/schema.ts` from
the live OpenAPI schema (no need to run a server -- FastAPI can build the
schema from the app object directly):

```bash
cd ..
uv run python -c "import json; from aitana.api.main import app; print(json.dumps(app.openapi()))" > /tmp/openapi.json
cd frontend
npx openapi-typescript@latest /tmp/openapi.json -o src/api/schema.ts
```

(`openapi-typescript` is intentionally not a `package.json` dependency --
running it via `npx` avoids a peer-dependency conflict with this project's
TypeScript version. It's a one-shot codegen step, never imported at runtime.)

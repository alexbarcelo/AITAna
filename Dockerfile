FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:/usr/local/bin:$PATH" \
    DENO_INSTALL=/usr/local

# Deno, not a Python package: it runs the sandboxed Python-execution tool a
# rubric question can opt into (Question.needs_python_sandbox). See
# src/aitana/grading/sandbox.py's module docstring for why this is a Deno
# subprocess (running Pyodide -- CPython compiled to WASM -- inside Deno's
# permission sandbox) rather than a `langchain-sandbox` pip dependency: that
# package pins an incompatible old langchain-core. Both the api (the
# POST /rubrics/{id}/test-answer endpoint) and worker (real grading) images
# come from this same Dockerfile, so both get Deno regardless of whether a
# given deployment's rubrics actually use the feature.
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates \
    && curl -fsSL https://deno.land/install.sh | sh -s -- --no-modify-path \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project

COPY . .
RUN uv sync --locked

# Best-effort cache warmup: pre-fetches the sandbox's JSR/npm dependencies
# and Pyodide's own bootstrap wheels (micropip, packaging) into ./node_modules
# so the *first real* grading request doesn't stall on a cold network fetch.
# `|| true` because, like `uv sync` above, this needs outbound network at
# build time -- a build without it should still succeed, just with that
# fetch cost deferred to the first grading run instead.
RUN deno run --allow-env --allow-read=node_modules --allow-write=node_modules \
    --allow-net=cdn.jsdelivr.net --node-modules-dir=auto \
    jsr:@langchain/pyodide-sandbox@0.0.4 -c "1" || true

EXPOSE 8000

CMD ["uvicorn", "aitana.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

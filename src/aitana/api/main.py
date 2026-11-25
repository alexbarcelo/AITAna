import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..db.session import dispose_engine, get_session, init_engine
from ..storage import ensure_bucket
from .routers import batches, courses, editions, grading_traces, llm, rubrics, students, submissions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The schema itself is managed by Alembic (`alembic upgrade head`, run
    # before the API starts -- see AGENTS.md "Database migrations").
    init_engine()
    async for session in get_session():
        await llm.seed_llm_models_from_env(session)
    ensure_bucket()
    yield
    await dispose_engine()


app = FastAPI(title="AITAna", lifespan=lifespan)

# Local dev: the Vite frontend runs on a different origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(students.router)
app.include_router(courses.router)
app.include_router(editions.router)
app.include_router(rubrics.router)
app.include_router(submissions.router)
app.include_router(batches.router)
app.include_router(grading_traces.router)
app.include_router(llm.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

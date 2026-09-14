import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..db import init_db
from ..storage import ensure_bucket
from .routers import courses, editions, rubrics, students, submissions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    ensure_bucket()
    yield


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


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

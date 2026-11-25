from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import Edition
from ...db.session import get_session
from ...slugify import slugify
from ..schemas import EditionOut

router = APIRouter(prefix="/editions", tags=["editions"])


class EditionCreate(BaseModel):
    name: str
    slug: str | None = None


@router.get("", response_model=list[EditionOut])
async def list_editions(session: AsyncSession = Depends(get_session)) -> list[Edition]:
    return list(await session.scalars(select(Edition).order_by(Edition.id)))


@router.post("", response_model=EditionOut, status_code=201)
async def create_edition(payload: EditionCreate, session: AsyncSession = Depends(get_session)) -> Edition:
    slug = payload.slug or slugify(payload.name)
    if await session.scalar(select(Edition).where(Edition.slug == slug)):
        raise HTTPException(status_code=409, detail=f"Edition {slug!r} already exists")
    edition = Edition(name=payload.name, slug=slug)
    session.add(edition)
    await session.commit()
    return edition


@router.get("/{edition_id}", response_model=EditionOut)
async def get_edition(edition_id: int, session: AsyncSession = Depends(get_session)) -> Edition:
    edition = await session.get(Edition, edition_id)
    if edition is None:
        raise HTTPException(status_code=404, detail="Edition not found")
    return edition

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...documents import Edition
from ...slugify import slugify

router = APIRouter(prefix="/editions", tags=["editions"])


class EditionCreate(BaseModel):
    name: str
    slug: str | None = None


@router.get("", response_model=list[Edition])
async def list_editions() -> list[Edition]:
    return await Edition.find_all().to_list()


@router.post("", response_model=Edition, status_code=201)
async def create_edition(payload: EditionCreate) -> Edition:
    slug = payload.slug or slugify(payload.name)
    if await Edition.find_one(Edition.slug == slug):
        raise HTTPException(status_code=409, detail=f"Edition {slug!r} already exists")

    edition = Edition(name=payload.name, slug=slug)
    await edition.insert()
    return edition


@router.get("/{edition_id}", response_model=Edition)
async def get_edition(edition_id: PydanticObjectId) -> Edition:
    edition = await Edition.get(edition_id)
    if edition is None:
        raise HTTPException(status_code=404, detail="Edition not found")
    return edition

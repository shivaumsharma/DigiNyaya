"""Read-only matter registry endpoints: what the platform covers and how far the AI goes on each matter."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..matters import get_matter, guides, list_matters, to_public

router = APIRouter(prefix="/api/matters", tags=["matters"])


@router.get("")
def matters(lang: str | None = None):
    return [to_public(m, lang) for m in list_matters()]


@router.get("/{matter_id}/guide")
def matter_guide(matter_id: str, lang: str | None = None):
    m = get_matter(matter_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Unknown matter")
    if m.guide is None:
        raise HTTPException(status_code=404, detail="This matter is handled by the filing flow, not a guide")
    body = guides.localize(m.id, m.guide, lang)
    return {"id": m.id, "level_name": m.level.name, "label": m.label, **body}

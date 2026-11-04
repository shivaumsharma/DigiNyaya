"""Read-only matter registry endpoints: what the platform covers and how far the AI goes on each matter."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..matters import get_matter, list_matters, to_public

router = APIRouter(prefix="/api/matters", tags=["matters"])


@router.get("")
def matters():
    return [to_public(m) for m in list_matters()]


@router.get("/{matter_id}/guide")
def matter_guide(matter_id: str):
    m = get_matter(matter_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Unknown matter")
    if m.guide is None:
        raise HTTPException(status_code=404, detail="This matter is handled by the filing flow, not a guide")
    return {"id": m.id, "label": m.label, "level_name": m.level.name, **m.guide}

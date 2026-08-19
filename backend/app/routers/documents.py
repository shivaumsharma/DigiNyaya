"""Document upload, extraction status, and discrepancy-check endpoints.

New APIRouter rather than more code in app.main -- main.py is 456 lines
covering a different concern (case lifecycle/mediation/SSE); this feature is
already architecturally decoupled from that pipeline (its own DB tables, its
own job phases), making it the lowest-risk place for this project's first
router split, since mounting it is purely additive (one import + one
`include_router` line in main.py).

The ownership check (`_load_owned`) is duplicated here (~5 lines) rather
than imported from app.main, to avoid main.py <-> this module becoming a
circular import (main.py will import and mount this router).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import db, jobs
from ..agents.preliminary_review import run_preliminary_review
from ..auth.db import get_db
from ..auth.deps import current_user
from ..auth.orm_models import User
from ..auth.rate_limit import enforce_call_limit
from ..documents.validation import is_audio_mime_type, validate_upload
from ..models import DiscrepancyOut, DocumentDetailOut, DocumentOut, PreliminaryReviewOut
from ..security import ensure_owner
from ..storage import get_storage
from ..storage.config import config as storage_config

# See auth.rate_limit.enforce_call_limit's docstring -- re-runnable by
# design ("any number of times before filing"), so this stays generous
# relative to real usage; it exists to bound a scripted client, not to
# police normal iteration on evidence.
_PRELIM_REVIEW_LIMIT, _PRELIM_REVIEW_WINDOW = 30, timedelta(minutes=10)

router = APIRouter(prefix="/api/cases/{case_id}", tags=["documents"])

_MAX_UPLOAD_BYTES = storage_config.max_upload_mb * 1024 * 1024
# Each document costs at least one LLM relevance-check call in BOTH the
# pre-filing preliminary review and the real pipeline's ingestion agent (see
# app.agents.preliminary_review.document_relevance) -- with no cap, a case
# with dozens of files means dozens of sequential LLM calls just to ingest
# it, an unbounded cost/latency vector found during the production-readiness
# review (2026-08-06). 15 is generous for any real dispute's actual evidence.
_MAX_EVIDENCE_PER_CASE = 15


def _load_owned(case_id: str, owner_id: str) -> dict:
    case = db.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    ensure_owner(case, owner_id)
    return case


def _evidence_kind(mime_type: str) -> str:
    if mime_type == "application/pdf":
        return "document"
    if is_audio_mime_type(mime_type):
        return "audio"
    return "photo"


@router.post("/documents", response_model=list[DocumentOut])
async def upload_documents(
    case_id: str,
    files: list[UploadFile] = File(...),
    user: User = Depends(current_user),
):
    case = _load_owned(case_id, user.id)
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    existing_count = len(db.list_documents(case_id))
    if existing_count + len(files) > _MAX_EVIDENCE_PER_CASE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This case already has {existing_count} evidence file(s); the maximum is "
                f"{_MAX_EVIDENCE_PER_CASE} per case."
            ),
        )

    storage = get_storage()
    created: list[dict] = []
    evidence = list(case.get("evidence", []))

    for upload in files:
        raw = await upload.read()
        result = validate_upload(upload.filename or "upload", raw, upload.content_type, _MAX_UPLOAD_BYTES)
        if not result.ok:
            status_code = 413 if "size limit" in (result.error or "") else 400
            raise HTTPException(status_code=status_code, detail=f"{upload.filename}: {result.error}")

        document_id = f"DOC-{uuid.uuid4().hex[:12]}"
        storage_path = storage.save(case_id, upload.filename or "upload", raw)
        doc = {
            "id": document_id,
            "case_id": case_id,
            "original_filename": upload.filename,
            "storage_path": storage_path,
            "mime_type": result.mime_type,
            "file_size": len(raw),
            "extraction_status": "pending",
        }
        db.insert_document(doc)
        evidence.append({"filename": upload.filename, "kind": _evidence_kind(result.mime_type), "note": upload.filename})
        created.append(db.get_document(document_id))

    # Evidence is appended once per upload batch, matching the field's
    # existing pure-declared-metadata semantics elsewhere in the codebase --
    # never re-appended as extraction progresses.
    db.update_case(case_id, evidence=evidence)

    for doc in created:
        jobs.start_extraction(doc["id"])

    return created


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(case_id: str, user: User = Depends(current_user)):
    _load_owned(case_id, user.id)
    return db.list_documents(case_id)


@router.get("/documents/{document_id}", response_model=DocumentDetailOut)
def get_document(case_id: str, document_id: str, user: User = Depends(current_user)):
    _load_owned(case_id, user.id)
    doc = db.get_document(document_id)
    if doc is None or doc["case_id"] != case_id:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.post("/preliminary-review", response_model=PreliminaryReviewOut)
def preliminary_review(case_id: str, user: User = Depends(current_user), auth_db: Session = Depends(get_db)):
    """Advisory-only check on a draft case's evidence -- not the real
    adjudication (that's the 5-agent pipeline, which only runs after the
    respondent replies). Re-runnable any number of times before filing.
    """
    _load_owned(case_id, user.id)
    enforce_call_limit(auth_db, user.id, "preliminary_review", limit=_PRELIM_REVIEW_LIMIT, window=_PRELIM_REVIEW_WINDOW)
    return run_preliminary_review(case_id)


@router.post("/discrepancy-check")
def trigger_discrepancy_check(case_id: str, user: User = Depends(current_user)):
    _load_owned(case_id, user.id)
    docs = db.list_documents(case_id)
    if not docs:
        raise HTTPException(status_code=400, detail="No documents uploaded for this case yet")
    started = jobs.start_discrepancy_check(case_id)
    return {"started": started, "running": jobs.is_discrepancy_check_running(case_id)}


@router.get("/discrepancies", response_model=list[DiscrepancyOut])
def list_discrepancies(case_id: str, user: User = Depends(current_user)):
    _load_owned(case_id, user.id)
    return db.list_discrepancies(case_id)

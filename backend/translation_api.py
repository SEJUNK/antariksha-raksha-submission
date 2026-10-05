"""FastAPI router for optional LOCAL brief translation.

Include from main.py with:
    from backend.translation_api import router as translation_router
    app.include_router(translation_router)

Read-only with respect to the database: an event's English brief is read via
get_event_with_brief, translated in memory by the local service, and returned.
Translated text is NEVER written to the DB or the audit trail -- English is
the source of record. No external translation API is ever called.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import translation

router = APIRouter(prefix="/api/translation", tags=["translation"])


class BriefTranslationRequest(BaseModel):
    target_language: str
    event_id: int | None = None
    brief_text: str | None = None
    maneuver_text: str | None = None


@router.get("/status")
def translation_status():
    st = translation.get_translation_service().status()
    return {
        **st,
        "supported_languages": list(translation.SUPPORTED_TARGETS),
        "source_of_record": "en",
        "persisted": False,
    }


@router.post("/brief")
def translate_brief(body: BriefTranslationRequest):
    target = (body.target_language or "").strip().lower()
    if target != "en" and target not in translation.SUPPORTED_TARGETS:
        raise HTTPException(status_code=400, detail=f"Unsupported target_language: {body.target_language}")

    if body.event_id is not None:
        from backend.db import get_event_with_brief  # read-only

        event = get_event_with_brief(body.event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")
        brief_text = event.get("brief_text")
        maneuver_text = event.get("maneuver_text")
        # Full object names are masked as whole units so no part of a catalog
        # name (e.g. "FENGYUN 1C DEB") is ever sent through the model.
        from backend.db import list_objects
        names = {o["norad_id"]: o["name"] for o in list_objects(include_inactive=True)}
        protected_terms = [names.get(event["object_a_id"]), names.get(event["object_b_id"]),
                           event["object_a_id"], event["object_b_id"]]
    else:
        brief_text = body.brief_text
        maneuver_text = body.maneuver_text
        if not brief_text and not maneuver_text:
            raise HTTPException(status_code=400, detail="Provide event_id or brief_text/maneuver_text")
        protected_terms = []

    service = translation.get_translation_service()
    result = service.translate_brief(brief_text, maneuver_text, target,
                                     protected_terms=[t for t in protected_terms if t])
    translated = result["status"] == "translated"
    texts = result["texts"] if translated else result["english"]
    return {
        "event_id": body.event_id,
        "source_language": "en",
        "target_language": target,
        "status": result["status"],          # translated | source_language | no_text | unavailable | failed | unsupported
        "translated": translated,
        "reason": result["reason"],
        "engine": service.engine if translated else None,
        "machine_translation": translated,
        "brief_text": texts.get("brief_text"),
        "maneuver_text": texts.get("maneuver_text"),
        "english": result["english"],
        "source_of_record": "en",
        "persisted": False,
    }

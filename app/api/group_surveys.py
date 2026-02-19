from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.session import get_db
from app.db import models

router = APIRouter(prefix="/groups", tags=["group_surveys"])


@router.get("/{group_id}/surveys")
def list_group_surveys(group_id: int, db: Session = Depends(get_db)):
    group = db.get(models.Group, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    rows = db.execute(
        select(models.Survey.id, models.Survey.title, models.Survey.description)
        .join(models.GroupSurvey, models.GroupSurvey.survey_id == models.Survey.id)
        .where(models.GroupSurvey.group_id == group_id)
        .order_by(models.Survey.id.desc())
    ).all()

    return {
        "group_id": group_id,
        "surveys": [{"id": r.id, "title": r.title, "description": r.description} for r in rows],
    }


@router.post("/{group_id}/surveys/{survey_id}")
def attach_survey(group_id: int, survey_id: int, db: Session = Depends(get_db)):
    group = db.get(models.Group, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    survey = db.get(models.Survey, survey_id)
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")

    # already attached?
    exists = db.execute(
        select(models.GroupSurvey.id)
        .where(models.GroupSurvey.group_id == group_id, models.GroupSurvey.survey_id == survey_id)
    ).scalar_one_or_none()
    if exists:
        return {"ok": True, "already": True}

    db.add(models.GroupSurvey(group_id=group_id, survey_id=survey_id))
    db.commit()
    return {"ok": True}


@router.delete("/{group_id}/surveys/{survey_id}")
def detach_survey(group_id: int, survey_id: int, db: Session = Depends(get_db)):
    link = db.execute(
        select(models.GroupSurvey).where(
            models.GroupSurvey.group_id == group_id,
            models.GroupSurvey.survey_id == survey_id
        )
    ).scalar_one_or_none()

    if not link:
        raise HTTPException(status_code=404, detail="Not attached")

    db.delete(link)
    db.commit()
    return {"ok": True}

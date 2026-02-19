from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_session
from app.db import models

router = APIRouter(prefix="/surveys", tags=["surveys"])


class SurveyCreate(BaseModel):
    bot_id: int
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    created_by: int | None = None


class QuestionCreate(BaseModel):
    type: str = Field(default="single")  # single/text (MVP)
    text: str = Field(min_length=1, max_length=500)
    options: list[str] | None = None  # тільки для single


@router.post("")
async def create_survey(payload: SurveyCreate, session: AsyncSession = Depends(get_session)):
    survey = models.Survey(
        bot_id=payload.bot_id,
        title=payload.title.strip(),
        description=(payload.description.strip() if payload.description else None),
        created_by=payload.created_by,
    )
    session.add(survey)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise HTTPException(status_code=400, detail="Cannot create survey (maybe duplicate title?)")

    await session.refresh(survey)
    return {"id": survey.id, "bot_id": survey.bot_id, "title": survey.title, "description": survey.description}


@router.get("/{survey_id}")
async def get_survey(survey_id: int, session: AsyncSession = Depends(get_session)):
    res = await session.execute(select(models.Survey).where(models.Survey.id == survey_id))
    survey = res.scalar_one_or_none()
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")
    return {"id": survey.id, "bot_id": survey.bot_id, "title": survey.title, "description": survey.description}


@router.delete("/{survey_id}")
async def delete_survey(survey_id: int, session: AsyncSession = Depends(get_session)):
    res = await session.execute(select(models.Survey).where(models.Survey.id == survey_id))
    survey = res.scalar_one_or_none()
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")

    await session.delete(survey)
    await session.commit()
    return {"ok": True}


@router.post("/{survey_id}/questions")
async def add_question(survey_id: int, payload: QuestionCreate, session: AsyncSession = Depends(get_session)):
    res = await session.execute(select(models.Survey).where(models.Survey.id == survey_id))
    survey = res.scalar_one_or_none()
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")

    qtype = payload.type.strip().lower()
    if qtype not in {"single", "text"}:
        raise HTTPException(status_code=400, detail="Only 'single' and 'text' supported in MVP")

    if qtype == "single":
        if not payload.options or len(payload.options) < 2:
            raise HTTPException(status_code=400, detail="Single question requires at least 2 options")

    # position = max + 1
    res_pos = await session.execute(
        select(models.SurveyQuestion.position)
        .where(models.SurveyQuestion.survey_id == survey_id)
        .order_by(models.SurveyQuestion.position.desc())
        .limit(1)
    )
    max_pos = res_pos.scalar_one_or_none()
    next_pos = (max_pos or 0) + 1

    question = models.SurveyQuestion(
        survey_id=survey_id,
        position=next_pos,
        type=qtype,
        text=payload.text.strip(),
    )
    session.add(question)
    await session.flush()  # щоб отримати question.id

    if qtype == "single":
        for i, opt in enumerate(payload.options, start=1):
            session.add(models.SurveyOption(
                question_id=question.id,
                position=i,
                text=opt.strip(),
            ))

    await session.commit()
    return {"ok": True, "question_id": question.id, "position": next_pos}


@router.delete("/questions/{question_id}")
async def delete_question(question_id: int, session: AsyncSession = Depends(get_session)):
    res = await session.execute(select(models.SurveyQuestion).where(models.SurveyQuestion.id == question_id))
    q = res.scalar_one_or_none()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    await session.delete(q)
    await session.commit()
    return {"ok": True}

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.db.session import get_session

router = APIRouter(prefix="/survey-sessions", tags=["survey_sessions"])


class StartPayload(BaseModel):
    survey_id: int
    group_id: int
    user_id: int


class AnswerPayload(BaseModel):
    question_id: int
    type: str  # "single" або "text"
    option_id: int | None = None
    text: str | None = None


@router.post("/start")
async def start(payload: StartPayload, session: AsyncSession = Depends(get_session)):
    # ✅ доступ тільки членам групи або creator
    g = await session.execute(select(models.Group).where(models.Group.id == payload.group_id))
    group = g.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    is_member = await session.execute(
        select(models.GroupMember.id).where(
            models.GroupMember.group_id == payload.group_id,
            models.GroupMember.user_id == payload.user_id,
        )
    )
    if not is_member.scalar_one_or_none() and group.created_by != payload.user_id:
        raise HTTPException(status_code=403, detail="Not a member of this group")

    # перевірка прив’язки survey до group
    link = await session.execute(
        select(models.GroupSurvey.id).where(
            models.GroupSurvey.group_id == payload.group_id,
            models.GroupSurvey.survey_id == payload.survey_id,
        )
    )
    if not link.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Survey not attached to group")

    # якщо є активна сесія — повертаємо її
    active = await session.execute(
        select(models.SurveySession).where(
            models.SurveySession.user_id == payload.user_id,
            models.SurveySession.group_id == payload.group_id,
            models.SurveySession.survey_id == payload.survey_id,
            models.SurveySession.status == "in_progress",
        )
    )
    s = active.scalar_one_or_none()
    if s:
        return {"session_id": s.id}

    s = models.SurveySession(
        survey_id=payload.survey_id,
        group_id=payload.group_id,
        user_id=payload.user_id,
        status="in_progress",
        current_pos=1,
    )
    session.add(s)
    await session.commit()
    await session.refresh(s)
    return {"session_id": s.id}


@router.get("/{session_id}/current")
async def current(session_id: int, session: AsyncSession = Depends(get_session)):
    res = await session.execute(select(models.SurveySession).where(models.SurveySession.id == session_id))
    s = res.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if s.status != "in_progress":
        return {"finished": True}

    q_res = await session.execute(
        select(models.SurveyQuestion).where(
            models.SurveyQuestion.survey_id == s.survey_id,
            models.SurveyQuestion.position == s.current_pos,
        )
    )
    q = q_res.scalar_one_or_none()
    if not q:
        # питань більше нема — фініш
        s.status = "finished"
        s.finished_at = datetime.utcnow()
        await session.commit()
        return {"finished": True}

    payload = {
        "finished": False,
        "question": {"id": q.id, "type": q.type, "text": q.text, "position": q.position},
    }

    if (q.type or "").lower() == "single":
        opts = await session.execute(
            select(models.SurveyOption)
            .where(models.SurveyOption.question_id == q.id)
            .order_by(models.SurveyOption.position)
        )
        payload["question"]["options"] = [{"id": o.id, "text": o.text} for o in opts.scalars().all()]

    return payload


@router.post("/{session_id}/answer")
async def answer(session_id: int, payload: AnswerPayload, session: AsyncSession = Depends(get_session)):
    res = await session.execute(select(models.SurveySession).where(models.SurveySession.id == session_id))
    s = res.scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if s.status != "in_progress":
        raise HTTPException(status_code=400, detail="Session finished")

    q_res = await session.execute(select(models.SurveyQuestion).where(models.SurveyQuestion.id == payload.question_id))
    q = q_res.scalar_one_or_none()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    qtype = (payload.type or "").lower().strip()

    if qtype == "single":
        if not payload.option_id:
            raise HTTPException(status_code=400, detail="option_id required")

        # чистимо попередні вибори для цього питання в цій сесії
        prev = await session.execute(
            select(models.SurveyAnswerOption).where(
                models.SurveyAnswerOption.session_id == session_id,
                models.SurveyAnswerOption.question_id == q.id,
            )
        )
        for row in prev.scalars().all():
            await session.delete(row)

        session.add(models.SurveyAnswerOption(session_id=session_id, question_id=q.id, option_id=payload.option_id))

    elif qtype == "text":
        prev = await session.execute(
            select(models.SurveyAnswer).where(
                models.SurveyAnswer.session_id == session_id,
                models.SurveyAnswer.question_id == q.id,
            )
        )
        a = prev.scalar_one_or_none()
        if a:
            a.answer_text = (payload.text or "").strip()
        else:
            session.add(
                models.SurveyAnswer(
                    session_id=session_id,
                    question_id=q.id,
                    answer_text=(payload.text or "").strip(),
                )
            )
    else:
        raise HTTPException(status_code=400, detail="Only single/text supported")

    # рухаємось далі
    s.current_pos += 1

    # ✅ КЛЮЧОВЕ: одразу перевіряємо, чи є наступне питання
    next_q = await session.execute(
        select(models.SurveyQuestion).where(
            models.SurveyQuestion.survey_id == s.survey_id,
            models.SurveyQuestion.position == s.current_pos,
        )
    )
    if not next_q.scalar_one_or_none():
        s.status = "finished"
        s.finished_at = datetime.utcnow()

    await session.commit()
    return {"ok": True}


@router.get("/status")
async def status(survey_id: int, group_id: int, user_id: int, session: AsyncSession = Depends(get_session)):
    res = await session.execute(
        select(models.SurveySession.id)
        .where(
            models.SurveySession.survey_id == survey_id,
            models.SurveySession.group_id == group_id,
            models.SurveySession.user_id == user_id,
            models.SurveySession.status == "finished",
        )
        .order_by(models.SurveySession.id.desc())
        .limit(1)
    )
    return {"completed": bool(res.scalar_one_or_none())}

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security import require_api_access
from app.db import models
from app.db.session import get_session

router = APIRouter(prefix="/survey-sessions", tags=["survey_sessions"], dependencies=[Depends(require_api_access)])


class StartPayload(BaseModel):
    survey_id: int
    group_id: int
    user_id: int


class AnswerPayload(BaseModel):
    question_id: int
    type: str  # "single" or "text"
    option_id: int | None = None
    text: str | None = None


async def is_bot_admin(session: AsyncSession, bot_id: int, user_id: int) -> bool:
    result = await session.execute(
        select(models.BotUser.id).where(
            models.BotUser.bot_id == bot_id,
            models.BotUser.user_id == user_id,
            models.BotUser.role == "admin",
            models.BotUser.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none() is not None


@router.post("/start")
async def start(payload: StartPayload, session: AsyncSession = Depends(get_session)):
    group_result = await session.execute(select(models.Group).where(models.Group.id == payload.group_id))
    group = group_result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    is_member = await session.execute(
        select(models.GroupMember.id).where(
            models.GroupMember.group_id == payload.group_id,
            models.GroupMember.user_id == payload.user_id,
        )
    )
    if (
        not is_member.scalar_one_or_none()
        and group.created_by != payload.user_id
        and not await is_bot_admin(session, group.bot_id, payload.user_id)
    ):
        raise HTTPException(status_code=403, detail="Not a member of this group")

    link = await session.execute(
        select(models.GroupSurvey.id).where(
            models.GroupSurvey.group_id == payload.group_id,
            models.GroupSurvey.survey_id == payload.survey_id,
        )
    )
    if not link.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Survey not attached to group")

    active = await session.execute(
        select(models.SurveySession).where(
            models.SurveySession.user_id == payload.user_id,
            models.SurveySession.group_id == payload.group_id,
            models.SurveySession.survey_id == payload.survey_id,
            models.SurveySession.status == "in_progress",
        )
    )
    survey_session = active.scalar_one_or_none()
    if survey_session:
        return {"session_id": survey_session.id}

    survey_session = models.SurveySession(
        survey_id=payload.survey_id,
        group_id=payload.group_id,
        user_id=payload.user_id,
        status="in_progress",
        current_pos=1,
    )
    session.add(survey_session)
    await session.commit()
    await session.refresh(survey_session)
    return {"session_id": survey_session.id}


@router.get("/{session_id}/current")
async def current(session_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(models.SurveySession).where(models.SurveySession.id == session_id))
    survey_session = result.scalar_one_or_none()
    if not survey_session:
        raise HTTPException(status_code=404, detail="Session not found")
    if survey_session.status != "in_progress":
        return {"finished": True}

    question_result = await session.execute(
        select(models.SurveyQuestion).where(
            models.SurveyQuestion.survey_id == survey_session.survey_id,
            models.SurveyQuestion.position == survey_session.current_pos,
        )
    )
    question = question_result.scalar_one_or_none()
    if not question:
        survey_session.status = "finished"
        survey_session.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return {"finished": True}

    payload = {
        "finished": False,
        "question": {
            "id": question.id,
            "type": question.type,
            "text": question.text,
            "position": question.position,
        },
    }

    if (question.type or "").lower() == "single":
        options_result = await session.execute(
            select(models.SurveyOption)
            .where(models.SurveyOption.question_id == question.id)
            .order_by(models.SurveyOption.position)
        )
        payload["question"]["options"] = [
            {"id": option.id, "text": option.text}
            for option in options_result.scalars().all()
        ]

    return payload


@router.post("/{session_id}/answer")
async def answer(session_id: int, payload: AnswerPayload, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(models.SurveySession).where(models.SurveySession.id == session_id))
    survey_session = result.scalar_one_or_none()
    if not survey_session:
        raise HTTPException(status_code=404, detail="Session not found")
    if survey_session.status != "in_progress":
        raise HTTPException(status_code=400, detail="Session finished")

    question_result = await session.execute(
        select(models.SurveyQuestion).where(
            models.SurveyQuestion.survey_id == survey_session.survey_id,
            models.SurveyQuestion.position == survey_session.current_pos,
        )
    )
    question = question_result.scalar_one_or_none()
    if not question:
        survey_session.status = "finished"
        survey_session.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return {"ok": True, "finished": True}
    if question.id != payload.question_id:
        raise HTTPException(status_code=400, detail="Question does not match current session position")

    question_type = (payload.type or "").lower().strip()
    if question_type != (question.type or "").lower().strip():
        raise HTTPException(status_code=400, detail="Question type mismatch")

    if question_type == "single":
        if not payload.option_id:
            raise HTTPException(status_code=400, detail="option_id required")

        option_result = await session.execute(
            select(models.SurveyOption).where(
                models.SurveyOption.id == payload.option_id,
                models.SurveyOption.question_id == question.id,
            )
        )
        if not option_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Option does not belong to the question")

        previous_options = await session.execute(
            select(models.SurveyAnswerOption).where(
                models.SurveyAnswerOption.session_id == session_id,
                models.SurveyAnswerOption.question_id == question.id,
            )
        )
        for answer_option in previous_options.scalars().all():
            await session.delete(answer_option)

        session.add(
            models.SurveyAnswerOption(
                session_id=session_id,
                question_id=question.id,
                option_id=payload.option_id,
            )
        )
    elif question_type == "text":
        answer_text = (payload.text or "").strip()
        if not answer_text:
            raise HTTPException(status_code=400, detail="Text answer cannot be empty")

        previous_answer = await session.execute(
            select(models.SurveyAnswer).where(
                models.SurveyAnswer.session_id == session_id,
                models.SurveyAnswer.question_id == question.id,
            )
        )
        answer_row = previous_answer.scalar_one_or_none()
        if answer_row:
            answer_row.answer_text = answer_text
        else:
            session.add(
                models.SurveyAnswer(
                    session_id=session_id,
                    question_id=question.id,
                    answer_text=answer_text,
                )
            )
    else:
        raise HTTPException(status_code=400, detail="Only single/text supported")

    survey_session.current_pos += 1

    next_question = await session.execute(
        select(models.SurveyQuestion).where(
            models.SurveyQuestion.survey_id == survey_session.survey_id,
            models.SurveyQuestion.position == survey_session.current_pos,
        )
    )
    if not next_question.scalar_one_or_none():
        survey_session.status = "finished"
        survey_session.finished_at = datetime.now(timezone.utc)

    await session.commit()
    return {"ok": True}


@router.get("/status")
async def status(survey_id: int, group_id: int, user_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
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
    return {"completed": bool(result.scalar_one_or_none())}

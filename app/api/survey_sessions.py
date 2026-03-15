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
    type: str  # "single" or "text"
    option_id: int | None = None
    text: str | None = None


@router.post("/start")
async def start(payload: StartPayload, session: AsyncSession = Depends(get_session)):
    # Access is allowed only for group members or the group creator.
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
    if not is_member.scalar_one_or_none() and group.created_by != payload.user_id:
        raise HTTPException(status_code=403, detail="Not a member of this group")

    # Verify that the survey is attached to the group.
    link = await session.execute(
        select(models.GroupSurvey.id).where(
            models.GroupSurvey.group_id == payload.group_id,
            models.GroupSurvey.survey_id == payload.survey_id,
        )
    )
    if not link.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Survey not attached to group")

    # Reuse an existing in-progress session if it already exists.
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
        # There are no questions left, so the session is finished.
        survey_session.status = "finished"
        survey_session.finished_at = datetime.utcnow()
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

    question_result = await session.execute(select(models.SurveyQuestion).where(models.SurveyQuestion.id == payload.question_id))
    question = question_result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    question_type = (payload.type or "").lower().strip()

    if question_type == "single":
        if not payload.option_id:
            raise HTTPException(status_code=400, detail="option_id required")

        # Replace any previous choice for this question in the same session.
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
        previous_answer = await session.execute(
            select(models.SurveyAnswer).where(
                models.SurveyAnswer.session_id == session_id,
                models.SurveyAnswer.question_id == question.id,
            )
        )
        answer_row = previous_answer.scalar_one_or_none()
        if answer_row:
            answer_row.answer_text = (payload.text or "").strip()
        else:
            session.add(
                models.SurveyAnswer(
                    session_id=session_id,
                    question_id=question.id,
                    answer_text=(payload.text or "").strip(),
                )
            )
    else:
        raise HTTPException(status_code=400, detail="Only single/text supported")

    # Move to the next question.
    survey_session.current_pos += 1

    # Mark the session finished immediately if there is no next question.
    next_question = await session.execute(
        select(models.SurveyQuestion).where(
            models.SurveyQuestion.survey_id == survey_session.survey_id,
            models.SurveyQuestion.position == survey_session.current_pos,
        )
    )
    if not next_question.scalar_one_or_none():
        survey_session.status = "finished"
        survey_session.finished_at = datetime.utcnow()

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

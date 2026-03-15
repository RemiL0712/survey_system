from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.survey_answer_utils import build_answers_by_session
from app.db import models
from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/by-telegram")
async def by_telegram(telegram_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user_id": user.id}


@router.get("")
async def list_users(session: AsyncSession = Depends(get_session)):
    query = select(User).order_by(User.id.desc())

    result = await session.execute(query)
    users = result.scalars().all()

    return {
        "items": [
            {
                "id": user.id,
                "telegram_id": user.telegram_id,
                "username": user.username,
            }
            for user in users
        ]
    }


@router.get("/{user_id}/answers")
async def get_user_answers(user_id: int, session: AsyncSession = Depends(get_session)):
    user_result = await session.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    session_result = await session.execute(
        select(models.SurveySession, models.Survey)
        .join(models.Survey, models.Survey.id == models.SurveySession.survey_id)
        .where(models.SurveySession.user_id == user_id)
        .order_by(models.SurveySession.id.desc())
    )
    session_rows = session_result.all()

    session_ids_by_survey: dict[int, list[int]] = defaultdict(list)
    for survey_session, survey in session_rows:
        session_ids_by_survey[survey.id].append(survey_session.id)

    answer_map_by_session: dict[int, dict[str, object]] = {}
    for survey_id, session_ids in session_ids_by_survey.items():
        answer_map_by_session.update(
            await build_answers_by_session(session, survey_id, session_ids)
        )

    return {
        "user": {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
        },
        "items": [
            {
                "session_id": survey_session.id,
                "group_id": survey_session.group_id,
                "survey": {
                    "id": survey.id,
                    "title": survey.title,
                    "description": survey.description,
                },
                "status": survey_session.status,
                "started_at": survey_session.started_at,
                "finished_at": survey_session.finished_at,
                "answers": answer_map_by_session.get(survey_session.id, {}),
            }
            for survey_session, survey in session_rows
        ],
    }


@router.get("/{user_id}")
async def get_user(user_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
    }

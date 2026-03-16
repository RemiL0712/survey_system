import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security import require_api_access
from app.api.survey_answer_utils import (
    build_answers_by_session,
    flatten_answers_for_csv,
    get_survey_questions,
    question_column_name,
)
from app.db import models
from app.db.session import get_session

router = APIRouter(prefix="/surveys", tags=["surveys"], dependencies=[Depends(require_api_access)])


class SurveyCreate(BaseModel):
    bot_id: int
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    created_by: int | None = None


class SurveyUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)


class QuestionCreate(BaseModel):
    type: str = Field(default="single")  # single/text (MVP)
    text: str = Field(min_length=1, max_length=500)
    options: list[str] | None = None  # Only for single choice questions.


class QuestionUpdate(BaseModel):
    type: str = Field(default="single")
    text: str = Field(min_length=1, max_length=500)
    options: list[str] | None = None


async def get_survey_or_404(session: AsyncSession, survey_id: int) -> models.Survey:
    result = await session.execute(select(models.Survey).where(models.Survey.id == survey_id))
    survey = result.scalar_one_or_none()
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found")
    return survey


async def get_question_or_404(session: AsyncSession, question_id: int) -> models.SurveyQuestion:
    result = await session.execute(select(models.SurveyQuestion).where(models.SurveyQuestion.id == question_id))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    return question


async def get_question_options(session: AsyncSession, question_ids: list[int]) -> dict[int, list[models.SurveyOption]]:
    if not question_ids:
        return {}

    result = await session.execute(
        select(models.SurveyOption)
        .where(models.SurveyOption.question_id.in_(question_ids))
        .order_by(models.SurveyOption.question_id, models.SurveyOption.position)
    )
    options_map: dict[int, list[models.SurveyOption]] = {}
    for option in result.scalars().all():
        options_map.setdefault(option.question_id, []).append(option)
    return options_map


async def question_has_answers(session: AsyncSession, question_id: int) -> bool:
    text_result = await session.execute(
        select(models.SurveyAnswer.id)
        .where(models.SurveyAnswer.question_id == question_id)
        .limit(1)
    )
    if text_result.scalar_one_or_none():
        return True

    option_result = await session.execute(
        select(models.SurveyAnswerOption.id)
        .where(models.SurveyAnswerOption.question_id == question_id)
        .limit(1)
    )
    return option_result.scalar_one_or_none() is not None


async def survey_has_sessions(session: AsyncSession, survey_id: int) -> bool:
    result = await session.execute(
        select(models.SurveySession.id)
        .where(models.SurveySession.survey_id == survey_id)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def survey_has_in_progress_sessions(session: AsyncSession, survey_id: int) -> bool:
    result = await session.execute(
        select(models.SurveySession.id)
        .where(
            models.SurveySession.survey_id == survey_id,
            models.SurveySession.status == "in_progress",
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def normalize_options(options: list[str] | None) -> list[str]:
    return [option.strip() for option in (options or []) if option and option.strip()]


def serialize_question(
    question: models.SurveyQuestion,
    options_map: dict[int, list[models.SurveyOption]] | None = None,
) -> dict[str, object]:
    options_map = options_map or {}
    return {
        "id": question.id,
        "survey_id": question.survey_id,
        "position": question.position,
        "type": question.type,
        "text": question.text,
        "options": [
            {"id": option.id, "position": option.position, "text": option.text}
            for option in options_map.get(question.id, [])
        ],
    }


async def compact_question_positions(session: AsyncSession, survey_id: int) -> None:
    questions = await get_survey_questions(session, survey_id)
    for index, question in enumerate(questions, start=1):
        question.position = index


@router.get("")
async def list_surveys(bot_id: int, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(models.Survey)
        .where(models.Survey.bot_id == bot_id)
        .order_by(models.Survey.id.desc())
    )
    surveys = result.scalars().all()
    survey_ids = [survey.id for survey in surveys]

    question_counts: dict[int, int] = {}
    session_counts: dict[int, int] = {}
    finished_counts: dict[int, int] = {}

    if survey_ids:
        question_result = await session.execute(
            select(models.SurveyQuestion.survey_id, func.count(models.SurveyQuestion.id))
            .where(models.SurveyQuestion.survey_id.in_(survey_ids))
            .group_by(models.SurveyQuestion.survey_id)
        )
        question_counts = {survey_id: count for survey_id, count in question_result.all()}

        session_result = await session.execute(
            select(models.SurveySession.survey_id, func.count(models.SurveySession.id))
            .where(models.SurveySession.survey_id.in_(survey_ids))
            .group_by(models.SurveySession.survey_id)
        )
        session_counts = {survey_id: count for survey_id, count in session_result.all()}

        finished_result = await session.execute(
            select(models.SurveySession.survey_id, func.count(models.SurveySession.id))
            .where(
                models.SurveySession.survey_id.in_(survey_ids),
                models.SurveySession.status == "finished",
            )
            .group_by(models.SurveySession.survey_id)
        )
        finished_counts = {survey_id: count for survey_id, count in finished_result.all()}

    return {
        "items": [
            {
                "id": survey.id,
                "bot_id": survey.bot_id,
                "title": survey.title,
                "description": survey.description,
                "questions_count": question_counts.get(survey.id, 0),
                "sessions_count": session_counts.get(survey.id, 0),
                "finished_sessions_count": finished_counts.get(survey.id, 0),
            }
            for survey in surveys
        ]
    }


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
    survey = await get_survey_or_404(session, survey_id)
    return {"id": survey.id, "bot_id": survey.bot_id, "title": survey.title, "description": survey.description}


@router.get("/{survey_id}/detail")
async def get_survey_detail(survey_id: int, session: AsyncSession = Depends(get_session)):
    survey = await get_survey_or_404(session, survey_id)
    questions = await get_survey_questions(session, survey_id)
    options_map = await get_question_options(session, [question.id for question in questions])

    groups_result = await session.execute(
        select(models.Group.id, models.Group.name)
        .join(models.GroupSurvey, models.GroupSurvey.group_id == models.Group.id)
        .where(models.GroupSurvey.survey_id == survey_id)
        .order_by(models.Group.name)
    )

    sessions_result = await session.execute(
        select(models.SurveySession.status, func.count(models.SurveySession.id))
        .where(models.SurveySession.survey_id == survey_id)
        .group_by(models.SurveySession.status)
    )
    status_counts = {status: count for status, count in sessions_result.all()}

    return {
        "survey": {
            "id": survey.id,
            "bot_id": survey.bot_id,
            "title": survey.title,
            "description": survey.description,
        },
        "questions": [serialize_question(question, options_map) for question in questions],
        "groups": [{"id": group_id, "name": name} for group_id, name in groups_result.all()],
        "stats": {
            "questions_count": len(questions),
            "sessions_count": sum(status_counts.values()),
            "finished_sessions_count": status_counts.get("finished", 0),
            "in_progress_sessions_count": status_counts.get("in_progress", 0),
        },
    }


@router.patch("/{survey_id}")
async def update_survey(survey_id: int, payload: SurveyUpdate, session: AsyncSession = Depends(get_session)):
    survey = await get_survey_or_404(session, survey_id)
    survey.title = payload.title.strip()
    survey.description = payload.description.strip() if payload.description else None

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        raise HTTPException(status_code=400, detail="Cannot update survey (maybe duplicate title?)")

    return {"ok": True, "survey": {"id": survey.id, "title": survey.title, "description": survey.description}}


@router.get("/{survey_id}/results")
async def get_survey_results(survey_id: int, session: AsyncSession = Depends(get_session)):
    survey = await get_survey_or_404(session, survey_id)
    questions = await get_survey_questions(session, survey_id)

    session_result = await session.execute(
        select(models.SurveySession, models.User, models.Group)
        .join(models.User, models.User.id == models.SurveySession.user_id)
        .join(models.Group, models.Group.id == models.SurveySession.group_id)
        .where(models.SurveySession.survey_id == survey_id)
        .order_by(models.SurveySession.id.desc())
    )
    session_rows = session_result.all()

    answer_map = await build_answers_by_session(
        session,
        survey_id,
        [survey_session.id for survey_session, _, _ in session_rows],
        questions=questions,
    )

    return {
        "survey": {
            "id": survey.id,
            "bot_id": survey.bot_id,
            "title": survey.title,
            "description": survey.description,
        },
        "questions": [
            {
                "id": question.id,
                "position": question.position,
                "type": question.type,
                "text": question.text,
            }
            for question in questions
        ],
        "results": [
            {
                "session_id": survey_session.id,
                "group": {
                    "id": group.id,
                    "name": group.name,
                },
                "user": {
                    "id": user.id,
                    "telegram_id": user.telegram_id,
                    "username": user.username,
                },
                "status": survey_session.status,
                "started_at": survey_session.started_at,
                "finished_at": survey_session.finished_at,
                "answers": answer_map.get(survey_session.id, {}),
            }
            for survey_session, user, group in session_rows
        ],
    }


@router.get("/{survey_id}/results/export")
async def export_survey_results_csv(survey_id: int, session: AsyncSession = Depends(get_session)):
    await get_survey_or_404(session, survey_id)
    questions = await get_survey_questions(session, survey_id)

    session_result = await session.execute(
        select(models.SurveySession, models.User, models.Group)
        .join(models.User, models.User.id == models.SurveySession.user_id)
        .join(models.Group, models.Group.id == models.SurveySession.group_id)
        .where(models.SurveySession.survey_id == survey_id)
        .order_by(models.SurveySession.id.desc())
    )
    session_rows = session_result.all()

    answer_map = await build_answers_by_session(
        session,
        survey_id,
        [survey_session.id for survey_session, _, _ in session_rows],
        questions=questions,
    )

    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "session_id",
            "group_id",
            "group_name",
            "user_id",
            "telegram_id",
            "username",
            "status",
            "started_at",
            "finished_at",
            *[question_column_name(question) for question in questions],
        ],
    )

    writer.writeheader()

    for survey_session, user, group in session_rows:
        answer_columns = flatten_answers_for_csv(questions, answer_map.get(survey_session.id, {}))
        writer.writerow({
            "session_id": survey_session.id,
            "group_id": survey_session.group_id,
            "group_name": group.name,
            "user_id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "status": survey_session.status,
            "started_at": survey_session.started_at,
            "finished_at": survey_session.finished_at,
            **answer_columns,
        })

    filename = f"survey_{survey_id}_results.csv"
    csv_content = "\ufeff" + output.getvalue()
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{survey_id}/reset-responses")
async def reset_survey_responses(survey_id: int, session: AsyncSession = Depends(get_session)):
    await get_survey_or_404(session, survey_id)

    session_ids_result = await session.execute(
        select(models.SurveySession.id).where(models.SurveySession.survey_id == survey_id)
    )
    session_ids = list(session_ids_result.scalars().all())

    if not session_ids:
        return {"ok": True, "deleted_sessions": 0}

    await session.execute(
        delete(models.SurveyAnswerOption).where(models.SurveyAnswerOption.session_id.in_(session_ids))
    )
    await session.execute(
        delete(models.SurveyAnswer).where(models.SurveyAnswer.session_id.in_(session_ids))
    )
    await session.execute(
        delete(models.SurveySession).where(models.SurveySession.id.in_(session_ids))
    )
    await session.commit()

    return {"ok": True, "deleted_sessions": len(session_ids)}


@router.delete("/{survey_id}")
async def delete_survey(survey_id: int, session: AsyncSession = Depends(get_session)):
    survey = await get_survey_or_404(session, survey_id)
    if await survey_has_sessions(session, survey_id):
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a survey that already has collected responses",
        )

    await session.delete(survey)
    await session.commit()
    return {"ok": True}


@router.post("/{survey_id}/questions")
async def add_question(survey_id: int, payload: QuestionCreate, session: AsyncSession = Depends(get_session)):
    await get_survey_or_404(session, survey_id)
    if await survey_has_in_progress_sessions(session, survey_id):
        raise HTTPException(
            status_code=400,
            detail="Cannot change survey structure while there are active survey sessions",
        )

    question_type = payload.type.strip().lower()
    if question_type not in {"single", "text"}:
        raise HTTPException(status_code=400, detail="Only 'single' and 'text' supported in MVP")

    options = normalize_options(payload.options)
    if question_type == "single" and len(options) < 2:
        raise HTTPException(status_code=400, detail="Single question requires at least 2 options")

    position_result = await session.execute(
        select(models.SurveyQuestion.position)
        .where(models.SurveyQuestion.survey_id == survey_id)
        .order_by(models.SurveyQuestion.position.desc())
        .limit(1)
    )
    max_position = position_result.scalar_one_or_none()
    next_position = (max_position or 0) + 1

    question = models.SurveyQuestion(
        survey_id=survey_id,
        position=next_position,
        type=question_type,
        text=payload.text.strip(),
    )
    session.add(question)
    await session.flush()

    if question_type == "single":
        for index, option_text in enumerate(options, start=1):
            session.add(
                models.SurveyOption(
                    question_id=question.id,
                    position=index,
                    text=option_text,
                )
            )

    await session.commit()
    options_map = await get_question_options(session, [question.id])
    return {"ok": True, "question": serialize_question(question, options_map)}


@router.patch("/questions/{question_id}")
async def update_question(question_id: int, payload: QuestionUpdate, session: AsyncSession = Depends(get_session)):
    question = await get_question_or_404(session, question_id)
    question_type = payload.type.strip().lower()
    if question_type not in {"single", "text"}:
        raise HTTPException(status_code=400, detail="Only 'single' and 'text' supported in MVP")

    new_text = payload.text.strip()
    new_options = normalize_options(payload.options)
    if question_type == "single" and len(new_options) < 2:
        raise HTTPException(status_code=400, detail="Single question requires at least 2 options")

    has_active_sessions = await survey_has_in_progress_sessions(session, question.survey_id)
    has_answers = await question_has_answers(session, question_id)
    if has_active_sessions:
        current_options = await get_question_options(session, [question.id])
        current_texts = [option.text for option in current_options.get(question.id, [])]
        active_structure_changed = question.type != question_type or (
            question_type == "single" and current_texts != new_options
        )
        if active_structure_changed:
            raise HTTPException(
                status_code=400,
                detail="Cannot change question type or options while there are active survey sessions",
            )

    if has_answers:
        current_options = await get_question_options(session, [question.id])
        current_texts = [option.text for option in current_options.get(question.id, [])]
        structure_changed = question.type != question_type or (
            question_type == "single" and current_texts != new_options
        )
        if structure_changed:
            raise HTTPException(
                status_code=400,
                detail="Cannot change question type or options after responses have been collected",
            )

    question.text = new_text
    question.type = question_type

    existing_options_result = await session.execute(
        select(models.SurveyOption)
        .where(models.SurveyOption.question_id == question.id)
        .order_by(models.SurveyOption.position)
    )
    existing_options = existing_options_result.scalars().all()

    if question_type == "text":
        for option in existing_options:
            await session.delete(option)
    elif not has_answers:
        for option in existing_options:
            await session.delete(option)
        await session.flush()
        for index, option_text in enumerate(new_options, start=1):
            session.add(
                models.SurveyOption(
                    question_id=question.id,
                    position=index,
                    text=option_text,
                )
            )

    await session.commit()
    options_map = await get_question_options(session, [question.id])
    return {"ok": True, "question": serialize_question(question, options_map)}


@router.delete("/questions/{question_id}")
async def delete_question(question_id: int, session: AsyncSession = Depends(get_session)):
    question = await get_question_or_404(session, question_id)
    if await survey_has_in_progress_sessions(session, question.survey_id):
        raise HTTPException(
            status_code=400,
            detail="Cannot change survey structure while there are active survey sessions",
        )
    if await question_has_answers(session, question_id):
        raise HTTPException(
            status_code=400,
            detail="Cannot delete a question that already has collected responses",
        )

    survey_id = question.survey_id
    await session.delete(question)
    await session.flush()
    await compact_question_positions(session, survey_id)
    await session.commit()
    return {"ok": True}

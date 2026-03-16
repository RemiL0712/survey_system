from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models


def question_column_name(question: models.SurveyQuestion) -> str:
    text = " ".join(question.text.split())
    if text:
        return f"Питання {question.position}. {text}"
    return f"Питання {question.position}"


def flatten_answers_for_csv(
    questions: list[models.SurveyQuestion],
    answers: dict[str, object],
) -> dict[str, str]:
    row: dict[str, str] = {}

    for question in questions:
        key = question_column_name(question)
        value = answers.get(str(question.id))

        if value is None:
            row[key] = ""
        elif isinstance(value, str):
            row[key] = value
        elif isinstance(value, dict):
            option_texts = value.get("option_texts", [])
            row[key] = " | ".join(option_texts)
        else:
            row[key] = str(value)

    return row


async def get_survey_questions(
    session: AsyncSession,
    survey_id: int,
) -> list[models.SurveyQuestion]:
    result = await session.execute(
        select(models.SurveyQuestion)
        .where(models.SurveyQuestion.survey_id == survey_id)
        .order_by(models.SurveyQuestion.position)
    )
    return result.scalars().all()


async def build_answers_by_session(
    session: AsyncSession,
    survey_id: int,
    session_ids: list[int],
    questions: list[models.SurveyQuestion] | None = None,
) -> dict[int, dict[str, object]]:
    if not session_ids:
        return {}

    if questions is None:
        questions = await get_survey_questions(session, survey_id)

    answers_by_session: dict[int, dict[str, object]] = {
        session_id: {str(question.id): None for question in questions}
        for session_id in session_ids
    }

    text_result = await session.execute(
        select(models.SurveyAnswer)
        .where(models.SurveyAnswer.session_id.in_(session_ids))
    )
    for answer in text_result.scalars().all():
        answers_by_session.setdefault(answer.session_id, {})[str(answer.question_id)] = answer.answer_text

    option_result = await session.execute(
        select(models.SurveyAnswerOption, models.SurveyOption)
        .join(models.SurveyOption, models.SurveyOption.id == models.SurveyAnswerOption.option_id)
        .where(models.SurveyAnswerOption.session_id.in_(session_ids))
        .order_by(
            models.SurveyAnswerOption.session_id,
            models.SurveyAnswerOption.question_id,
            models.SurveyOption.position,
        )
    )

    option_buckets: dict[int, dict[int, dict[str, list[object]]]] = defaultdict(dict)
    for answer_option, option in option_result.all():
        question_bucket = option_buckets[answer_option.session_id].setdefault(
            answer_option.question_id,
            {"option_ids": [], "option_texts": []},
        )
        question_bucket["option_ids"].append(option.id)
        question_bucket["option_texts"].append(option.text)

    for session_id, question_map in option_buckets.items():
        for question_id, value in question_map.items():
            answers_by_session.setdefault(session_id, {})[str(question_id)] = value

    return answers_by_session

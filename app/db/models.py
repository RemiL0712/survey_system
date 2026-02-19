from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Boolean, func, UniqueConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base

class Bot(Base):
    __tablename__ = "bots"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    token: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BotUser(Base):
    __tablename__ = "bot_users"
    __table_args__ = (UniqueConstraint("bot_id", "user_id", name="uq_bot_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Group(Base):
    __tablename__ = "groups"
    __table_args__ = (UniqueConstraint("bot_id", "name", name="uq_group_name_per_bot"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JoinRequest(Base):
    __tablename__ = "join_requests"

    __table_args__ = (
        Index(
            "uq_joinreq_pending_user_group",
            "user_id",
            "group_id",
            unique=True,
            postgresql_where=text("status = 'pending'")
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/approved/rejected
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_member_user_group"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    joined_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())

# ---------------- Surveys (polls) ----------------

class Survey(Base):
    __tablename__ = "surveys"
    __table_args__ = (UniqueConstraint("bot_id", "title", name="uq_survey_title_per_bot"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(ForeignKey("bots.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SurveyQuestion(Base):
    __tablename__ = "survey_questions"
    __table_args__ = (UniqueConstraint("survey_id", "position", name="uq_survey_question_pos"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("surveys.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column()
    type: Mapped[str] = mapped_column(String(20), default="single")  # single/multi/text/scale
    text: Mapped[str] = mapped_column(String(500))


class SurveyOption(Base):
    __tablename__ = "survey_options"
    __table_args__ = (UniqueConstraint("question_id", "position", name="uq_survey_option_pos"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("survey_questions.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column()
    text: Mapped[str] = mapped_column(String(200))


class GroupSurvey(Base):
    __tablename__ = "group_surveys"
    __table_args__ = (UniqueConstraint("group_id", "survey_id", name="uq_group_survey"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    survey_id: Mapped[int] = mapped_column(ForeignKey("surveys.id", ondelete="CASCADE"))
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SurveySession(Base):
    __tablename__ = "survey_sessions"
    __table_args__ = (
        Index("ix_survey_sessions_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    survey_id: Mapped[int] = mapped_column(ForeignKey("surveys.id", ondelete="CASCADE"))
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    status: Mapped[str] = mapped_column(String(20), default="in_progress")  # in_progress/finished
    current_pos: Mapped[int] = mapped_column(default=1)

    started_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped["DateTime | None"] = mapped_column(DateTime(timezone=True), nullable=True)


class SurveyAnswer(Base):
    __tablename__ = "survey_answers"
    __table_args__ = (UniqueConstraint("session_id", "question_id", name="uq_session_question_answer"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("survey_sessions.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("survey_questions.id", ondelete="CASCADE"))
    answer_text: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SurveyAnswerOption(Base):
    __tablename__ = "survey_answer_options"
    __table_args__ = (UniqueConstraint("session_id", "question_id", "option_id", name="uq_session_question_option"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("survey_sessions.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("survey_questions.id", ondelete="CASCADE"))
    option_id: Mapped[int] = mapped_column(ForeignKey("survey_options.id", ondelete="CASCADE"))
    created_at: Mapped["DateTime"] = mapped_column(DateTime(timezone=True), server_default=func.now())

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Boolean, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base
from sqlalchemy import Index, text

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

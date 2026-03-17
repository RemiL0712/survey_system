import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security import require_api_access
from app.db.models import Bot, BotUser, User
from app.db.session import get_session

router = APIRouter(prefix="/telegram", tags=["telegram"], dependencies=[Depends(require_api_access)])


def parse_admin_tg_ids() -> set[int]:
    admin_ids: set[int] = set()
    for raw_source in (os.getenv("ADMIN_TG_ID", ""), os.getenv("ADMIN_TG_IDS", "")):
        for raw_value in raw_source.split(","):
            value = raw_value.strip()
            if value.isdigit():
                admin_ids.add(int(value))
    return admin_ids


ADMIN_TG_IDS = parse_admin_tg_ids()


class RegisterIn(BaseModel):
    bot_id: int
    telegram_id: int
    username: str | None = None


@router.post("/register")
async def register(data: RegisterIn, session: AsyncSession = Depends(get_session)):
    bot_result = await session.execute(select(Bot).where(Bot.id == data.bot_id))
    bot = bot_result.scalar_one_or_none()
    if not bot:
        bot = Bot(
            id=data.bot_id,
            name=os.getenv("BOT_NAME", "Survey Bot"),
            token=os.getenv("BOT_TOKEN", ""),
            is_active=True,
        )
        session.add(bot)
        await session.flush()

    user_result = await session.execute(select(User).where(User.telegram_id == data.telegram_id))
    user = user_result.scalar_one_or_none()
    if not user:
        user = User(telegram_id=data.telegram_id, username=data.username)
        session.add(user)
        await session.flush()
    elif user.username != data.username:
        user.username = data.username

    desired_role = "admin" if data.telegram_id in ADMIN_TG_IDS else "user"

    link_result = await session.execute(
        select(BotUser).where(BotUser.bot_id == data.bot_id, BotUser.user_id == user.id)
    )
    link = link_result.scalar_one_or_none()
    if not link:
        link = BotUser(bot_id=data.bot_id, user_id=user.id, role=desired_role)
        session.add(link)
    elif desired_role == "admin" and link.role != "admin":
        link.role = "admin"

    await session.commit()
    return {"user_id": user.id, "bot_id": data.bot_id, "role": link.role}

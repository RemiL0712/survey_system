import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.db.models import Bot, BotUser, User

router = APIRouter(prefix="/telegram", tags=["telegram"])


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

    res = await session.execute(select(User).where(User.telegram_id == data.telegram_id))
    user = res.scalar_one_or_none()
    if not user:
        user = User(telegram_id=data.telegram_id, username=data.username)
        session.add(user)
        await session.flush()

    res = await session.execute(
        select(BotUser).where(BotUser.bot_id == data.bot_id, BotUser.user_id == user.id)
    )
    link = res.scalar_one_or_none()
    if not link:
        link = BotUser(bot_id=data.bot_id, user_id=user.id, role="user")
        session.add(link)

    await session.commit()
    return {"user_id": user.id, "bot_id": data.bot_id, "role": "user"}

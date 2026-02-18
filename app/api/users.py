from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.db.models import User

router = APIRouter(prefix="/users", tags=["users"])

@router.get("/by-telegram")
async def by_telegram(telegram_id: int, session: AsyncSession = Depends(get_session)):
    res = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user_id": user.id}

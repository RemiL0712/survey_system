from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
from app.api.security import require_api_access
from app.db.models import User  # додай імпорт зверху



from app.db.session import get_session
from app.db.models import JoinRequest, GroupMember, Group

router = APIRouter(prefix="/join-requests", tags=["join-requests"], dependencies=[Depends(require_api_access)])


class JoinRequestCreateIn(BaseModel):
    user_id: int
    group_id: int


@router.post("")
async def create_join_request(data: JoinRequestCreateIn, session: AsyncSession = Depends(get_session)):
    # 1) вже в групі?
    res = await session.execute(
        select(GroupMember).where(GroupMember.user_id == data.user_id, GroupMember.group_id == data.group_id)
    )
    if res.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Already a member")

    # 2) є pending?
    res = await session.execute(
        select(JoinRequest).where(
            JoinRequest.user_id == data.user_id,
            JoinRequest.group_id == data.group_id,
            JoinRequest.status == "pending",
        )
    )
    if res.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Request already pending")

    jr = JoinRequest(user_id=data.user_id, group_id=data.group_id, status="pending")
    session.add(jr)
    await session.commit()
    await session.refresh(jr)
    return {"id": jr.id, "status": jr.status}


@router.get("/pending")
async def pending_requests(bot_id: int, session: AsyncSession = Depends(get_session)):
    # join join_requests -> groups щоб фільтрувати по bot_id
    res = await session.execute(
        select(JoinRequest.id, JoinRequest.user_id, JoinRequest.group_id, Group.name)
        .join(Group, Group.id == JoinRequest.group_id)
        .where(Group.bot_id == bot_id, JoinRequest.status == "pending")
        .order_by(JoinRequest.id)
    )
    rows = res.all()
    return [
        {"id": r.id, "user_id": r.user_id, "group_id": r.group_id, "group_name": r.name}
        for r in rows
    ]


class ProcessIn(BaseModel):
    admin_id: int

@router.patch("/{request_id}/reject")
async def reject(request_id: int, data: ProcessIn, session: AsyncSession = Depends(get_session)):
    res = await session.execute(select(JoinRequest).where(JoinRequest.id == request_id))
    jr = res.scalar_one_or_none()
    if not jr:
        raise HTTPException(status_code=404, detail="Request not found")

    # якщо вже оброблено — повертаємо без помилки
    if jr.status != "pending":
        res_u = await session.execute(select(User).where(User.id == jr.user_id))
        user = res_u.scalar_one()

        res_g = await session.execute(select(Group).where(Group.id == jr.group_id))
        group = res_g.scalar_one()

        return {
            "ok": True,
            "already_processed": True,
            "status": jr.status,
            "user_telegram_id": user.telegram_id,
            "group_name": group.name,
        }


    jr.status = "rejected"
    jr.processed_by = data.admin_id
    jr.processed_at = datetime.now(timezone.utc)

    await session.commit()

    res_u = await session.execute(select(User).where(User.id == jr.user_id))
    user = res_u.scalar_one()

    res_g = await session.execute(select(Group).where(Group.id == jr.group_id))
    group = res_g.scalar_one()

    return {
        "ok": True,
        "status": "rejected",
        "user_telegram_id": user.telegram_id,
        "group_name": group.name,
    }

@router.patch("/{request_id}/approve")
async def approve(request_id: int, data: ProcessIn, session: AsyncSession = Depends(get_session)):
    res = await session.execute(select(JoinRequest).where(JoinRequest.id == request_id))
    jr = res.scalar_one_or_none()
    if not jr:
        raise HTTPException(status_code=404, detail="Request not found")

    # якщо вже оброблено — повертаємо без помилки
    if jr.status != "pending":
        res_u = await session.execute(select(User).where(User.id == jr.user_id))
        user = res_u.scalar_one()

        res_g = await session.execute(select(Group).where(Group.id == jr.group_id))
        group = res_g.scalar_one()

        return {
            "ok": True,
            "already_processed": True,
            "status": jr.status,
            "user_telegram_id": user.telegram_id,
            "group_name": group.name,
        }



    # перевірити чи вже є учасником
    res = await session.execute(
        select(GroupMember).where(
            GroupMember.user_id == jr.user_id,
            GroupMember.group_id == jr.group_id
        )
    )
    if not res.scalar_one_or_none():
        session.add(GroupMember(user_id=jr.user_id, group_id=jr.group_id))

    jr.status = "approved"
    jr.processed_by = data.admin_id
    jr.processed_at = datetime.now(timezone.utc)

    await session.commit()

    # отримати user + group
    res_u = await session.execute(select(User).where(User.id == jr.user_id))
    user = res_u.scalar_one()

    res_g = await session.execute(select(Group).where(Group.id == jr.group_id))
    group = res_g.scalar_one()

    return {
        "ok": True,
        "status": "approved",
        "user_telegram_id": user.telegram_id,
        "group_name": group.name,
    }


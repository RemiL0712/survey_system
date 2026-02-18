from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.db.models import Group, GroupMember, User, JoinRequest
from app.db.session import get_session

router = APIRouter(prefix="/groups", tags=["groups"])


class GroupCreateIn(BaseModel):
    bot_id: int
    name: str
    created_by: int | None = None
    
class GroupUpdateIn(BaseModel):
    name: str

@router.post("")
async def create_group(data: GroupCreateIn, session: AsyncSession = Depends(get_session)):
    group = Group(bot_id=data.bot_id, name=data.name, created_by=data.created_by)
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return {"id": group.id, "bot_id": group.bot_id, "name": group.name}


@router.get("")
async def list_groups(bot_id: int, session: AsyncSession = Depends(get_session)):
    res = await session.execute(select(Group).where(Group.bot_id == bot_id).order_by(Group.id))
    groups = res.scalars().all()
    return [{"id": g.id, "name": g.name} for g in groups]

@router.get("/{group_id}/members")
async def group_members(group_id: int, session: AsyncSession = Depends(get_session)):
    res = await session.execute(
        select(User.id, User.telegram_id, User.username, GroupMember.joined_at)
        .join(GroupMember, GroupMember.user_id == User.id)
        .where(GroupMember.group_id == group_id)
        .order_by(GroupMember.joined_at.desc())
    )
    rows = res.all()
    return [
        {
            "user_id": r.id,
            "telegram_id": r.telegram_id,
            "username": r.username,
            "joined_at": r.joined_at,
        }
        for r in rows
    ]

@router.delete("/{group_id}/members/{user_id}")
async def remove_member(group_id: int, user_id: int, session: AsyncSession = Depends(get_session)):
    # дістанемо дані для повідомлення
    res_u = await session.execute(select(User).where(User.id == user_id))
    user = res_u.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    res_g = await session.execute(select(Group).where(Group.id == group_id))
    group = res_g.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # видаляємо членство
    res = await session.execute(
        delete(GroupMember)
        .where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
        .returning(GroupMember.id)
    )
    deleted = res.scalar_one_or_none()
    if not deleted:
        raise HTTPException(status_code=404, detail="Member not found")

    await session.commit()
    return {"ok": True, "user_telegram_id": user.telegram_id, "group_name": group.name}

@router.patch("/{group_id}")
async def rename_group(group_id: int, data: GroupUpdateIn, session: AsyncSession = Depends(get_session)):
    new_name = data.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name is empty")

    res = await session.execute(select(Group).where(Group.id == group_id))
    group = res.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    group.name = new_name
    await session.commit()
    return {"ok": True, "id": group.id, "name": group.name}

@router.delete("/{group_id}")
async def delete_group(group_id: int, session: AsyncSession = Depends(get_session)):
    # перевірка існування групи
    res = await session.execute(select(Group).where(Group.id == group_id))
    group = res.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # чистимо залежності (щоб не було FK проблем)
    await session.execute(delete(GroupMember).where(GroupMember.group_id == group_id))
    await session.execute(delete(JoinRequest).where(JoinRequest.group_id == group_id))

    # видаляємо групу
    await session.execute(delete(Group).where(Group.id == group_id))
    await session.commit()

    return {"ok": True, "deleted_group_id": group_id}

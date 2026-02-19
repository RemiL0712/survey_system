from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.db.models import Group, GroupMember, User, JoinRequest
from app.db.session import get_session
from app.db import models


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
@router.get("/{group_id}/surveys")
async def list_group_surveys(group_id: int, user_id: int, session: AsyncSession = Depends(get_session)):
    g = await session.execute(select(models.Group).where(models.Group.id == group_id))
    group = g.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # ✅ доступ тільки членам групи або creator
    is_member = await session.execute(
        select(models.GroupMember.id).where(
            models.GroupMember.group_id == group_id,
            models.GroupMember.user_id == user_id,
        )
    )
    if not is_member.scalar_one_or_none() and group.created_by != user_id:
        raise HTTPException(status_code=403, detail="Not a member of this group")

    rows = await session.execute(
        select(models.Survey.id, models.Survey.title, models.Survey.description)
        .join(models.GroupSurvey, models.GroupSurvey.survey_id == models.Survey.id)
        .where(models.GroupSurvey.group_id == group_id)
        .order_by(models.Survey.id.desc())
    )
    data = rows.all()

    return {
        "group_id": group_id,
        "surveys": [{"id": r.id, "title": r.title, "description": r.description} for r in data],
    }

@router.post("/{group_id}/surveys/{survey_id}")
async def attach_survey(group_id: int, survey_id: int, session: AsyncSession = Depends(get_session)):
    g = await session.execute(select(models.Group).where(models.Group.id == group_id))
    if not g.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Group not found")

    s = await session.execute(select(models.Survey).where(models.Survey.id == survey_id))
    if not s.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Survey not found")

    exists = await session.execute(
        select(models.GroupSurvey.id).where(
            models.GroupSurvey.group_id == group_id,
            models.GroupSurvey.survey_id == survey_id
        )
    )
    if exists.scalar_one_or_none():
        return {"ok": True, "already": True}

    session.add(models.GroupSurvey(group_id=group_id, survey_id=survey_id))
    await session.commit()
    return {"ok": True}


@router.delete("/{group_id}/surveys/{survey_id}")
async def detach_survey(group_id: int, survey_id: int, session: AsyncSession = Depends(get_session)):
    res = await session.execute(
        select(models.GroupSurvey).where(
            models.GroupSurvey.group_id == group_id,
            models.GroupSurvey.survey_id == survey_id
        )
    )
    link = res.scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Not attached")

    await session.delete(link)
    await session.commit()
    return {"ok": True}

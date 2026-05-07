from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime
from datetime import timedelta
from app.db.session import get_db_session
from app.models.chat import ChatSession, ChatMember, MemberRole, ChatMessage, MessageReaction, MessageRead, ChatType
from app.schemas.chat import SendMessage
from app.models.user import User
from app.core.dependencies import get_current_user

import json

async def validate_membership(chat_id: int, user_id: int, db: AsyncSession):
    result = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == user_id
        )
    )
    if not result.scalar():
        raise HTTPException(403, "Not a member of this chat")
    
async def validate_admin(chat_id: int, user_id: int, db: AsyncSession):
    result = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == user_id
        )
    )
    member = result.scalar()

    if not member or member.role != MemberRole.ADMIN:
        raise HTTPException(403, "Admin access required")

router = APIRouter(prefix="/chats", tags=["Chat"])

@router.post("/private/{user_id}")
async def create_private_chat(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    # check if chat already exists (FIXED)
    # existing = await db.execute(
    #     select(ChatSession)
    #     .join(ChatMember)
    #     .where(ChatSession.type == ChatType.PRIVATE)
    #     .group_by(ChatSession.id)
    #     .having(
    #         func.count(ChatMember.id) == 2,
    #         func.count().filter(ChatMember.user_id == current_user.id) == 1,
    #         func.count().filter(ChatMember.user_id == user_id) == 1
    #     )
    # )

    from sqlalchemy import func, case

    existing = await db.execute(
        select(ChatSession)
        .join(ChatMember)
        .where(ChatSession.type == ChatType.PRIVATE)
        .group_by(ChatSession.id)
        .having(
            func.count(ChatMember.id) == 2,
            func.sum(
                case((ChatMember.user_id == current_user.id, 1), else_=0)
            ) == 1,
            func.sum(
                case((ChatMember.user_id == user_id, 1), else_=0)
            ) == 1,
        )
    )

    chat = existing.scalar()

    if chat:
        return {"chat_id": chat.id}

    # create new chat
    chat = ChatSession(
        type=ChatType.PRIVATE,
        created_by=current_user.id
    )
    db.add(chat)
    await db.flush()

    db.add_all([
        ChatMember(chat_id=chat.id, user_id=current_user.id),
        ChatMember(chat_id=chat.id, user_id=user_id),
    ])

    return {"chat_id": chat.id}


@router.post("/{chat_id}/messages")
async def send_message(
    request: Request,
    chat_id: int,
    payload: SendMessage,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_membership(chat_id, current_user.id, db)

    #  2. VALIDATE PARENT MESSAGE CHAT
    if payload.parent_id:
        parent = await db.get(ChatMessage, payload.parent_id)

        if not parent or parent.chat_id != chat_id:
            raise HTTPException(400, "Invalid parent message")

    #  3. LIMIT MESSAGE SIZE
    if payload.message and len(payload.message) > 2000:
        raise HTTPException(400, "Message too long")

    #  8. VALIDATE ATTACHMENT URL
    if payload.attachment_url and not payload.attachment_url.startswith("http"):
        raise HTTPException(400, "Invalid attachment URL")

    #  7. RATE LIMIT (requires redis)
    try:
        redis = request.app.state.redis
        if redis:
            key = f"user:{current_user.id}:msg_rate"
            count = await redis.incr(key)

            if count == 1:
                await redis.expire(key, 1)

            if count > 10:
                raise HTTPException(429, "Too many messages")
    except Exception:
        pass  # don't block request if redis fails

    msg = ChatMessage(
        chat_id=chat_id,
        sender_id=current_user.id,
        message=payload.message,
        parent_id=payload.parent_id,
        attachment_url=payload.attachment_url
    )

    db.add(msg)
    await db.flush()

    db.add(MessageRead(
        message_id=msg.id,
        user_id=current_user.id
    ))

    chat = await db.get(ChatSession, chat_id)

    if not chat:
        raise HTTPException(404, "Chat not found")

    chat.last_message = payload.message
    chat.last_message_at = datetime.utcnow()

    #  4. REDIS FAIL SAFETY
    try:
        redis = request.app.state.redis
        if redis:
            await redis.publish(
                f"chat:{chat_id}",
                json.dumps({
                    "type": "message",
                    "chat_id": chat_id,
                    "message": payload.message,
                    "sender": current_user.id,
                    "message_id": msg.id,
                    "parent_id": payload.parent_id,
                    "attachment_url": payload.attachment_url,
                    "status": "sent"
                })
            )
    except Exception:
        pass

    return msg


from sqlalchemy.orm import aliased

@router.get("/{chat_id}/messages")
async def get_messages(
    request: Request,
    chat_id: int,
    limit: int = 20,
    cursor: int | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_membership(chat_id, current_user.id, db)

    Parent = aliased(ChatMessage)

    query = (
        select(ChatMessage, User, Parent)
        .join(User, ChatMessage.sender_id == User.id)
        .outerjoin(Parent, ChatMessage.parent_id == Parent.id)
        .where(ChatMessage.chat_id == chat_id)
    )

    if cursor:
        query = query.where(ChatMessage.id < cursor)

    query = query.order_by(ChatMessage.id.desc()).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    messages = []
    message_objects = []

    for msg, user, parent in rows:
        message_objects.append(msg)

        message_text = "[deleted]" if getattr(msg, "is_deleted", False) else msg.message

        messages.append({
            "id": msg.id,
            "message": message_text,
            "created_at": msg.created_at,
            "status": msg.status.value if msg.status else None,
            "parent_id": msg.parent_id,
            "chat_id": msg.chat_id,

            "sender": {
                "id": user.id,
                "name": user.name
            },

            "parent": {
                "id": parent.id,
                "message": "[deleted]" if parent.is_deleted else parent.message
            } if parent else None,

            "is_deleted": msg.is_deleted,
            "is_pinned": msg.is_pinned,
            "is_edited": getattr(msg, "is_edited", False),
            "attachment_url": msg.attachment_url,

            "read_by": []
        })

    # mark messages as read
    message_ids = [
        msg.id for msg in message_objects
        if msg.sender_id != current_user.id
    ][:50]

    if message_ids:
        existing_reads = await db.execute(
            select(MessageRead.message_id).where(
                MessageRead.user_id == current_user.id,
                MessageRead.message_id.in_(message_ids)
            )
        )

        already_read_ids = set(existing_reads.scalars().all())

        new_reads = [
            MessageRead(
                message_id=mid,
                user_id=current_user.id
            )
            for mid in message_ids
            if mid not in already_read_ids
        ]

        db.add_all(new_reads)

        # realtime read event
        if new_reads:
            try:
                redis = request.app.state.redis

                if redis:
                    await redis.publish(
                        f"chat:{chat_id}",
                        json.dumps({
                            "type": "read",
                            "chat_id": chat_id,
                            "user_id": current_user.id,
                            "message_ids": message_ids
                        })
                    )
            except Exception:
                pass

    # batch read_by
    read_map = {}

    if message_objects:
        reads = await db.execute(
            select(
                MessageRead.message_id,
                MessageRead.user_id
            ).where(
                MessageRead.message_id.in_(
                    [m.id for m in message_objects]
                )
            )
        )

        for mid, uid in reads.all():
            read_map.setdefault(mid, []).append(uid)

    # inject read_by
    for item in messages:
        item["read_by"] = read_map.get(item["id"], [])

    return messages


@router.get("/messages/{message_id}/replies")
async def get_replies(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    # check parent exists
    parent = await db.get(ChatMessage, message_id)

    if not parent:
        raise HTTPException(404, "Parent message not found")

    # validate membership
    await validate_membership(
        parent.chat_id,
        current_user.id,
        db
    )

    result = await db.execute(
        select(ChatMessage, User)
        .join(User, ChatMessage.sender_id == User.id)
        .where(ChatMessage.parent_id == message_id)
        .order_by(ChatMessage.created_at.asc())
    )

    rows = result.all()

    replies = []

    for msg, user in rows:
        replies.append({
            "id": msg.id,
            "message": "[deleted]" if msg.is_deleted else msg.message,
            "created_at": msg.created_at,
            "sender": {
                "id": user.id,
                "name": user.name
            },
            "attachment_url": msg.attachment_url,
            "is_deleted": msg.is_deleted,
            "is_edited": getattr(msg, "is_edited", False)
        })

    return replies


@router.get("/{chat_id}/unread")
async def unread_count(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_membership(chat_id, current_user.id, db)

    count = await db.scalar(
        select(func.count())
        .select_from(ChatMessage)
        .outerjoin(
            MessageRead,
            (ChatMessage.id == MessageRead.message_id) &
            (MessageRead.user_id == current_user.id)
        )
        .where(
            ChatMessage.chat_id == chat_id,
            ChatMessage.sender_id != current_user.id,
            MessageRead.id.is_(None)   # 
        )
    )

    return {"unread": count}


@router.post("/group")
async def create_group(
    name: str,
    member_ids: list[int],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    if current_user.id not in member_ids:
        member_ids.append(current_user.id)

    chat = ChatSession(
        type=ChatType.GROUP,
        name=name,
        created_by=current_user.id
    )

    db.add(chat)
    await db.flush()

    for uid in set(member_ids):
        role = MemberRole.ADMIN if uid == current_user.id else MemberRole.MEMBER

        db.add(ChatMember(
            chat_id=chat.id,
            user_id=uid,
            role=role
        ))

    return {"chat_id": chat.id}


@router.post("/group/{chat_id}/add")
async def add_member(
    chat_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_admin(chat_id, current_user.id, db)

    existing = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == user_id
        )
    )

    if existing.scalar():
        return {"status": "already exists"}

    db.add(ChatMember(
        chat_id=chat_id,
        user_id=user_id,
        role=MemberRole.MEMBER
    ))

    return {"status": "added"}


@router.post("/group/{chat_id}/remove")
async def remove_member(
    chat_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    #  only admin can remove
    await validate_admin(chat_id, current_user.id, db)

    #  prevent admin removing themselves via this API
    if user_id == current_user.id:
        raise HTTPException(400, "Use leave API instead")

    obj = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == user_id
        )
    )
    member = obj.scalar()

    if not member:
        raise HTTPException(404, "Member not found")

    await db.delete(member)

    return {"status": "removed"}


@router.post("/{chat_id}/typing")
async def typing(
    request: Request,
    chat_id: int,
    is_typing: bool,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_membership(chat_id, current_user.id, db)
    redis = request.app.state.redis

    if redis:
        # store typing with expiry
        if is_typing:
            await redis.set(
                f"chat:{chat_id}:typing:{current_user.id}",
                1,
                ex=5
            )
        else:
            await redis.delete(f"chat:{chat_id}:typing:{current_user.id}")

        await redis.publish(
            f"chat:{chat_id}",
            json.dumps({
                "type": "typing",
                "user": current_user.id,
                "is_typing": is_typing
            })
        )

    return {"ok": True}


@router.get("/")
async def get_chat_list(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    from sqlalchemy import case

    result = await db.execute(
        select(
            ChatSession.id,
            ChatSession.name,
            ChatSession.last_message,
            ChatSession.last_message_at,

            func.sum(
                case(
                    (
                        (ChatMessage.sender_id != current_user.id) &
                        (MessageRead.id.is_(None)),
                        1
                    ),
                    else_=0
                )
            ).label("unread_count")
        )
        .join(ChatMember, ChatMember.chat_id == ChatSession.id)
        .outerjoin(ChatMessage, ChatMessage.chat_id == ChatSession.id)
        .outerjoin(
            MessageRead,
            (ChatMessage.id == MessageRead.message_id) &
            (MessageRead.user_id == current_user.id)
        )
        .where(ChatMember.user_id == current_user.id)
        .group_by(ChatSession.id)
        .order_by(ChatSession.last_message_at.desc())
    )

    return result.all()

@router.post("/group/{chat_id}/kick")
async def kick_member(
    chat_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_admin(chat_id, current_user.id, db)

    obj = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == user_id
        )
    )
    member = obj.scalar()

    if not member:
        raise HTTPException(404, "User not in group")

    await db.delete(member)

    return {"status": "kicked"}


@router.post("/group/{chat_id}/leave")
async def leave_group(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == current_user.id
        )
    )
    member = obj.scalar()

    if not member:
        raise HTTPException(404, "Not part of group")

    #  if admin leaving → handle admin transfer
    if member.role == MemberRole.ADMIN:
        admins = await db.execute(
            select(ChatMember).where(
                ChatMember.chat_id == chat_id,
                ChatMember.role == MemberRole.ADMIN
            )
        )
        admin_list = admins.scalars().all()

        #  if this is last admin → promote someone else
        if len(admin_list) == 1:
            others = await db.execute(
                select(ChatMember).where(
                    ChatMember.chat_id == chat_id,
                    ChatMember.user_id != current_user.id
                )
            )
            candidates = others.scalars().all()

            if candidates:
                candidates[0].role = MemberRole.ADMIN
            else:
                #  no members left → allow delete (group becomes empty)
                pass

    #  now remove current user
    await db.delete(member)

    #  ADD THIS BLOCK
    remaining = await db.execute(
        select(ChatMember).where(ChatMember.chat_id == chat_id)
    )

    if not remaining.scalars().first():
        chat = await db.get(ChatSession, chat_id)
        if chat:
            await db.delete(chat)

    return {"status": "left"}


@router.post("/group/{chat_id}/transfer-admin")
async def transfer_admin(
    chat_id: int,
    new_admin_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    #  ensure current user is admin
    await validate_admin(chat_id, current_user.id, db)

    #  fetch new admin
    result = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == new_admin_id
        )
    )
    new_admin = result.scalar()

    if not new_admin:
        raise HTTPException(404, "User not in group")

    #  fetch current admin record
    current = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == current_user.id
        )
    )
    current_member = current.scalar()

    if not current_member:
        raise HTTPException(404, "Current admin not found")

    #  optional: prevent transferring to self
    if new_admin_id == current_user.id:
        return {"status": "already admin"}

    #  DEMOTE current admin
    #  if u want multiple admin remove this lne
    current_member.role = MemberRole.MEMBER

    #  PROMOTE new admin
    new_admin.role = MemberRole.ADMIN

    return {"status": "transferred"}


@router.post("/messages/{message_id}/react")
async def react_message(
    message_id: int,
    reaction: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    msg = await db.get(ChatMessage, message_id)

    if not msg:
        raise HTTPException(404, "Message not found")

    await validate_membership(msg.chat_id, current_user.id, db)

    # check existing
    result = await db.execute(
        select(MessageReaction).where(
            MessageReaction.message_id == message_id,
            MessageReaction.user_id == current_user.id
        )
    )
    existing = result.scalar()

    if existing:
        # update reaction (no duplicate row)
        existing.reaction = reaction
    else:
        db.add(MessageReaction(
            message_id=message_id,
            user_id=current_user.id,
            reaction=reaction
        ))

    # realtime event
    redis = request.app.state.redis
    if redis:
        await redis.publish(
            f"chat:{msg.chat_id}",
            json.dumps({
                "type": "reaction",
                "message_id": message_id,
                "user_id": current_user.id,
                "reaction": reaction
            })
        )

    return {"status": "reacted"}


@router.put("/messages/{message_id}/edit")
async def edit_message(
    request: Request,   #  ADD THIS
    message_id: int,
    new_text: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    msg = await db.get(ChatMessage, message_id)

    if not msg:
        raise HTTPException(404, "ChatMessage not found")

    if msg.sender_id != current_user.id:
        raise HTTPException(403, "Not allowed")

    if datetime.utcnow() - msg.created_at > timedelta(minutes=2):
        raise HTTPException(403, "Edit time expired")

    msg.message = new_text
    msg.is_edited = True

    #  REAL-TIME edit event
    redis = request.app.state.redis
    if redis:
        await redis.publish(
            f"chat:{msg.chat_id}",
            json.dumps({
                "type": "edit",
                "message_id": msg.id,
                "new_text": new_text
            })
        )

    return {"status": "edited"}


@router.delete("/messages/{message_id}")
async def delete_message(
    request: Request,   #  ADD THIS
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    msg = await db.get(ChatMessage, message_id)

    if not msg:
        raise HTTPException(404, "ChatMessage not found")

    if msg.sender_id != current_user.id:
        raise HTTPException(403, "Not allowed")

    if datetime.utcnow() - msg.created_at > timedelta(minutes=5):
        raise HTTPException(403, "Delete time expired")

    #  soft delete
    msg.is_deleted = True
    msg.message = "[deleted]"

    #  REAL-TIME delete event
    redis = request.app.state.redis
    if redis:
        await redis.publish(
            f"chat:{msg.chat_id}",
            json.dumps({
                "type": "delete",
                "message_id": msg.id
            })
        )

    return {"status": "deleted"}


@router.get("/users/{user_id}/status")
async def user_status(user_id: int, request: Request):
    redis = request.app.state.redis

    if not redis:
        return {
            "online": False,
            "last_seen": None
        }

    online = await redis.get(f"user:{user_id}:online")
    last_seen = await redis.get(f"user:{user_id}:last_seen")

    return {
        "online": bool(online),
        "last_seen": last_seen.decode() if last_seen else None  # ✅ FIX
    }


@router.get("/{chat_id}/search")
async def search_messages(
    chat_id: int,
    query: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_membership(chat_id, current_user.id, db)
    result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.chat_id == chat_id,
            ChatMessage.message.ilike(f"%{query}%")
        )
        .limit(50)
    )

    return result.scalars().all()


@router.post("/messages/{message_id}/pin")
async def pin_message(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    msg = await db.get(ChatMessage, message_id)

    if not msg:
        raise HTTPException(404, "ChatMessage not found")

    # optional but important safety (same pattern as edit/delete)
    if msg.sender_id != current_user.id:
        raise HTTPException(403, "Not allowed")

    msg.is_pinned = True

    return {"status": "pinned"}


@router.get("/{chat_id}/active-users")
async def active_users(
    chat_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    #  ensure user belongs to chat
    await validate_membership(chat_id, current_user.id, db)

    redis = request.app.state.redis

    if not redis:
        return {"active_users": []}

    users = await redis.smembers(f"chat:{chat_id}:online_users")

    return {"active_users": [int(u.decode()) for u in users]}


@router.get("/{chat_id}/user-states")
async def get_user_states(
    chat_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_membership(chat_id, current_user.id, db)
    redis = request.app.state.redis

    # get members
    result = await db.execute(
        select(ChatMember.user_id).where(ChatMember.chat_id == chat_id)
    )
    user_ids = result.scalars().all()

    states = []

    for uid in user_ids:
        online = await redis.get(f"user:{uid}:online")
        last_seen = await redis.get(f"user:{uid}:last_seen")

        states.append({ 
            "user_id": uid,
            "online": bool(online),
            "last_seen": last_seen.decode() if last_seen else None
        })

    return states
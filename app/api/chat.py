from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import case, or_, select, func, text, update
from datetime import datetime
from datetime import timedelta
from app.db.session import get_db_session
from app.models.chat import (
    ChatSession,
    ChatMember,
    MemberRole,
    ChatMessage,
    MessageDelivery,
    MessageReaction,
    MessageRead,
    ChatType,
    MessageAttachment,
    MessageMention,
)
from app.schemas.chat import (
    ChatInfoOut,
    ChatListEnhancedOut,
    ChatListOut,
    ChatMemberAddPayload,
    ChatUserOut,
    ChatUserSearchOut,
    CreateGroup,
    MentionMessageOut,
    MessageOut,
    MessageReadUserOut,
    ReplyOut,
    SendMessage,
)
from app.models.user import User
from app.core.dependencies import get_current_user
import json
from pathlib import Path
from PIL import Image
from io import BytesIO
import uuid
from sqlalchemy.orm import aliased, selectinload

# =========================
# LIMITS
# =========================

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_VIDEO_SIZE = 50 * 1024 * 1024  # 50MB
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB

# =========================
# MIME TYPES
# =========================

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}

VIDEO_TYPES = {"video/mp4", "video/quicktime"}

DOCUMENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

ALLOWED_TYPES = IMAGE_TYPES | VIDEO_TYPES | DOCUMENT_TYPES


async def validate_membership(chat_id: int, user_id: int, db: AsyncSession):
    result = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id, ChatMember.user_id == user_id
        )
    )
    if not result.scalar():
        raise HTTPException(403, "Not a member of this chat")


async def validate_admin(chat_id: int, user_id: int, db: AsyncSession):
    result = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id, ChatMember.user_id == user_id
        )
    )
    member = result.scalar()

    if not member or member.role != MemberRole.ADMIN:
        raise HTTPException(403, "Admin access required")


async def validate_group(chat_id: int, db: AsyncSession):
    chat = await db.get(ChatSession, chat_id)

    if not chat or chat.type != ChatType.GROUP:
        raise HTTPException(400, "Group chat required")

    return chat


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

    if user_id == current_user.id:
        raise HTTPException(400, "Cannot create chat with yourself")

    # CHECK TARGET USER EXISTS
    target = await db.get(User, user_id)

    if not target:
        raise HTTPException(404, "User not found")

    existing = await db.execute(
        select(ChatSession)
        .join(ChatMember)
        .where(ChatSession.type == ChatType.PRIVATE)
        .group_by(ChatSession.id)
        .having(
            func.count(ChatMember.id) == 2,
            func.sum(case((ChatMember.user_id == current_user.id, 1), else_=0)) == 1,
            func.sum(case((ChatMember.user_id == user_id, 1), else_=0)) == 1,
        )
    )

    chat = existing.scalar()

    if chat:
        return {"chat_id": chat.id}

    # create new chat
    chat = ChatSession(type=ChatType.PRIVATE, created_by=current_user.id)
    db.add(chat)
    await db.flush()

    db.add_all(
        [
            ChatMember(chat_id=chat.id, user_id=current_user.id),
            ChatMember(chat_id=chat.id, user_id=user_id),
        ]
    )

    await db.commit()

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
    # EMPTY MESSAGE VALIDATION
    if (
        not payload.message or not payload.message.strip()
    ) and not payload.attachment_ids:
        raise HTTPException(400, "Message required")

    # MESSAGE LENGTH
    if payload.message and len(payload.message.strip()) > 2000:
        raise HTTPException(400, "Message too long")

    # MENTION DETECTION

    mention_ids = list(set(payload.mention_user_ids))

    mentioned_users = []

    if mention_ids:

        valid_members = await db.execute(
            select(ChatMember.user_id).where(
                ChatMember.chat_id == chat_id,
                ChatMember.user_id.in_(mention_ids),
            )
        )

        valid_member_ids = set(valid_members.scalars().all())

        invalid_ids = set(mention_ids) - valid_member_ids

        if invalid_ids:
            raise HTTPException(400, f"Invalid mentioned users: {list(invalid_ids)}")

        users = await db.execute(select(User).where(User.id.in_(mention_ids)))

        mentioned_users = users.scalars().all()

    #  7. RATE LIMIT (requires redis)
    try:
        redis = getattr(request.app.state, "redis", None)

        if redis:
            key = f"user:{current_user.id}:msg_rate"

            count = await redis.incr(key)

            if count == 1:
                await redis.expire(key, 1)

            if count > 10:
                raise HTTPException(429, "Too many messages")

    except HTTPException:
        raise

    except Exception:
        pass

    msg = ChatMessage(
        chat_id=chat_id,
        sender_id=current_user.id,
        message=payload.message,
        parent_id=payload.parent_id,
    )

    db.add(msg)

    await db.flush()

    if mention_ids:

        db.add_all(
            [
                MessageMention(
                    message_id=msg.id,
                    mentioned_user_id=user_id,
                )
                for user_id in mention_ids
            ]
        )

    # attach uploaded files
    if payload.attachment_ids:

        attachments = await db.execute(
            select(MessageAttachment).where(
                MessageAttachment.id.in_(payload.attachment_ids),
                MessageAttachment.message_id.is_(None),
                MessageAttachment.uploaded_by == current_user.id,
            )
        )

        attachment_list = attachments.scalars().all()

        if len(attachment_list) != len(payload.attachment_ids):
            raise HTTPException(400, "Invalid attachments")

        msg.attachments.extend(attachment_list)

    db.add(MessageRead(message_id=msg.id, user_id=current_user.id))

    chat = await db.get(ChatSession, chat_id)

    if not chat:
        raise HTTPException(404, "Chat not found")

    chat.last_message = payload.message if payload.message else "📎 Attachment"
    chat.last_message_at = datetime.utcnow()

    #  4. REDIS FAIL SAFETY
    try:
        redis = request.app.state.redis
        if redis:
            await redis.publish(
                f"chat:{chat_id}",
                json.dumps(
                    {
                        "type": "message",
                        "chat_id": chat_id,
                        "message": payload.message,
                        "sender": current_user.id,
                        "message_id": msg.id,
                        "mentions": [u.id for u in mentioned_users],
                        "parent_id": payload.parent_id,
                        "attachments": payload.attachment_ids,
                        "status": "sent",
                    }
                ),
            )
    except Exception:
        pass

    await db.refresh(msg)
    return msg


# =========================
# CHAT UPLOAD API
# =========================


@router.post("/chat")
async def upload_chat_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    # =========================
    # MIME VALIDATION
    # =========================

    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "Invalid file type")

    # =========================
    # READ FILE
    # =========================

    content = await file.read()

    # =========================
    # FILE SIZE VALIDATION
    # =========================

    if file.content_type in IMAGE_TYPES:
        if len(content) > MAX_IMAGE_SIZE:
            raise HTTPException(400, "Image exceeds 10MB limit")

    elif file.content_type in VIDEO_TYPES:
        if len(content) > MAX_VIDEO_SIZE:
            raise HTTPException(400, "Video exceeds 50MB limit")

    else:
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(400, "File exceeds 25MB limit")

    # =========================
    # FILE EXTENSION
    # =========================

    ext = Path(file.filename).suffix.lower()

    ALLOWED_EXTENSIONS = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".mp4",
        ".mov",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
    }

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, "Invalid file extension")

    # =========================
    # GENERATE UNIQUE NAME
    # =========================

    filename = f"{uuid.uuid4()}{ext}"

    # =========================
    # DIRECTORY
    # =========================

    now = datetime.utcnow()

    year = str(now.year)
    month = str(now.month).zfill(2)

    # =========================
    # IMAGE
    # =========================

    if file.content_type in IMAGE_TYPES:

        upload_dir = Path(f"uploads/chats/images/{year}/{month}")

        upload_dir.mkdir(parents=True, exist_ok=True)

        save_path = upload_dir / filename

        # =========================
        # IMAGE COMPRESSION
        # =========================

        image = Image.open(BytesIO(content))

        image.verify()

        image = Image.open(BytesIO(content))

        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")

        image.save(save_path, optimize=True, quality=75)

        file_url = f"/uploads/chats/images/{year}/{month}/{filename}"

    # =========================
    # VIDEO
    # =========================

    elif file.content_type in VIDEO_TYPES:

        upload_dir = Path(f"uploads/chats/videos/{year}/{month}")

        upload_dir.mkdir(parents=True, exist_ok=True)

        save_path = upload_dir / filename

        with open(save_path, "wb") as f:
            f.write(content)

        file_url = f"/uploads/chats/videos/{year}/{month}/{filename}"

    # =========================
    # DOCUMENTS
    # =========================

    else:

        upload_dir = Path(f"uploads/chats/files/{year}/{month}")

        upload_dir.mkdir(parents=True, exist_ok=True)

        save_path = upload_dir / filename

        with open(save_path, "wb") as f:
            f.write(content)

        file_url = f"/uploads/chats/files/{year}/{month}/{filename}"

    # =========================
    # SAVE DB RECORD
    # =========================

    attachment = MessageAttachment(
        file_url=file_url,
        file_name=file.filename,
        file_type=file.content_type,
        file_size=len(content),
        thumbnail_url=None,
        uploaded_by=current_user.id,
    )

    db.add(attachment)

    await db.commit()
    await db.refresh(attachment)

    # =========================
    # RESPONSE
    # =========================

    return {
        "attachment_id": attachment.id,
        "file_url": file_url,
        "file_name": file.filename,
        "file_type": file.content_type,
        "file_size": len(content),
    }


@router.post("/messages/{message_id}/delivered")
async def mark_delivered(
    request: Request,
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    msg = await db.get(ChatMessage, message_id)

    if not msg:
        raise HTTPException(404, "Message not found")

    await validate_membership(msg.chat_id, current_user.id, db)

    if msg.sender_id == current_user.id:
        return {"status": "ignored"}

    existing = await db.execute(
        select(MessageDelivery).where(
            MessageDelivery.message_id == message_id,
            MessageDelivery.user_id == current_user.id,
        )
    )

    delivery = existing.scalar()

    if delivery:

        if not delivery.delivered_at:
            delivery.delivered_at = datetime.utcnow()

    else:

        db.add(
            MessageDelivery(
                message_id=message_id,
                user_id=current_user.id,
                delivered_at=datetime.utcnow(),
            )
        )

    await db.commit()

    try:

        redis = request.app.state.redis

        if redis:
            await redis.publish(
                f"chat:{msg.chat_id}",
                json.dumps(
                    {
                        "type": "delivered",
                        "message_id": msg.id,
                        "user_id": current_user.id,
                    }
                ),
            )

    except Exception:
        pass

    return {"status": "delivered"}


@router.get("/{chat_id}/messages", response_model=list[MessageOut])
async def get_messages(
    request: Request,
    chat_id: int,
    limit: int = 20,
    cursor: int | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    if limit > 100:
        limit = 100

    await validate_membership(chat_id, current_user.id, db)

    Parent = aliased(ChatMessage)

    query = (
        select(ChatMessage, User, Parent)
        # HERE
        .options(selectinload(ChatMessage.attachments))
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

        messages.append(
            {
                "id": msg.id,
                "message": message_text,
                "created_at": msg.created_at,
                "is_delivered": False,
                "is_read": False,
                "parent_id": msg.parent_id,
                "chat_id": msg.chat_id,
                "sender_id": msg.sender_id,
                "sender": {"id": user.id, "name": user.full_name},
                "parent": (
                    {
                        "id": parent.id,
                        "message": (
                            "[deleted]"
                            if parent.is_deleted
                            else (parent.message if parent.message else "[attachment]")
                        ),
                    }
                    if parent
                    else None
                ),
                "is_deleted": msg.is_deleted,
                "is_pinned": msg.is_pinned,
                "is_edited": getattr(msg, "is_edited", False),
                "attachments": [
                    {
                        "id": a.id,
                        "file_url": a.file_url,
                        "file_name": a.file_name,
                        "file_type": a.file_type,
                        "file_size": a.file_size,
                        "thumbnail_url": a.thumbnail_url,
                    }
                    for a in msg.attachments
                ],
                "reactions": [],
                "read_by": [],
            }
        )

    # AUTO READ WHEN OPENING CHAT

    message_ids = [
        msg.id for msg in message_objects if msg.sender_id != current_user.id
    ][:50]

    if message_ids:

        existing_deliveries = await db.execute(
            select(MessageDelivery).where(
                MessageDelivery.user_id == current_user.id,
                MessageDelivery.message_id.in_(message_ids),
            )
        )

        delivery_map = {d.message_id: d for d in existing_deliveries.scalars().all()}

        new_deliveries = []

        for mid in message_ids:

            delivery = delivery_map.get(mid)

            if delivery:

                if not delivery.read_at:
                    delivery.read_at = datetime.utcnow()

                if not delivery.delivered_at:
                    delivery.delivered_at = datetime.utcnow()

            else:
                new_deliveries.append(
                    MessageDelivery(
                        message_id=mid,
                        user_id=current_user.id,
                        delivered_at=datetime.utcnow(),
                        read_at=datetime.utcnow(),
                    )
                )

        if new_deliveries:
            db.add_all(new_deliveries)

        existing_reads = await db.execute(
            select(MessageRead.message_id).where(
                MessageRead.user_id == current_user.id,
                MessageRead.message_id.in_(message_ids),
            )
        )

        already_read_ids = set(existing_reads.scalars().all())

        new_reads = [
            MessageRead(
                message_id=mid,
                user_id=current_user.id,
            )
            for mid in message_ids
            if mid not in already_read_ids
        ]

        if new_reads:
            db.add_all(new_reads)

        await db.commit()

        # realtime read event
        if new_reads:
            try:

                redis = request.app.state.redis

                if redis:
                    await redis.publish(
                        f"chat:{chat_id}",
                        json.dumps(
                            {
                                "type": "read",
                                "chat_id": chat_id,
                                "user_id": current_user.id,
                                "message_ids": message_ids,
                            }
                        ),
                    )

            except Exception:
                pass

    # batch read_by
    read_map = {}

    if message_objects:
        reads = await db.execute(
            select(MessageRead.message_id, MessageRead.user_id).where(
                MessageRead.message_id.in_([m.id for m in message_objects])
            )
        )

        for mid, uid in reads.all():
            read_map.setdefault(mid, []).append(uid)

    # inject read_by
    # batch reactions
    reaction_map = {}

    delivery_map = {}

    if message_objects:

        deliveries = await db.execute(
            select(
                MessageDelivery.message_id,
                func.count(MessageDelivery.id).label("delivery_count"),
                func.sum(
                    case(
                        (MessageDelivery.read_at.is_not(None), 1),
                        else_=0,
                    )
                ).label("read_count"),
            )
            .where(MessageDelivery.message_id.in_([m.id for m in message_objects]))
            .group_by(MessageDelivery.message_id)
        )

        for mid, delivery_count, read_count in deliveries.all():

            delivery_map[mid] = {
                "is_delivered": delivery_count > 0,
                "is_read": (read_count or 0) > 0,
            }

    if message_objects:
        reactions = await db.execute(
            select(
                MessageReaction.message_id,
                MessageReaction.user_id,
                MessageReaction.reaction,
            ).where(MessageReaction.message_id.in_([m.id for m in message_objects]))
        )

        for mid, uid, reaction in reactions.all():
            reaction_map.setdefault(mid, []).append(
                {
                    "user_id": uid,
                    "reaction": reaction,
                    "is_reacted_by_me": uid == current_user.id,
                }
            )

    # batch reply counts
    reply_map = {}

    if message_objects:

        reply_counts = await db.execute(
            select(ChatMessage.parent_id, func.count(ChatMessage.id))
            .where(ChatMessage.parent_id.in_([m.id for m in message_objects]))
            .group_by(ChatMessage.parent_id)
        )

        for pid, count in reply_counts.all():
            reply_map[pid] = count

    for item in messages:
        item["read_by"] = read_map.get(item["id"], [])

        item["reactions"] = reaction_map.get(item["id"], [])

        item["reply_count"] = reply_map.get(item["id"], 0)

        delivery = delivery_map.get(item["id"])

        if delivery:
            item["is_delivered"] = delivery["is_delivered"]
            item["is_read"] = delivery["is_read"]

    return messages


@router.get("/messages/{message_id}/replies", response_model=list[ReplyOut])
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
    await validate_membership(parent.chat_id, current_user.id, db)

    result = await db.execute(
        select(ChatMessage, User)
        .options(selectinload(ChatMessage.attachments))
        .join(User, ChatMessage.sender_id == User.id)
        .where(ChatMessage.parent_id == message_id)
        .order_by(ChatMessage.created_at.asc())
    )

    rows = result.all()

    replies = []

    for msg, user in rows:
        replies.append(
            {
                "id": msg.id,
                "message": "[deleted]" if msg.is_deleted else msg.message,
                "created_at": msg.created_at,
                "sender": {"id": user.id, "name": user.full_name},
                "attachments": [
                    {
                        "id": a.id,
                        "file_url": a.file_url,
                        "file_name": a.file_name,
                        "file_type": a.file_type,
                        "file_size": a.file_size,
                        "thumbnail_url": a.thumbnail_url,
                    }
                    for a in msg.attachments
                ],
                "is_deleted": msg.is_deleted,
                "is_edited": getattr(msg, "is_edited", False),
            }
        )

    return replies


@router.get("/pinned", response_model=list[ChatListEnhancedOut])
async def get_pinned_chats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    result = await db.execute(
        select(
            ChatSession,
            ChatMember,
        )
        .join(ChatMember, ChatMember.chat_id == ChatSession.id)
        .where(
            ChatMember.user_id == current_user.id,
            ChatMember.is_pinned == True,
        )
        .order_by(ChatMember.pinned_at.desc())
    )

    rows = result.all()

    private_chat_ids = [
        chat.id for chat, member in rows if chat.type == ChatType.PRIVATE
    ]

    private_users_map = {}

    if private_chat_ids:

        private_users = await db.execute(
            select(
                ChatMember.chat_id,
                User.id,
                User.full_name,
                User.profile_image,
            )
            .join(User, ChatMember.user_id == User.id)
            .where(
                ChatMember.chat_id.in_(private_chat_ids),
                User.id != current_user.id,
            )
        )

        for chat_id, uid, name, avatar in private_users.all():

            private_users_map[chat_id] = {
                "id": uid,
                "name": name,
                "avatar": avatar,
            }

    chats = []

    for chat, member in rows:

        other_user_id = None
        other_user_name = None
        other_user_avatar = None

        if chat.type == ChatType.PRIVATE:

            other_user = private_users_map.get(chat.id)

            if other_user:
                other_user_id = other_user["id"]
                other_user_name = other_user["name"]
                other_user_avatar = other_user["avatar"]

        chats.append(
            {
                "id": chat.id,
                "type": chat.type.value,
                "name": chat.name,
                "avatar_url": chat.avatar_url,
                "other_user_id": other_user_id,
                "other_user_name": other_user_name,
                "other_user_avatar": other_user_avatar,
                "last_message": chat.last_message,
                "last_message_at": chat.last_message_at,
                "unread_count": 0,
                "is_pinned": member.is_pinned,
            }
        )

    return chats


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
            (ChatMessage.id == MessageRead.message_id)
            & (MessageRead.user_id == current_user.id),
        )
        .where(
            ChatMessage.chat_id == chat_id,
            ChatMessage.sender_id != current_user.id,
            ChatMessage.is_deleted == False,
            MessageRead.id.is_(None),
        )
    )

    return {"unread": count}


@router.get("/messages/{message_id}/reads", response_model=list[MessageReadUserOut])
async def get_message_reads(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    msg = await db.get(ChatMessage, message_id)

    if not msg:
        raise HTTPException(404, "Message not found")

    await validate_membership(msg.chat_id, current_user.id, db)

    result = await db.execute(
        select(MessageRead, User)
        .join(User, MessageRead.user_id == User.id)
        .where(MessageRead.message_id == message_id)
        .order_by(MessageRead.read_at.asc())
    )

    rows = result.all()

    output = []

    for read, user in rows:

        output.append(
            {
                "user_id": user.id,
                "full_name": user.full_name,
                "profile_image": user.profile_image,
                "read_at": read.read_at,
            }
        )

    return output


@router.post("/group")
async def create_group(
    payload: CreateGroup,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    member_ids = payload.member_ids
    name = payload.name

    if current_user.id not in member_ids:
        member_ids.append(current_user.id)

    # VALIDATE USERS EXIST
    users = await db.execute(select(User.id).where(User.id.in_(member_ids)))

    valid_ids = set(users.scalars().all())

    invalid = set(member_ids) - valid_ids

    if invalid:
        raise HTTPException(400, f"Invalid users: {list(invalid)}")

    chat = ChatSession(type=ChatType.GROUP, name=name, created_by=current_user.id)

    db.add(chat)
    await db.flush()

    for uid in set(member_ids):
        role = MemberRole.ADMIN if uid == current_user.id else MemberRole.MEMBER

        db.add(ChatMember(chat_id=chat.id, user_id=uid, role=role))

    await db.commit()

    return {"chat_id": chat.id}


@router.get("/users", response_model=list[ChatUserOut])
async def get_chat_users(
    request: Request,
    search: str | None = None,
    role: str | None = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    if limit > 100:
        limit = 100

    query = select(User).where(
        User.is_deleted == False,
        User.is_active == True,
        User.id != current_user.id,
    )

    if search:
        like = f"%{search.strip()}%"

        query = query.where(
            or_(
                User.full_name.ilike(like),
                User.mobile.ilike(like),
                User.email.ilike(like),
            )
        )

    if role:
        query = query.where(User.role == role)

    query = query.order_by(User.full_name.asc()).limit(limit)

    result = await db.execute(query)

    users = result.scalars().all()

    output = []

    redis = getattr(request.app.state, "redis", None)

    for user in users:

        is_online = False
        last_seen = None

        try:
            if redis:

                online = await redis.get(f"user:{user.id}:online")

                if online:
                    is_online = True

                ls = await redis.get(f"user:{user.id}:last_seen")

                if ls:
                    last_seen = ls.decode()

        except Exception:
            pass

        output.append(
            {
                "user_id": user.id,
                "full_name": user.full_name,
                "role": user.role,
                "designation": user.designation,
                "profile_image": user.profile_image,
                "mobile_number": user.mobile,
                "is_online": is_online,
                "last_seen": last_seen,
            }
        )

    return output


@router.get("/search-users", response_model=list[ChatUserSearchOut])
async def search_chat_users(
    q: str,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    if limit > 100:
        limit = 100

    q = q.strip()

    if len(q) < 1:
        return []

    like = f"%{q}%"

    result = await db.execute(
        select(User)
        .where(
            User.is_deleted == False,
            User.is_active == True,
            User.id != current_user.id,
            or_(
                User.full_name.ilike(like),
                User.mobile.ilike(like),
                User.email.ilike(like),
            ),
        )
        .order_by(User.full_name.asc())
        .limit(limit)
    )

    users = result.scalars().all()

    return [
        {
            "user_id": user.id,
            "full_name": user.full_name,
            "role": user.role,
            "profile_image": user.profile_image,
        }
        for user in users
    ]


@router.get("/enhanced", response_model=list[ChatListEnhancedOut])
async def get_enhanced_chat_list(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    unread_subquery = (
        select(
            ChatMessage.chat_id.label("chat_id"),
            func.count(ChatMessage.id).label("unread_count"),
        )
        .outerjoin(
            MessageRead,
            (ChatMessage.id == MessageRead.message_id)
            & (MessageRead.user_id == current_user.id),
        )
        .where(
            ChatMessage.sender_id != current_user.id,
            ChatMessage.is_deleted == False,
            MessageRead.id.is_(None),
        )
        .group_by(ChatMessage.chat_id)
        .subquery()
    )

    result = await db.execute(
        select(
            ChatSession.id,
            ChatSession.type,
            ChatSession.name,
            ChatSession.avatar_url,
            ChatSession.last_message,
            ChatSession.last_message_at,
            ChatMember.is_pinned,
            func.coalesce(unread_subquery.c.unread_count, 0).label("unread_count"),
        )
        .join(ChatMember, ChatMember.chat_id == ChatSession.id)
        .outerjoin(
            unread_subquery,
            unread_subquery.c.chat_id == ChatSession.id,
        )
        .where(
            ChatMember.user_id == current_user.id,
            ChatMember.is_deleted == False,
        )
        .order_by(
            ChatMember.is_pinned.desc(),
            ChatMember.pinned_at.desc(),
            ChatSession.last_message_at.desc(),
        )
    )

    rows = result.all()

    private_chat_ids = [row.id for row in rows if row.type == ChatType.PRIVATE]

    private_users_map = {}

    if private_chat_ids:

        private_users = await db.execute(
            select(
                ChatMember.chat_id,
                User.id,
                User.full_name,
                User.profile_image,
            )
            .join(User, ChatMember.user_id == User.id)
            .where(
                ChatMember.chat_id.in_(private_chat_ids),
                User.id != current_user.id,
            )
        )

        for chat_id, uid, name, avatar in private_users.all():

            private_users_map[chat_id] = {
                "id": uid,
                "name": name,
                "avatar": avatar,
            }

    chats = []

    for row in rows:

        other_user_id = None
        other_user_name = None
        other_user_avatar = None

        if row.type == ChatType.PRIVATE:

            other_user = private_users_map.get(row.id)

            if other_user:
                other_user_id = other_user["id"]
                other_user_name = other_user["name"]
                other_user_avatar = other_user["avatar"]

        chats.append(
            {
                "id": row.id,
                "type": row.type.value,
                "name": row.name,
                "avatar_url": row.avatar_url,
                "other_user_id": other_user_id,
                "other_user_name": other_user_name,
                "other_user_avatar": other_user_avatar,
                "last_message": row.last_message,
                "last_message_at": row.last_message_at,
                "unread_count": row.unread_count or 0,
                "is_pinned": row.is_pinned,
            }
        )

    return chats


@router.delete("/{chat_id}")
async def delete_chat(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    result = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == current_user.id,
            ChatMember.is_deleted == False,
        )
    )

    member = result.scalar()

    if not member:
        raise HTTPException(404, "Chat not found")

    member.is_deleted = True
    member.deleted_at = datetime.utcnow()

    await db.commit()

    return {"status": "deleted"}


@router.post("/{chat_id}/restore")
async def restore_chat(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    result = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == current_user.id,
        )
    )

    member = result.scalar()

    if not member:
        raise HTTPException(404, "Chat not found")

    member.is_deleted = False
    member.deleted_at = None

    await db.commit()

    return {"status": "restored"}


@router.post("/group/{chat_id}/members")
async def add_multiple_members(
    chat_id: int,
    payload: ChatMemberAddPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    await validate_group(chat_id, db)

    await validate_admin(chat_id, current_user.id, db)

    member_ids = list(set(payload.member_ids))

    if not member_ids:
        raise HTTPException(400, "member_ids required")

    users = await db.execute(
        select(User.id).where(
            User.id.in_(member_ids),
            User.is_deleted == False,
            User.is_active == True,
        )
    )

    valid_user_ids = set(users.scalars().all())

    invalid_ids = set(member_ids) - valid_user_ids

    if invalid_ids:
        raise HTTPException(400, f"Invalid users: {list(invalid_ids)}")

    existing = await db.execute(
        select(ChatMember.user_id).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id.in_(member_ids),
        )
    )

    existing_ids = set(existing.scalars().all())

    added = []

    for uid in member_ids:

        if uid in existing_ids:
            continue

        db.add(
            ChatMember(
                chat_id=chat_id,
                user_id=uid,
                role=MemberRole.MEMBER,
            )
        )

        added.append(uid)

    await db.commit()

    return {
        "status": "success",
        "added_members": added,
        "already_exists": list(existing_ids),
    }


@router.delete("/group/{chat_id}/members")
async def remove_multiple_members(
    chat_id: int,
    payload: ChatMemberAddPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    await validate_group(chat_id, db)

    await validate_admin(chat_id, current_user.id, db)

    removed = []

    for uid in payload.member_ids:

        member_result = await db.execute(
            select(ChatMember).where(
                ChatMember.chat_id == chat_id,
                ChatMember.user_id == uid,
            )
        )

        member = member_result.scalar()

        if not member:
            continue

        if member.role == MemberRole.ADMIN:
            continue

        await db.delete(member)

        removed.append(uid)

    await db.commit()

    return {
        "status": "success",
        "removed_members": removed,
    }


@router.get("/messages/mentions", response_model=list[MentionMessageOut])
async def get_mentions(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    if limit > 100:
        limit = 100

    result = await db.execute(
        select(ChatMessage, User)
        .options(selectinload(ChatMessage.attachments))
        .join(MessageMention, MessageMention.message_id == ChatMessage.id)
        .join(User, ChatMessage.sender_id == User.id)
        .join(ChatMember, ChatMember.chat_id == ChatMessage.chat_id)
        .where(
            MessageMention.mentioned_user_id == current_user.id,
            ChatMember.user_id == current_user.id,
            ChatMember.is_deleted == False,
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )

    rows = result.all()

    output = []

    for msg, sender in rows:

        output.append(
            {
                "id": msg.id,
                "chat_id": msg.chat_id,
                "message": msg.message,
                "sender_id": msg.sender_id,
                "created_at": msg.created_at,
                "sender": {
                    "id": sender.id,
                    "name": sender.full_name,
                },
                "attachments": [
                    {
                        "id": a.id,
                        "file_url": a.file_url,
                        "file_name": a.file_name,
                        "file_type": a.file_type,
                        "file_size": a.file_size,
                        "thumbnail_url": a.thumbnail_url,
                    }
                    for a in msg.attachments
                ],
            }
        )

    return output


@router.get("/{chat_id}/mention-users")
async def mention_users(
    chat_id: int,
    q: str = "",
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    if limit > 100:
        limit = 100

    await validate_membership(chat_id, current_user.id, db)

    query = (
        select(User)
        .join(ChatMember, ChatMember.user_id == User.id)
        .where(
            ChatMember.chat_id == chat_id,
            User.id != current_user.id,
        )
    )

    if q:

        like = f"%{q.strip()}%"

        query = query.where(
            or_(
                User.full_name.ilike(like),
                User.mobile.ilike(like),
            )
        )

    query = query.order_by(User.full_name.asc()).limit(limit)

    result = await db.execute(query)

    users = result.scalars().all()

    return {
        "items": [
            {
                "user_id": user.id,
                "full_name": user.full_name,
                "profile_image": user.profile_image,
            }
            for user in users
        ]
    }


@router.post("/group/{chat_id}/add")
async def add_member(
    chat_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_group(chat_id, db)

    await validate_admin(chat_id, current_user.id, db)

    target = await db.get(User, user_id)

    if not target:
        raise HTTPException(404, "User not found")

    existing = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id, ChatMember.user_id == user_id
        )
    )

    if existing.scalar():
        return {"status": "already exists"}

    db.add(ChatMember(chat_id=chat_id, user_id=user_id, role=MemberRole.MEMBER))

    return {"status": "added"}


@router.post("/group/{chat_id}/remove")
async def remove_member(
    chat_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    #  only admin can remove
    await validate_group(chat_id, db)

    chat = await db.get(ChatSession, chat_id)

    if user_id == chat.created_by:
        raise HTTPException(400, "Cannot remove group creator")

    # only admin can remove
    await validate_admin(chat_id, current_user.id, db)

    #  prevent admin removing themselves via this API
    if user_id == current_user.id:
        raise HTTPException(400, "Use leave API instead")

    obj = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id, ChatMember.user_id == user_id
        )
    )
    member = obj.scalar()

    if not member:
        raise HTTPException(404, "Member not found")

    # PREVENT REMOVING LAST ADMIN
    if member.role == MemberRole.ADMIN:

        admins = await db.execute(
            select(ChatMember).where(
                ChatMember.chat_id == chat_id, ChatMember.role == MemberRole.ADMIN
            )
        )

        admin_list = admins.scalars().all()

        if len(admin_list) == 1:
            raise HTTPException(400, "Cannot remove last admin")

    await db.delete(member)

    return {"status": "removed"}


@router.get("/group/{chat_id}/members")
async def group_members(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_group(chat_id, db)

    await validate_membership(chat_id, current_user.id, db)

    result = await db.execute(
        select(ChatMember, User)
        .join(User, ChatMember.user_id == User.id)
        .where(
            ChatMember.chat_id == chat_id,
            ChatMember.is_deleted == False,
        )
        .order_by(ChatMember.joined_at.asc())
    )

    rows = result.all()

    members = []

    for member, user in rows:
        members.append(
            {
                "user_id": user.id,
                "name": user.full_name,
                "role": member.role.value,
                "joined_at": member.joined_at,
            }
        )

    return members


@router.put("/group/{chat_id}")
async def update_group(
    chat_id: int,
    name: str | None = None,
    avatar_url: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_group(chat_id, db)

    await validate_admin(chat_id, current_user.id, db)

    chat = await db.get(ChatSession, chat_id)

    if not chat:
        raise HTTPException(404, "Chat not found")

    if name:
        chat.name = name.strip()

    if avatar_url:
        chat.avatar_url = avatar_url

    await db.commit()

    return {"status": "updated"}


@router.get("/{chat_id}", response_model=ChatInfoOut)
async def get_chat_info(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_membership(chat_id, current_user.id, db)

    chat = await db.get(ChatSession, chat_id)

    if not chat:
        raise HTTPException(404, "Chat not found")

    member_count = await db.scalar(
        select(func.count())
        .select_from(ChatMember)
        .where(
            ChatMember.chat_id == chat_id,
            ChatMember.is_deleted == False,
        )
    )

    member = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id, ChatMember.user_id == current_user.id
        )
    )

    current_member = member.scalar()

    return {
        "id": chat.id,
        "type": chat.type.value,
        "name": chat.name,
        "avatar_url": chat.avatar_url,
        "created_by": chat.created_by,
        "created_at": chat.created_at,
        "member_count": member_count,
        "last_message": chat.last_message,
        "last_message_at": chat.last_message_at,
        "is_muted": current_member.is_muted,
        "is_archived": current_member.is_archived,
        "is_pinned": current_member.is_pinned,
    }


@router.post("/{chat_id}/mute")
async def mute_chat(
    chat_id: int,
    muted: bool,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    member = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id, ChatMember.user_id == current_user.id
        )
    )

    obj = member.scalar()

    if not obj:
        raise HTTPException(404, "Not part of chat")

    obj.is_muted = muted

    await db.commit()

    return {"status": "updated", "is_muted": muted}


@router.post("/{chat_id}/archive")
async def archive_chat(
    chat_id: int,
    archived: bool,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    member = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id, ChatMember.user_id == current_user.id
        )
    )

    obj = member.scalar()

    if not obj:
        raise HTTPException(404, "Not part of chat")

    obj.is_archived = archived

    await db.commit()

    return {"status": "updated", "is_archived": archived}


@router.post("/{chat_id}/typing")
async def typing(
    request: Request,
    chat_id: int,
    is_typing: bool,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_membership(chat_id, current_user.id, db)
    redis = getattr(request.app.state, "redis", None)

    if redis:
        # store typing with expiry
        if is_typing:
            await redis.set(f"chat:{chat_id}:typing:{current_user.id}", 1, ex=5)
        else:
            await redis.delete(f"chat:{chat_id}:typing:{current_user.id}")

        await redis.publish(
            f"chat:{chat_id}",
            json.dumps(
                {"type": "typing", "user": current_user.id, "is_typing": is_typing}
            ),
        )

    return {"ok": True}


@router.get("/{chat_id}/typing-users")
async def typing_users(
    chat_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_membership(chat_id, current_user.id, db)

    redis = getattr(request.app.state, "redis", None)

    if not redis:
        return {"users": []}

    result = await db.execute(
        select(ChatMember.user_id, User.full_name)
        .join(User, ChatMember.user_id == User.id)
        .where(ChatMember.chat_id == chat_id)
    )

    rows = result.all()

    users = []

    pipe = redis.pipeline()

    for uid, _ in rows:
        pipe.get(f"chat:{chat_id}:typing:{uid}")

    typing_results = await pipe.execute()

    for index, (uid, name) in enumerate(rows):

        typing = typing_results[index]

        if typing and uid != current_user.id:
            users.append(
                {
                    "user_id": uid,
                    "name": name or "Unknown User",
                }
            )

    return {"users": users}


@router.get("/", response_model=list[ChatListOut])
async def get_chat_list(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    unread_subquery = (
        select(
            ChatMessage.chat_id.label("chat_id"),
            func.count(ChatMessage.id).label("unread_count"),
        )
        .outerjoin(
            MessageRead,
            (ChatMessage.id == MessageRead.message_id)
            & (MessageRead.user_id == current_user.id),
        )
        .where(
            ChatMessage.sender_id != current_user.id,
            ChatMessage.is_deleted == False,
            MessageRead.id.is_(None),
        )
        .group_by(ChatMessage.chat_id)
        .subquery()
    )

    result = await db.execute(
        select(
            ChatSession.id,
            ChatSession.name,
            ChatSession.last_message,
            ChatSession.last_message_at,
            ChatMember.is_pinned,
            func.coalesce(unread_subquery.c.unread_count, 0).label("unread_count"),
        )
        .join(ChatMember, ChatMember.chat_id == ChatSession.id)
        .outerjoin(
            unread_subquery,
            unread_subquery.c.chat_id == ChatSession.id,
        )
        .where(
            ChatMember.user_id == current_user.id,
            ChatMember.is_deleted == False,
        )
        .order_by(
            ChatMember.is_pinned.desc(),
            ChatMember.pinned_at.desc(),
            ChatSession.last_message_at.desc(),
        )
    )

    rows = result.all()

    chats = []

    for row in rows:

        chats.append(
            {
                "id": row.id,
                "name": row.name,
                "last_message": row.last_message,
                "last_message_at": row.last_message_at,
                "unread_count": row.unread_count or 0,
                "is_pinned": row.is_pinned,
            }
        )

    return chats


@router.post("/group/{chat_id}/kick")
async def kick_member(
    chat_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    await validate_group(chat_id, db)

    chat = await db.get(ChatSession, chat_id)

    if user_id == chat.created_by:
        raise HTTPException(400, "Cannot remove group creator")

    await validate_admin(chat_id, current_user.id, db)

    obj = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id, ChatMember.user_id == user_id
        )
    )
    member = obj.scalar()

    if not member:
        raise HTTPException(404, "User not in group")

    # PREVENT REMOVING LAST ADMIN
    if member.role == MemberRole.ADMIN:

        admins = await db.execute(
            select(ChatMember).where(
                ChatMember.chat_id == chat_id, ChatMember.role == MemberRole.ADMIN
            )
        )

        admin_list = admins.scalars().all()

        if len(admin_list) == 1:
            raise HTTPException(400, "Cannot remove last admin")

    await db.delete(member)

    return {"status": "kicked"}


@router.post("/group/{chat_id}/leave")
async def leave_group(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    await validate_group(chat_id, db)
    obj = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id, ChatMember.user_id == current_user.id
        )
    )
    member = obj.scalar()

    if not member:
        raise HTTPException(404, "Not part of group")

    #  if admin leaving → handle admin transfer
    if member.role == MemberRole.ADMIN:
        admins = await db.execute(
            select(ChatMember).where(
                ChatMember.chat_id == chat_id, ChatMember.role == MemberRole.ADMIN
            )
        )
        admin_list = admins.scalars().all()

        #  if this is last admin → promote someone else
        if len(admin_list) == 1:
            others = await db.execute(
                select(ChatMember).where(
                    ChatMember.chat_id == chat_id, ChatMember.user_id != current_user.id
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
    await db.flush()
    #  ADD THIS BLOCK
    remaining = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id != current_user.id,
        )
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
    await validate_group(chat_id, db)
    #  ensure current user is admin
    await validate_admin(chat_id, current_user.id, db)

    #  fetch new admin
    result = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id, ChatMember.user_id == new_admin_id
        )
    )
    new_admin = result.scalar()

    if not new_admin:
        raise HTTPException(404, "User not in group")

    #  fetch current admin record
    current = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id, ChatMember.user_id == current_user.id
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
            MessageReaction.user_id == current_user.id,
        )
    )
    existing = result.scalar()

    if existing:
        if existing.reaction == reaction:
            await db.delete(existing)
            await db.commit()
            return {"status": "removed"}

        existing.reaction = reaction
    else:
        db.add(
            MessageReaction(
                message_id=message_id,
                user_id=current_user.id,
                reaction=reaction,
            )
        )

    await db.commit()

    # realtime event
    redis = getattr(request.app.state, "redis", None)
    if redis:
        await redis.publish(
            f"chat:{msg.chat_id}",
            json.dumps(
                {
                    "type": "reaction",
                    "message_id": message_id,
                    "user_id": current_user.id,
                    "reaction": reaction,
                }
            ),
        )

    return {"status": "reacted"}


@router.put("/messages/{message_id}/edit")
async def edit_message(
    request: Request,  #  ADD THIS
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

    # EDIT LIMIT
    if datetime.utcnow() - msg.created_at > timedelta(minutes=15):
        raise HTTPException(403, "Edit time expired")

    # EMPTY VALIDATION
    if not new_text.strip():
        raise HTTPException(400, "Message required")

    msg.message = new_text.strip()
    msg.is_edited = True

    #  REAL-TIME edit event
    redis = getattr(request.app.state, "redis", None)
    if redis:
        await redis.publish(
            f"chat:{msg.chat_id}",
            json.dumps({"type": "edit", "message_id": msg.id, "new_text": new_text}),
        )

    return {"status": "edited"}


@router.delete("/messages/{message_id}")
async def delete_message(
    request: Request,  #  ADD THIS
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    msg = await db.get(ChatMessage, message_id)

    if not msg:
        raise HTTPException(404, "ChatMessage not found")

    await validate_membership(msg.chat_id, current_user.id, db)

    if msg.sender_id != current_user.id:
        raise HTTPException(403, "Not allowed")

    # DELETE LIMIT
    if datetime.utcnow() - msg.created_at > timedelta(minutes=60):
        raise HTTPException(403, "Delete time expired")

    #  soft delete
    msg.is_deleted = True

    #  REAL-TIME delete event
    redis = getattr(request.app.state, "redis", None)
    if redis:
        await redis.publish(
            f"chat:{msg.chat_id}", json.dumps({"type": "delete", "message_id": msg.id})
        )

    return {"status": "deleted"}


@router.get("/users/{user_id}/status")
async def user_status(user_id: int, request: Request):
    redis = getattr(request.app.state, "redis", None)

    if not redis:
        return {"online": False, "last_seen": None}

    online = await redis.get(f"user:{user_id}:online")
    last_seen = await redis.get(f"user:{user_id}:last_seen")

    return {
        "online": bool(online),
        "last_seen": last_seen.decode() if last_seen else None,
    }


@router.get("/{chat_id}/search", response_model=list[MessageOut])
async def search_messages(
    chat_id: int,
    query: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_membership(chat_id, current_user.id, db)

    Parent = aliased(ChatMessage)

    clean_query = query.strip()

    clean_query = clean_query.replace("@", " ")
    clean_query = clean_query.replace("+", " ")
    clean_query = clean_query.replace("-", " ")
    clean_query = clean_query.replace("*", " ")
    clean_query = clean_query.replace('"', " ")

    clean_query = " ".join(clean_query.split())

    search_query = f"{clean_query}*"

    if len(clean_query) < 3:
        condition = ChatMessage.message.ilike(f"%{clean_query}%")
    else:
        condition = text("MATCH(message) AGAINST(:q IN BOOLEAN MODE)")

    query_stmt = (
        select(ChatMessage, User, Parent)
        .options(selectinload(ChatMessage.attachments))
        .join(User, ChatMessage.sender_id == User.id)
        .outerjoin(Parent, ChatMessage.parent_id == Parent.id)
        .where(ChatMessage.chat_id == chat_id, condition)
        .order_by(ChatMessage.created_at.desc())
        .limit(50)
    )

    if len(clean_query) >= 3:
        query_stmt = query_stmt.params(q=f"{clean_query}*")

    result = await db.execute(query_stmt)

    rows = result.all()

    messages = []

    for msg, user, parent in rows:

        messages.append(
            {
                "id": msg.id,
                "chat_id": msg.chat_id,
                "message": ("[deleted]" if msg.is_deleted else msg.message),
                "sender_id": msg.sender_id,
                "created_at": msg.created_at,
                "is_delivered": False,
                "is_read": False,
                "parent_id": msg.parent_id,
                "sender": {
                    "id": user.id,
                    "name": user.full_name,
                },
                "parent": (
                    {
                        "id": parent.id,
                        "message": (
                            "[deleted]"
                            if parent.is_deleted
                            else (parent.message if parent.message else "[attachment]")
                        ),
                    }
                    if parent
                    else None
                ),
                "is_deleted": msg.is_deleted,
                "is_edited": msg.is_edited,
                "is_pinned": msg.is_pinned,
                "attachments": [
                    {
                        "id": a.id,
                        "file_url": a.file_url,
                        "file_name": a.file_name,
                        "file_type": a.file_type,
                        "file_size": a.file_size,
                        "thumbnail_url": a.thumbnail_url,
                    }
                    for a in msg.attachments
                ],
                "reply_count": 0,
                "read_by": [],
                "reactions": [],
            }
        )

    return messages


@router.post("/messages/{message_id}/pin")
async def pin_message(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    msg = await db.get(ChatMessage, message_id)

    if not msg:
        raise HTTPException(404, "ChatMessage not found")

    await validate_membership(msg.chat_id, current_user.id, db)

    chat = await db.get(ChatSession, msg.chat_id)

    # group → admin only
    if chat and chat.type == ChatType.GROUP:
        await validate_admin(msg.chat_id, current_user.id, db)

    else:
        if msg.sender_id != current_user.id:
            raise HTTPException(403, "Not allowed")

    if msg.is_pinned:
        return {"status": "already pinned"}

    pin_count = await db.scalar(
        select(func.count())
        .select_from(ChatMessage)
        .where(
            ChatMessage.chat_id == msg.chat_id,
            ChatMessage.is_pinned == True,
        )
    )

    if pin_count >= 3:
        raise HTTPException(status_code=400, detail="Maximum 3 pinned messages allowed")

    msg.is_pinned = True

    await db.commit()

    return {"status": "pinned"}


@router.get("/{chat_id}/pinned", response_model=list[MessageOut])
async def pinned_messages(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await validate_membership(chat_id, current_user.id, db)

    result = await db.execute(
        select(ChatMessage, User)
        .options(selectinload(ChatMessage.attachments))
        .join(User, ChatMessage.sender_id == User.id)
        .where(ChatMessage.chat_id == chat_id, ChatMessage.is_pinned == True)
        .order_by(ChatMessage.created_at.desc())
    )
    rows = result.all()

    messages = []

    for msg, user in rows:
        messages.append(
            {
                "id": msg.id,
                "chat_id": msg.chat_id,
                "message": ("[deleted]" if msg.is_deleted else msg.message),
                "sender_id": msg.sender_id,
                "created_at": msg.created_at,
                "is_delivered": False,
                "is_read": False,
                "parent_id": msg.parent_id,
                "is_deleted": msg.is_deleted,
                "is_edited": msg.is_edited,
                "is_pinned": msg.is_pinned,
                "attachments": [
                    {
                        "id": a.id,
                        "file_url": a.file_url,
                        "file_name": a.file_name,
                        "file_type": a.file_type,
                        "file_size": a.file_size,
                        "thumbnail_url": a.thumbnail_url,
                    }
                    for a in msg.attachments
                ],
                "sender": {"id": user.id, "name": user.full_name or "Unknown User"},
                "parent": None,
                "reactions": [],
                "read_by": [],
                "reply_count": 0,
            }
        )

    return messages


@router.post("/messages/{message_id}/unpin")
async def unpin_message(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    msg = await db.get(ChatMessage, message_id)

    if not msg:
        raise HTTPException(404, "ChatMessage not found")

    await validate_membership(msg.chat_id, current_user.id, db)

    chat = await db.get(ChatSession, msg.chat_id)

    # group → admin only
    if chat and chat.type == ChatType.GROUP:
        await validate_admin(msg.chat_id, current_user.id, db)

    else:
        if msg.sender_id != current_user.id:
            raise HTTPException(403, "Not allowed")

    if not msg.is_pinned:
        return {"status": "already unpinned"}

    msg.is_pinned = False

    await db.commit()

    return {"status": "unpinned"}


@router.post("/{chat_id}/pin")
async def pin_chat(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    result = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == current_user.id,
        )
    )

    member = result.scalar()

    if not member:
        raise HTTPException(404, "Chat not found")

    member.is_pinned = True
    member.pinned_at = datetime.utcnow()

    await db.commit()

    return {"status": "pinned"}


@router.post("/{chat_id}/unpin")
async def unpin_chat(
    chat_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    result = await db.execute(
        select(ChatMember).where(
            ChatMember.chat_id == chat_id,
            ChatMember.user_id == current_user.id,
        )
    )

    member = result.scalar()

    if not member:
        raise HTTPException(404, "Chat not found")

    member.is_pinned = False
    member.pinned_at = None

    await db.commit()

    return {"status": "unpinned"}


@router.post("/messages/{message_id}/forward")
async def forward_message(
    message_id: int,
    target_chat_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    # original message
    result = await db.execute(
        select(ChatMessage)
        .options(selectinload(ChatMessage.attachments))
        .where(ChatMessage.id == message_id)
    )

    original = result.scalar_one_or_none()

    if not original:
        raise HTTPException(404, "Original message not found")

    # must belong to source chat
    await validate_membership(original.chat_id, current_user.id, db)

    # must belong to target chat
    await validate_membership(target_chat_id, current_user.id, db)

    # cannot forward deleted message
    if original.is_deleted:
        raise HTTPException(400, "Cannot forward deleted message")

    # create forwarded message
    forwarded = ChatMessage(
        chat_id=target_chat_id,
        sender_id=current_user.id,
        message=original.message,
        is_forwarded=True,
        forwarded_from_message_id=original.id,
    )

    db.add(forwarded)
    await db.flush()

    for a in original.attachments:
        copied = MessageAttachment(
            file_url=a.file_url,
            file_name=a.file_name,
            file_type=a.file_type,
            file_size=a.file_size,
            thumbnail_url=a.thumbnail_url,
            uploaded_by=current_user.id,
        )

        db.add(copied)

        forwarded.attachments.append(copied)

    # self read
    db.add(MessageRead(message_id=forwarded.id, user_id=current_user.id))

    # update chat last message
    chat = await db.get(ChatSession, target_chat_id)

    if chat:
        chat.last_message = (
            original.message if original.message else "📎 Forwarded Attachment"
        )
        chat.last_message_at = datetime.utcnow()

    # realtime websocket event
    redis = getattr(request.app.state, "redis", None)

    if redis:
        await redis.publish(
            f"chat:{target_chat_id}",
            json.dumps(
                {
                    "type": "forward",
                    "chat_id": target_chat_id,
                    "message_id": forwarded.id,
                    "sender": current_user.id,
                    "forwarded_from_message_id": original.id,
                    "message": original.message,
                    "attachments": [
                        {
                            "id": a.id,
                            "file_url": a.file_url,
                            "file_name": a.file_name,
                            "file_type": a.file_type,
                            "file_size": a.file_size,
                            "thumbnail_url": a.thumbnail_url,
                        }
                        for a in original.attachments
                    ],
                }
            ),
        )

    await db.commit()

    return {"status": "forwarded", "message_id": forwarded.id}


@router.get("/{chat_id}/active-users")
async def active_users(
    chat_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):

    await validate_membership(chat_id, current_user.id, db)

    redis = getattr(request.app.state, "redis", None)

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

    redis = getattr(request.app.state, "redis", None)

    if not redis:
        return []

    result = await db.execute(
        select(ChatMember.user_id).where(ChatMember.chat_id == chat_id)
    )

    user_ids = result.scalars().all()

    states = []

    pipe = redis.pipeline()

    for uid in user_ids:
        pipe.get(f"user:{uid}:online")
        pipe.get(f"user:{uid}:last_seen")

    results = await pipe.execute()

    states = []

    for i, uid in enumerate(user_ids):
        online = results[i * 2]
        last_seen = results[i * 2 + 1]

        states.append(
            {
                "user_id": uid,
                "online": bool(online),
                "last_seen": last_seen.decode() if last_seen else None,
            }
        )

    return states

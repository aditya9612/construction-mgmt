import asyncio
from datetime import datetime, timedelta, UTC
from pathlib import Path
import os

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.chat import (
    ChatMessage,
    MessageAttachment,
    MessageReaction,
    MessageRead,
)

# =========================
# SETTINGS
# =========================

DELETE_AFTER_DAYS = 5


async def cleanup_old_messages():

    cutoff = datetime.now(UTC) - timedelta(days=DELETE_AFTER_DAYS)

    deleted_messages = 0
    deleted_files = 0

    async with AsyncSessionLocal() as db:

        result = await db.execute(
            select(ChatMessage).where(
                ChatMessage.created_at < cutoff
            )
        )

        messages = result.scalars().all()

        for msg in messages:

            # =========================
            # DELETE ATTACHMENTS
            # =========================

            for attachment in msg.attachments:

                try:

                    # remove physical file
                    file_path = attachment.file_url.lstrip("/")

                    path = Path(file_path)

                    if path.exists():
                        path.unlink()
                        deleted_files += 1

                except Exception as e:
                    print(f"Failed deleting file: {e}")

                await db.delete(attachment)

            # =========================
            # DELETE REACTIONS
            # =========================

            reactions = await db.execute(
                select(MessageReaction).where(
                    MessageReaction.message_id == msg.id
                )
            )

            for reaction in reactions.scalars().all():
                await db.delete(reaction)

            # =========================
            # DELETE READS
            # =========================

            reads = await db.execute(
                select(MessageRead).where(
                    MessageRead.message_id == msg.id
                )
            )

            for read in reads.scalars().all():
                await db.delete(read)

            # =========================
            # DELETE MESSAGE
            # =========================

            await db.delete(msg)

            deleted_messages += 1

        await db.commit()

    print(f"\nDeleted messages: {deleted_messages}")
    print(f"Deleted files: {deleted_files}")


if __name__ == "__main__":
    asyncio.run(cleanup_old_messages())
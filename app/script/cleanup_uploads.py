from pathlib import Path
from datetime import datetime, timedelta, UTC
import os

# =========================
# UPLOAD DIRECTORIES
# =========================

UPLOAD_DIRS = [
    "uploads/chats/images",
    "uploads/chats/videos",
    "uploads/chats/files",
]

# =========================
# DELETE SETTINGS
# =========================

# auto delete uploads older than 5 days
DELETE_AFTER_DAYS = 5

cutoff = datetime.now(UTC) - timedelta(days=DELETE_AFTER_DAYS)

deleted_count = 0


# =========================
# CLEANUP FUNCTION
# =========================

def cleanup_folder(folder_path: str):
    global deleted_count

    base = Path(folder_path)

    if not base.exists():
        print(f"Folder not found: {folder_path}")
        return

    for file in base.rglob("*"):

        if not file.is_file():
            continue

        try:
            modified_time = datetime.fromtimestamp(
                os.path.getmtime(file),
                UTC
            )

            if modified_time < cutoff:

                file.unlink()

                deleted_count += 1

                print(f"Deleted: {file}")

        except Exception as e:
            print(f"Failed deleting {file}: {e}")


# =========================
# RUN CLEANUP
# =========================

for folder in UPLOAD_DIRS:
    cleanup_folder(folder)

print(f"\nTotal deleted files: {deleted_count}")
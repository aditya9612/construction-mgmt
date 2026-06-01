import os
import uuid
import shutil
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db_session
from app.core.dependencies import get_current_active_user
from app.models.user import User
from app.models.project import Task
from app.services.voice_service import VoiceService
from app.core.enums import TaskStatus

router = APIRouter(prefix="/voice-tasks", tags=["voice-tasks"])

UPLOAD_DIR = "uploads/voice_instructions"
RAW_DIR = os.path.join(UPLOAD_DIR, "raw")
GEN_DIR = os.path.join(UPLOAD_DIR, "generated")

os.makedirs(RAW_DIR, exist_ok=True)
os.makedirs(GEN_DIR, exist_ok=True)

@router.post("/process-audio")
async def process_voice_audio(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user)
):
    """
    1. Upload supervisor audio.
    2. Convert speech -> text (Whisper).
    3. Translate to English.
    4. Convert translated/original text to audio (gTTS) for the worker if needed.
    """
    # 1. Save raw audio
    ext = file.filename.split(".")[-1] if file.filename else "mp3"
    raw_filename = f"raw_{uuid.uuid4().hex}.{ext}"
    raw_filepath = os.path.join(RAW_DIR, raw_filename)
    
    with open(raw_filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        # 2. Transcribe (Whisper)
        transcription_result = VoiceService.transcribe_audio(raw_filepath)
        original_text = transcription_result["text"]
        lang_code = transcription_result.get("language_code", "hi")
        
        # 3. Translate to English
        english_text = VoiceService.translate_to_english(original_text, lang_code)
        
        # 4. Generate TTS audio
        gen_filepath = VoiceService.generate_task_audio(original_text, lang_code, GEN_DIR)
        
        return {
            "voice_instruction_url": f"/{raw_filepath}".replace("\\", "/"),
            "generated_audio_url": f"/{gen_filepath}".replace("\\", "/"),
            "original_transcription_text": original_text,
            "translated_english_text": english_text,
            "language_code": lang_code,
            "audio_duration": 0
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Audio processing failed: {str(e)}")


@router.post("/assign")
async def assign_voice_task(
    project_id: int = Form(...),
    title: str = Form(...),
    description: Optional[str] = Form(None),
    assigned_user_id: Optional[int] = Form(None),
    voice_instruction_url: str = Form(...),
    generated_audio_url: str = Form(...),
    original_transcription_text: str = Form(...),
    translated_english_text: str = Form(...),
    language_code: str = Form(...),
    task_icon: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    """
    Create a task using the voice workflow outputs.
    """
    new_task = Task(
        project_id=project_id,
        title=title,
        description=description,
        assigned_user_id=assigned_user_id,
        status=TaskStatus.PLANNED,
        created_by_user_id=current_user.id,
        voice_instruction_url=voice_instruction_url,
        generated_audio_url=generated_audio_url,
        original_transcription_text=original_transcription_text,
        translated_english_text=translated_english_text,
        language_code=language_code,
        task_icon=task_icon
    )
    
    db.add(new_task)
    await db.flush()
    await db.refresh(new_task)
    
    return {"message": "Voice task assigned successfully", "task_id": new_task.id}

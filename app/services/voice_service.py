import os
import uuid
import logging
from faster_whisper import WhisperModel
from gtts import gTTS
from deep_translator import GoogleTranslator

logger = logging.getLogger(__name__)

# Load model in memory (base model is a good compromise between speed and accuracy)
try:
    # Using CPU and int8 for compatibility and low memory usage
    whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
    logger.info("Whisper model loaded successfully.")
except Exception as e:
    logger.error(f"Failed to load whisper model: {e}")
    whisper_model = None

class VoiceService:
    @staticmethod
    def transcribe_audio(file_path: str) -> dict:
        """
        Transcribes an audio file and returns the text and language code.
        """
        if not whisper_model:
            raise RuntimeError("Whisper model not loaded.")
        
        segments, info = whisper_model.transcribe(file_path, beam_size=5)
        text = " ".join([segment.text for segment in segments]).strip()
        
        return {
            "text": text,
            "language_code": info.language
        }

    @staticmethod
    def translate_to_english(text: str, source_lang: str) -> str:
        """
        Translates text to English for reporting and analytics.
        """
        if source_lang == "en":
            return text
        try:
            # Map whisper language codes to deep_translator codes if necessary
            # deep_translator 'auto' can also be used
            translator = GoogleTranslator(source=source_lang, target='en')
            return translator.translate(text)
        except Exception as e:
            logger.error(f"Translation failed: {e}")
            # Fallback to auto-detect if specific source lang fails
            try:
                return GoogleTranslator(source='auto', target='en').translate(text)
            except:
                return text

    @staticmethod
    def generate_task_audio(text: str, language_code: str, output_dir: str) -> str:
        """
        Generates TTS audio from text in the specified language.
        Returns the absolute file path of the generated audio.
        """
        # gTTS supports 'mr' (Marathi) and 'hi' (Hindi). Fallback to Hindi if unsupported.
        supported_langs = ['mr', 'hi', 'en', 'gu', 'ta', 'te', 'kn', 'ml', 'bn', 'pa', 'ur']
        tts_lang = language_code if language_code in supported_langs else 'hi'
        
        try:
            tts = gTTS(text=text, lang=tts_lang, slow=False)
            filename = f"tts_{uuid.uuid4().hex}.mp3"
            file_path = os.path.join(output_dir, filename)
            
            os.makedirs(output_dir, exist_ok=True)
            tts.save(file_path)
            
            return file_path
        except Exception as e:
            logger.error(f"TTS generation failed: {e}")
            raise RuntimeError(f"Could not generate audio: {e}")

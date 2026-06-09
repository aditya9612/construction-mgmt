import os
import uuid
import logging

from faster_whisper import WhisperModel
from gtts import gTTS
from deep_translator import GoogleTranslator

logger = logging.getLogger(__name__)

# =========================================
# LOAD WHISPER MODEL ONCE GLOBALLY
# =========================================

try:

    whisper_model = WhisperModel(
        "small",
        device="cpu",
        compute_type="int8"
    )

    logger.info(
        "Whisper model loaded successfully."
    )

except Exception as e:

    logger.error(
        f"Failed to load whisper model: {e}"
    )

    whisper_model = None


class VoiceService:

    @staticmethod
    def transcribe_audio(
        audio_path: str,
        language: str = None
    ):

        """
        Transcribe audio using Faster Whisper.
        """

        if not whisper_model:

            raise RuntimeError(
                "Whisper model not loaded."
            )

        # segments, info = whisper_model.transcribe(
        #     audio_path,
        #     language=None if language == "auto" else language,
        #     vad_filter=True,
        #     beam_size=5
        # )

        segments, info = whisper_model.transcribe(
            audio_path,
            language=None if language == "auto" else language,
            vad_filter=True,
            beam_size=5,
            initial_prompt="""
            Construction site instructions in Marathi and Hindi.
            Common words:
            साइट, काम, सिमेंट, कॉलम, बीम,
            उद्या, सकाळी, मजूर, इंजिनियर
            """
        )

        text = " ".join(
            [segment.text for segment in segments]
        )

        return {
            "text": text.strip(),
            "language": info.language
        }

    @staticmethod
    def translate_to_english(
        text: str,
        source_lang: str
    ) -> str:

        """
        Translate text to English.
        """

        if source_lang == "en":

            return text

        try:

            translator = GoogleTranslator(
                source=source_lang,
                target="en"
            )

            return translator.translate(text)

        except Exception as e:

            logger.error(
                f"Translation failed: {e}"
            )

            try:

                return GoogleTranslator(
                    source="auto",
                    target="en"
                ).translate(text)

            except:

                return text

    @staticmethod
    def generate_task_audio(
        text: str,
        language_code: str,
        output_dir: str
    ) -> str:

        """
        Generate TTS audio from text.
        """

        supported_langs = [
            "mr", "hi", "en", "gu",
            "ta", "te", "kn", "ml",
            "bn", "pa", "ur"
        ]

        tts_lang = (
            language_code
            if language_code in supported_langs
            else "hi"
        )

        try:

            tts = gTTS(
                text=text,
                lang=tts_lang,
                slow=False
            )

            filename = (
                f"tts_{uuid.uuid4().hex}.mp3"
            )

            file_path = os.path.join(
                output_dir,
                filename
            )

            os.makedirs(
                output_dir,
                exist_ok=True
            )

            tts.save(file_path)

            return file_path

        except Exception as e:

            logger.error(
                f"TTS generation failed: {e}"
            )

            raise RuntimeError(
                f"Could not generate audio: {e}"
            )
from faster_whisper import WhisperModel

print("Downloading model...")

model = WhisperModel(
    "small",
    device="cpu",
    compute_type="int8"
)

print("Model downloaded successfully!")
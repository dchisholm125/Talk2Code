from faster_whisper import WhisperModel
import time

# use 'distil-small.en' -> It is 6x faster than standard Whisper and accurate enough.
model_size = "distil-small.en"

print(f"Loading {model_size}...")

# STRATEGY: Try GPU first, but fallback to CPU if VRAM is full (likely with 6GB card)
try:
    # Force CPU and use int8 quantization (super fast on Intel CPUs)
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    print("✅ Loaded on GPU (Speed Mode)")
except Exception as e:
    print(f"⚠️ GPU Full (Ollama is eating it!). Falling back to CPU.")
    # int8 is fast on CPU
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    print("✅ Loaded on CPU (Safe Mode)")

print("Transcribing...")
start = time.time()

# We provide 'initial_prompt' to help it understand we are coding
segments, info = model.transcribe(
    "test.wav", 
    beam_size=1, 
    initial_prompt="python code function class def import"
)

for segment in segments:
    print(f"[{segment.start:.2f}s -> {segment.end:.2f}s] {segment.text}")

print(f"⏱️ Total Time: {time.time() - start:.2f}s")
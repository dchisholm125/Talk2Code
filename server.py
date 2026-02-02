import os
import shutil
import time
import subprocess
from fastapi import FastAPI, UploadFile, File
from faster_whisper import WhisperModel
import httpx

# --- CONFIGURATION ---
MODEL_PATH = "distil-small.en"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5-coder:7b"
UPLOAD_DIR = "uploads"

# Ensure upload dir exists
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 1. Load Whisper on CPU (to save GPU for Ollama)
print("🎧 Loading Ear Model (CPU)...")
ear_model = WhisperModel(MODEL_PATH, device="cpu", compute_type="int8")
print("✅ Ears Ready.")

app = FastAPI()

def refine_with_ollama(raw_text):
    """
    Asks Qwen to turn messy speech into a strict coding command.
    """
    system_prompt = (
        "You are a Senior Technical Architect. "
        "Your goal is to convert the User's transcribed voice note into a strict, imperative technical task. "
        "Remove filler words. Do not answer the question, just rephrase it as a clear instruction. "
        "Keep it concise."
    )
    
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"{system_prompt}\n\nUser Voice: \"{raw_text}\"\n\nTask:",
        "stream": False,
        "keep_alive": -1  # Keep model loaded in VRAM
    }
    
    try:
        response = httpx.post(OLLAMA_URL, json=payload, timeout=30.0)
        response.raise_for_status()
        return response.json()["response"].strip()
    except Exception as e:
        return f"Brain Error: {str(e)}"

@app.post("/voice")
async def handle_voice(file: UploadFile = File(...)):
    start_time = time.time()
    
    # A. Save the file locally
    file_location = f"{UPLOAD_DIR}/{file.filename}"
    with open(file_location, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    print(f"🎤 Received Audio: {file.filename}")

    # B. Transcribe (The Ears)
    segments, _ = ear_model.transcribe(
        file_location, 
        beam_size=1, 
        initial_prompt="python code function class def import"
    )
    # Combine all segments into one string
    raw_text = " ".join([segment.text for segment in segments])
    print(f"📝 Heard: {raw_text}")
    
    # C. Refine (The Brain)
    refined_text = refine_with_ollama(raw_text)
    print(f"🧠 Refined: {refined_text}")
    
    total_time = time.time() - start_time
    
    # Return the result to the phone
    return {
        "raw": raw_text,
        "refined": refined_text,
        "latency": f"{total_time:.2f}s"
    }

if __name__ == "__main__":
    import uvicorn
    # Host 0.0.0.0 allows other devices on your network to see it!
    uvicorn.run(app, host="0.0.0.0", port=8000)
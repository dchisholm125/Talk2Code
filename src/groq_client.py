import os
import json
import subprocess
import logging

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_TIMEOUT = int(os.getenv("GROQ_TIMEOUT", "60"))

API_URL = "https://api.groq.com/openai/v1/chat/completions"


def groq_chat(prompt: str, system_prompt: str = "You are a helpful assistant.") -> str:
    """Send a prompt to Groq API via curl.
    
    Args:
        prompt: The user message content
        system_prompt: Optional system prompt
        
    Returns:
        The assistant's response content
        
    Raises:
        Exception: On curl errors, non-200 status, or parse errors
    """
    if not GROQ_API_KEY:
        raise Exception("GROQ_API_KEY not configured. Set GROQ_API_KEY in your .env file.")

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7
    }

    payload_json = json.dumps(payload)

    cmd = [
        "curl", "-s", "-X", "POST",
        API_URL,
        "-H", f"Authorization: Bearer {GROQ_API_KEY}",
        "-H", "Content-Type: application/json",
        "-d", payload_json
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GROQ_TIMEOUT
        )

        if result.returncode != 0:
            raise Exception(f"curl failed: {result.stderr}")

        response_text = result.stdout

        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            raise Exception(f"Failed to parse JSON response: {e}. Response: {response_text[:500]}")

        if "error" in data:
            raise Exception(f"Groq API error: {data['error']}")

        if "choices" not in data or not data["choices"]:
            raise Exception(f"Invalid response format, no choices: {response_text[:500]}")

        return data["choices"][0]["message"]["content"]

    except subprocess.TimeoutExpired:
        raise Exception(f"Request timed out after {GROQ_TIMEOUT}s")
    except Exception as e:
        if "GROQ_API_KEY" in str(e) or "curl failed" in str(e) or "JSON" in str(e):
            raise
        raise Exception(f"Groq request failed: {e}")


def is_configured() -> bool:
    """Check if Groq client is properly configured."""
    return bool(GROQ_API_KEY)

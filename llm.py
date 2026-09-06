"""Minimal client for the local Ollama server.

Uses the standard library only: no SDK, no vendor dependency. The whole
point of running locally is that nothing leaves the machine, so the client
does not need to be more than an HTTP call.
"""

import json
import os
import urllib.error
import urllib.request

BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
TIMEOUT_SECONDS = 180


class LLMError(Exception):
    """Raised when the local model cannot be reached."""


def chat(system_prompt, user_prompt, temperature=0):
    """Send a chat request to Ollama and return the raw text answer.

    Args:
        system_prompt: The instructions given to the model.
        user_prompt: The content to process.
        temperature: 0 keeps the output stable across runs.

    Returns:
        The model answer as a string.

    Raises:
        LLMError: If the server is unreachable or returns an error.
    """
    payload = {
        "model": MODEL,
        "stream": False,
        "format": "json",  # server-side JSON guarantee
        "options": {"temperature": temperature},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    request = urllib.request.Request(
        f"{BASE_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read())["message"]["content"]
    except urllib.error.URLError as error:
        raise LLMError(
            f"Serveur Ollama injoignable sur {BASE_URL}. "
            f"Lancez 'ollama serve' dans un terminal. ({error})"
        ) from error
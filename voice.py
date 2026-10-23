"""Optional Termux:API voice helpers for Ai-Assistant.

Requires the Termux:API app and the termux-api package. Voice commands are
optional; the text assistant still works when these commands are unavailable.
"""

import shutil
import subprocess


def command_available(name):
    return shutil.which(name) is not None


def listen(timeout=60):
    """Return (recognized_text, None) or (None, error_message)."""
    command = "termux-speech-to-text"
    if not command_available(command):
        return None, "termux-speech-to-text was not found. Install the Termux:API app and package first."
    try:
        result = subprocess.run(
            [command], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"Speech recognition timed out after {timeout} seconds."
    except Exception as exc:
        return None, f"Speech recognition error: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        return None, detail or f"Speech recognition exited with code {result.returncode}."
    recognized = (result.stdout or "").strip()
    if not recognized:
        return None, "No speech was recognized."
    return recognized, None


def speak(text, timeout=30):
    """Speak text once and return (success, message)."""
    command = "termux-tts-speak"
    if not command_available(command):
        return False, "termux-tts-speak was not found. Install the Termux:API app and package first."
    text = str(text).strip()
    if not text:
        return False, "Nothing to speak."
    try:
        result = subprocess.run(
            [command, text], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return False, (
            f"TTS did not finish within {timeout} seconds. "
            "No repeated retry was attempted."
        )
    except Exception as exc:
        return False, f"TTS error: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        return False, detail or f"TTS exited with code {result.returncode}."
    return True, "Speech command completed."

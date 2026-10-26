#!/usr/bin/env python3
"""Termux AI assistant powered by OpenRouter with optional Gemini fallback."""

import datetime
import json
import os
import shlex
import signal
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import ui
except ImportError:
    ui = None

try:
    import voice
except ImportError:
    voice = None

try:
    import project_memory
except ImportError:
    project_memory = None

HOME = Path.home().resolve()
KEY_FILE = HOME / ".config" / "openrouter" / "key"
GEMINI_KEY_FILE = HOME / ".config" / "gemini" / "key"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
AI_WORKDIR = Path(os.environ.get("AI_WORKDIR", os.getcwd())).expanduser().resolve()
APPROVE_MODE_FILE = HOME / ".config" / "ai-assistant" / "approve_mode"
SPEAK_MODE_FILE = HOME / ".config" / "ai-assistant" / "speak_mode"
PROJECT_MEMORY_STORE = HOME / ".config" / "ai-assistant" / "projects"

SECRET_PATH_TOKENS = (
    str(KEY_FILE),
    str(GEMINI_KEY_FILE),
    ".config/openrouter/key",
    ".config/gemini/key",
)

try:
    from rich.console import Console
    from rich.panel import Panel
    console = Console()
    RICH = True
except ImportError:
    console = None
    RICH = False

SYSTEM = f"""You are a helpful terminal AI agent running in Termux on Android.
You can inspect, create, edit, and delete files; create directories; run shell
commands; start local Python HTTP servers; and stop processes using tools.
The active workspace is {AI_WORKDIR}. Prefer working inside that workspace.
Outside-workspace file access requires explicit approval. With /approve on,
routine workspace file creation, editing, directory creation, and local
development tasks may proceed without repeated prompts. Always require
confirmation for deletion, process termination, destructive/system-level
commands, and suspicious or potentially harmful actions. Never claim an action
succeeded unless its tool confirms success. Do not request root access or use
sudo/su. Do not expose API keys or secrets. If the user's request is vague
(e.g. "retry"), first ask what specifically to retry — do NOT guess and loop.
You are an autonomous coding agent. If the user gives you PRD or instruction files, you must read them, plan the architecture, create all necessary files (HTML, CSS, JS), run the server, and test the website yourself. Do not ask for step-by-step permission. Keep executing tools until the website is fully built and running.

IMPORTANT server tool distinction:
- `start_server` runs Python's built-in `http.server` — a STATIC file server
  only. It does NOT run custom logic like /login endpoints.
- To run a custom server script (e.g. server.py with login, auth, database),
  use `run_command` with a background launch like:
      nohup python3 server.py > server.log 2>&1 &
  then check server.log for startup confirmation.
- Do not retry start_server if the app needs custom routes.
A persistent project memory feature is available via /project init, /project
save, and /project resume. Never invent tests or completed work; only mark
items as done when there is real evidence in the session.
"""

TOOLS = [
    {"type": "function", "function": {"name": "list_files", "description": "List files in the active workspace or a subdirectory.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Directory path; defaults to the active workspace."}}, "required": []}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file inside the active workspace.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a text file inside the active workspace.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "edit_file", "description": "Replace the first exact text match in a workspace file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}}},
    {"type": "function", "function": {"name": "delete_file", "description": "Delete a file or directory inside the active workspace. Requires user approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "recursive": {"type": "boolean"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "make_directory", "description": "Create a directory inside the active workspace.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the active workspace. Never use sudo or su.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "start_server", "description": "Start a local Python HTTP server for a workspace directory on 127.0.0.1.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "port": {"type": "integer"}}, "required": ["path", "port"]}}},
    {"type": "function", "function": {"name": "stop_process", "description": "Send SIGTERM to a process by PID. Only stop processes you recognize.", "parameters": {"type": "object", "properties": {"pid": {"type": "integer"}}, "required": ["pid"]}}},
]


def say(message=""):
    if RICH:
        console.print(message)
    else:
        print(message)


def show_error(message):
    if ui is not None and hasattr(ui, "show_error"):
        ui.show_error(message)
    else:
        say(f"Error: {message}")


def approval_mode():
    try:
        return APPROVE_MODE_FILE.read_text(encoding="utf-8").strip().lower() == "on"
    except OSError:
        return False


def set_approval_mode(enabled):
    APPROVE_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    APPROVE_MODE_FILE.write_text("on" if enabled else "off", encoding="utf-8")


_APPROVAL_NOTICE_SHOWN = {"seen": False}


def approved(action, high_risk=True):
    if approval_mode() and not high_risk:
        if not _APPROVAL_NOTICE_SHOWN["seen"]:
            say("Approval skipped (low-risk mode ON).")
            _APPROVAL_NOTICE_SHOWN["seen"] = True
        return True
    if ui is not None and hasattr(ui, "ask_confirmation"):
        try:
            return ui.ask_confirmation(f"\n{action}")
        except Exception:
            pass
    say("\nAPPROVAL REQUIRED")
    say(action)
    try:
        answer = input("Proceed? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        say("Cancelled.")
        return False
    if answer in {"y", "yes"}:
        return True
    say("Cancelled.")
    return False


def is_secret_path(path):
    try:
        rp = path.resolve()
    except OSError:
        rp = path
    try:
        if rp == KEY_FILE.resolve() or rp == GEMINI_KEY_FILE.resolve():
            return True
    except OSError:
        pass
    s = str(rp)
    return any(s.endswith(tok) for tok in (".config/openrouter/key", ".config/gemini/key"))


def safe_path(raw=".", high_risk_outside=True):
    raw_path = Path(str(raw)).expanduser()
    if not raw_path.is_absolute():
        raw_path = AI_WORKDIR / raw_path
    path = raw_path.resolve()
    if is_secret_path(path):
        raise ValueError("Blocked: API key files are protected and cannot be accessed by file tools.")
    outside = path != AI_WORKDIR and AI_WORKDIR not in path.parents
    if outside and high_risk_outside:
        if not approved(f"Access path outside the active workspace:\n{path}", high_risk=True):
            raise PermissionError("Outside-workspace access denied by user.")
    return path, outside


def tool_list_files(args):
    path, _outside = safe_path(args.get("path", "."))
    if not path.is_dir():
        return "Error: not a directory."
    entries = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))[:300]
    return "\n".join(("DIR  " if item.is_dir() else "FILE ") + item.name for item in entries) or "(empty directory)"


def tool_read_file(args):
    path, _outside = safe_path(args["path"])
    if not path.is_file():
        return "Error: file not found."
    if path.stat().st_size > 500_000:
        return "Error: file is larger than 500 KB."
    return path.read_text(encoding="utf-8", errors="replace")[:30_000]


def tool_write_file(args):
    path, outside = safe_path(args["path"])
    content = str(args["content"])
    if not approved(f"Write file: {path}\nBytes: {len(content.encode('utf-8'))}", high_risk=outside):
        return "Cancelled by user."
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"Saved: {path}"


def tool_edit_file(args):
    path, outside = safe_path(args["path"])
    if not path.is_file():
        return "Error: file not found."
    content = path.read_text(encoding="utf-8", errors="replace")
    old_text = str(args["old_text"])
    if not old_text:
        return "Error: old_text cannot be empty."
    if old_text not in content:
        return "Error: exact old_text was not found; file unchanged."
    updated = content.replace(old_text, str(args["new_text"]), 1)
    if not approved(f"Edit file: {path}\nReplace the first matching text?", high_risk=outside):
        return "Cancelled by user."
    path.write_text(updated, encoding="utf-8")
    return f"Updated: {path}"


def tool_delete_file(args):
    path, _outside = safe_path(args["path"])
    if path == AI_WORKDIR:
        return "Blocked: cannot delete the active workspace root."
    if not path.exists():
        return "Error: path not found."
    recursive = bool(args.get("recursive", False))
    if path.is_dir() and any(path.iterdir()) and not recursive:
        return "Directory is not empty. Set recursive=true only if you intend to delete its contents."
    if not approved(f"DELETE: {path}\nDirectory: {path.is_dir()}\nRecursive: {recursive}"):
        return "Cancelled by user."
    if path.is_dir():
        import shutil
        shutil.rmtree(path) if recursive else path.rmdir()
    else:
        path.unlink()
    return f"Deleted: {path}"


def tool_make_directory(args):
    path, outside = safe_path(args["path"])
    if not approved(f"Create directory: {path}", high_risk=outside):
        return "Cancelled by user."
    path.mkdir(parents=True, exist_ok=True)
    return f"Directory ready: {path}"


_HARD_BLOCK_TOKENS = (
    "sudo", "su", "doas", "tsu",
    "mkfs", "fdisk", "parted", "wipefs",
    "reboot", "shutdown", "poweroff", "halt",
)

_HIGH_RISK_PATTERNS = [
    r"(^|[;&|]\s*)rm\s",
    r"(^|[;&|]\s*)rmdir\s",
    r"(^|[;&|]\s*)shred\s",
    r"(^|[;&|]\s*)unlink\s",
    r"(^|[;&|]\s*)dd\s",
    r"(^|[;&|]\s*)truncate\s",
    r"(^|[;&|]\s*)mv\s+/\S+",
    r"(^|[;&|]\s*)chmod\s",
    r"(^|[;&|]\s*)chown\s",
    r"(^|[;&|]\s*)kill(all)?\s",
    r"(^|[;&|]\s*)pkill\s",
    r"\bfind\b[^\n]*-delete\b",
    r"python[0-9.]*\s+-c\s+.*(os\.remove|shutil\.rmtree|os\.unlink|os\.rmdir)",
    r"(?<![0-9&])>{1,2}\s*/(?!dev/null\b|dev/stdout\b|dev/stderr\b)",
    r"\|\s*(sh|bash|zsh)\b",
    r"(^|[;&|]\s*)eval\s",
    r"(^|[;&|]\s*)exec\s",
]

import re as _re
_HIGH_RISK_RE = [_re.compile(p) for p in _HIGH_RISK_PATTERNS]


def _split_command(command):
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _command_mentions_secret(command):
    lowered = command.lower().replace("\\", "/")
    try:
        expanded = os.path.expandvars(command).lower().replace("\\", "/")
    except Exception:
        expanded = lowered
    for tok in SECRET_PATH_TOKENS:
        tok_l = tok.lower().replace("\\", "/")
        if tok_l and (tok_l in lowered or tok_l in expanded):
            return True
    for tok in ("~/.config/openrouter/key", "~/.config/gemini/key",
                "$home/.config/openrouter/key", "$home/.config/gemini/key"):
        if tok in expanded:
            return True
    return False


def _command_refers_outside_workspace(command, words):
    for word in words:
        w = word.replace("\\", "/")
        if w.startswith("/") and not (w == str(AI_WORKDIR) or w.startswith(str(AI_WORKDIR) + "/")):
            return True
        if w.startswith("~") or w.startswith("$HOME"):
            return True
    for word in words:
        w = word.replace("\\", "/")
        if ".." in Path(w).parts:
            try:
                target = (AI_WORKDIR / w).resolve()
            except Exception:
                return True
            if target != AI_WORKDIR and AI_WORKDIR not in target.parents:
                return True
    return False


def _command_is_high_risk(command):
    lowered = command.lower()
    for word in _split_command(lowered):
        if Path(word).name.lower() in _HARD_BLOCK_TOKENS:
            return True
    for rx in _HIGH_RISK_RE:
        if rx.search(lowered):
            return True
    return False


# ---- Hardened shell detection (shlex-aware, v2) ----
_HARDENED_SHELL_DETECT_V2 = True

_DANGEROUS_PROGRAMS_EXACT = {
    "rm", "rmdir", "shred", "unlink", "truncate", "dd",
    "chmod", "chown", "chattr", "kill", "killall", "pkill",
    "reboot", "shutdown", "poweroff", "halt",
    "sudo", "su", "doas", "tsu",
}
_DANGEROUS_PROGRAM_PREFIXES = ("mkfs", "fdisk", "parted", "wipefs", "mke2fs")
_DANGEROUS_BUILTINS = {"eval", "exec", "source", "."}
_PRIV_ESCALATION = {"sudo", "su", "doas", "tsu"}
_SYSTEM_DESTRUCTIVE = {"reboot", "shutdown", "poweroff", "halt"}

_ABS_PATH_WRITE_RE = _re.compile(r"(?<![0-9&])>{1,2}\s*/(?!dev/null\b|dev/stdout\b|dev/stderr\b)")
_PIPE_TO_SHELL_RE = _re.compile(r"\|\s*(?:sh|bash|zsh|ksh|dash)\b")
_PYTHON_MUTATION_TOKENS = (
    "os.remove", "os.unlink", "os.rmdir", "shutil.rmtree",
    "pathlib.Path.unlink", ".unlink(", ".rmdir(",
)


def _classify_segment(seg):
    """Return (hard_reason_or_None, high_risk_bool) for one command segment."""
    try:
        argv = shlex.split(seg, posix=True)
    except ValueError:
        # Malformed quoting — treat as high risk, not hard blocked
        return None, True
    if not argv:
        return None, False

    prog = Path(argv[0]).name.lower()

    if prog in _PRIV_ESCALATION:
        return f"privilege-escalation program '{prog}'", True
    if prog in _SYSTEM_DESTRUCTIVE:
        return f"system-destructive program '{prog}'", True
    for prefix in _DANGEROUS_PROGRAM_PREFIXES:
        if prog.startswith(prefix):
            return f"system-destructive program '{prog}'", True

    if prog in _DANGEROUS_BUILTINS:
        return None, True
    if prog in _DANGEROUS_PROGRAMS_EXACT:
        return None, True

    if prog == "find" and "-delete" in argv:
        return None, True

    # python -c <code> — inspect code as a single string (argv has -c then code)
    if prog.startswith("python") and "-c" in argv:
        try:
            i = argv.index("-c")
            code = argv[i + 1] if i + 1 < len(argv) else ""
        except (ValueError, IndexError):
            code = ""
        if any(tok in code for tok in _PYTHON_MUTATION_TOKENS):
            return None, True

    return None, False


def _segments_of(command):
    """Split at shell separators OUTSIDE quotes."""
    segs = []
    buf = []
    quote = None
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote and (i == 0 or command[i - 1] != "\\"):
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            segs.append("".join(buf)); buf = []; i += 1; continue
        if ch == "|" and i + 1 < n and command[i + 1] == "|":
            segs.append("".join(buf)); buf = []; i += 2; continue
        if ch == "|":
            segs.append("".join(buf)); buf = []; i += 1; continue
        if ch == "&" and i + 1 < n and command[i + 1] == "&":
            segs.append("".join(buf)); buf = []; i += 2; continue
        buf.append(ch); i += 1
    segs.append("".join(buf))
    return [s for s in segs if s.strip()]


def _command_is_high_risk(command):
    for seg in _segments_of(command):
        reason, risky = _classify_segment(seg)
        if reason or risky:
            return True
    if _ABS_PATH_WRITE_RE.search(command):
        return True
    if _PIPE_TO_SHELL_RE.search(command):
        return True
    return False


def _hard_block_reason(command):
    if _command_mentions_secret(command):
        return "command references a known API-key path"
    for seg in _segments_of(command):
        reason, _ = _classify_segment(seg)
        if reason:
            return reason
    return None


def tool_run_command(args):
    command = str(args["command"]).strip()
    if not command:
        return "Error: empty command."
    for word in _split_command(command):
        if Path(word).name.lower() in _HARD_BLOCK_TOKENS:
            return "Blocked: root/privilege-escalation commands are not allowed."
    if _command_mentions_secret(command):
        return "Blocked: commands may not directly reference known API-key files."
    words = _split_command(command)
    risky = _command_is_high_risk(command)
    outside_refs = _command_refers_outside_workspace(command, words)
    high_risk = risky or outside_refs
    if not approved(f"Run shell command in {AI_WORKDIR}:\n{command}", high_risk=high_risk):
        return "Cancelled by user."
    try:
        result = subprocess.run(
            command, shell=True, cwd=str(AI_WORKDIR), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120,
        )
        output = result.stdout or "(no output)"
        return f"Exit code: {result.returncode}\n{output[-12_000:]}"
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return f"Command timed out after 120 seconds.\n{output[-6_000:]}"
    except Exception as exc:
        return f"Command error: {type(exc).__name__}: {exc}"


def tool_start_server(args):
    path, outside = safe_path(args["path"])
    try:
        port = int(args["port"])
    except (TypeError, ValueError):
        return "Error: port must be an integer."
    if not path.is_dir():
        return "Error: server path must be an existing workspace directory."
    if not 1024 <= port <= 65535:
        return "Error: choose a port between 1024 and 65535."
    if not approved(f"Start local Python HTTP server:\nDirectory: {path}\nPort: {port}\nURL: http://127.0.0.1:{port}", high_risk=outside):
        return "Cancelled by user."
    log_path = AI_WORKDIR / "http-server.log"
    try:
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(path)],
                cwd=str(AI_WORKDIR), stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True,
            )
        return f"Started local server PID {process.pid}. URL: http://127.0.0.1:{port}. Log: {log_path}"
    except Exception as exc:
        return f"Could not start server: {exc}"


def tool_stop_process(args):
    try:
        pid = int(args["pid"])
    except (TypeError, ValueError):
        return "Error: PID must be an integer."
    if pid <= 1 or pid == os.getpid() or pid == os.getppid():
        return "Blocked: invalid or protected PID."
    if not approved(f"Send SIGTERM to PID {pid}?"):
        return "Cancelled by user."
    try:
        os.kill(pid, signal.SIGTERM)
        return f"Sent SIGTERM to PID {pid}."
    except Exception as exc:
        return f"Could not stop PID {pid}: {exc}"


_LIVE_PROGRESS = True


def _with_autosave(fn, tool_name):
    def wrapper(args):
        # Show a one-line progress notice BEFORE running the tool, so the
        # terminal never looks "hung" during slow operations.
        hint = ""
        if tool_name == "run_command" and isinstance(args, dict):
            cmd = str(args.get("command", ""))[:60]
            hint = f" -> {cmd}"
        elif tool_name == "start_server" and isinstance(args, dict):
            hint = f" -> port {args.get('port','?')}"
        elif tool_name == "stop_process" and isinstance(args, dict):
            hint = f" -> PID {args.get('pid','?')}"
        elif tool_name in ("write_file", "edit_file", "delete_file") and isinstance(args, dict):
            hint = f" -> {args.get('path','?')}"
        if hint:
            say(f"[{tool_name}] running{hint}...")
        result = fn(args)
        try:
            if isinstance(result, str) and not result.startswith(("Cancelled", "Error", "Blocked")):
                if _autosave_after_mutation(tool_name):
                    say(f"(project memory autosaved after {tool_name})")
        except Exception:
            pass
        return result
    return wrapper


DISPATCH = {
    "list_files": tool_list_files,
    "read_file": tool_read_file,
    "write_file": _with_autosave(tool_write_file, "write_file"),
    "edit_file": _with_autosave(tool_edit_file, "edit_file"),
    "delete_file": _with_autosave(tool_delete_file, "delete_file"),
    "make_directory": _with_autosave(tool_make_directory, "make_directory"),
    "run_command": _with_autosave(tool_run_command, "run_command"),
    "start_server": _with_autosave(tool_start_server, "start_server"),
    "stop_process": _with_autosave(tool_stop_process, "stop_process"),
}


def call_api(messages):
    errors = []

    def strip_nulls(value):
        if isinstance(value, dict):
            return {k: strip_nulls(v) for k, v in value.items() if v is not None}
        if isinstance(value, list):
            return [strip_nulls(v) for v in value if v is not None]
        return value

    def request_json(url, payload, key, provider, extra_headers=None):
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(
            url,
            data=json.dumps(strip_nulls(payload)).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace").lower()
            except Exception:
                body = ""
            if exc.code == 429:
                msg = "free quota/rate limit exhausted" if provider == "OpenRouter" else "quota or rate limit exceeded"
            elif "thought_signature" in body or "thought signature" in body:
                msg = "tool-call signature missing in conversation history"
            elif exc.code in (401, 403):
                msg = "API key rejected or access not permitted"
            elif exc.code == 400:
                msg = "request rejected (check model, tool-call history, or payload)"
            else:
                msg = "provider request failed"
            raise RuntimeError(f"{provider}: HTTP {exc.code} — {msg}") from None
        except urllib.error.URLError:
            raise RuntimeError(f"{provider}: connection failed; check internet") from None

    try:
        if not KEY_FILE.is_file():
            raise RuntimeError(f"Key file not found: {KEY_FILE}")
        key = KEY_FILE.read_text(encoding="utf-8").strip()
        if not key:
            raise RuntimeError("OpenRouter key file is empty.")
        payload = {
            "model": MODEL,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "temperature": 0.3,
        }
        return request_json(
            API_URL, payload, key, "OpenRouter",
            {"HTTP-Referer": "https://openrouter.ai/", "X-Title": "Termux AI Assistant"},
        )
    except Exception as exc:
        errors.append(str(exc))

    def gemini_compatible_messages(history):
        converted = []
        for item in history:
            if not isinstance(item, dict):
                continue
            role = item.get("role", "")
            content = item.get("content")
            if role == "tool":
                tool_id = item.get("tool_call_id", "")
                converted.append({"role": "user", "content": f"Previous tool result (call {tool_id}):\n{content or ''}"})
            elif role in ("system", "user", "assistant") and content not in (None, ""):
                converted.append({"role": role, "content": content})
        return converted

    try:
        if not GEMINI_KEY_FILE.is_file():
            raise RuntimeError(f"Key file not found: {GEMINI_KEY_FILE}")
        key = GEMINI_KEY_FILE.read_text(encoding="utf-8").strip()
        if not key:
            raise RuntimeError("Gemini key file is empty.")
        payload = {
            "model": "gemini-3.5-flash-lite",
            "messages": gemini_compatible_messages(messages),
            "tools": TOOLS,
            "tool_choice": "auto",
            "temperature": 0.3,
        }
        return request_json(
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            payload, key, "Gemini",
        )
    except Exception as exc:
        errors.append(str(exc))

    summary = "\n".join(f"- {e}" for e in errors)
    raise RuntimeError("All AI providers failed:\n" + summary)


def call_gemini_native(history):
    from google import genai
    from google.genai import types

    if not GEMINI_KEY_FILE.is_file():
        raise RuntimeError(f"Key file not found: {GEMINI_KEY_FILE}")
    key = GEMINI_KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError("Gemini key file is empty.")

    client = genai.Client(api_key=key)
    declarations = []
    for item in TOOLS:
        function = item.get("function", {})
        if not function.get("name"):
            continue
        schema = function.get("parameters") or {"type": "object", "properties": {}}
        declarations.append(types.FunctionDeclaration(
            name=function["name"],
            description=function.get("description", ""),
            parameters_json_schema=schema,
        ))

    system_text = "\n".join(
        str(item.get("content", ""))
        for item in history
        if isinstance(item, dict) and item.get("role") == "system" and item.get("content")
    )

    contents = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content:
            continue
        contents.append(types.Content(
            role="user" if role == "user" else "model",
            parts=[types.Part.from_text(text=content)],
        ))
    if not contents:
        contents = [types.Content(role="user", parts=[types.Part.from_text(text="Hello")])]

    config = types.GenerateContentConfig(
        system_instruction=system_text or None,
        tools=[types.Tool(function_declarations=declarations)] if declarations else None,
        temperature=0.3,
    )

    for _ in range(60):
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=contents,
            config=config,
        )
        candidate_content = response.candidates[0].content if response.candidates else None
        if candidate_content is not None:
            contents.append(candidate_content)
        calls = list(response.function_calls or [])
        if not calls:
            answer = response.text
            if answer:
                return answer
            # No text, no calls: append a synthetic user nudge so the next
            # retry does not end on a model turn (Gemini rejects that).
            contents.append(types.Content(
                role="user",
                parts=[types.Part.from_text(text="Continue or summarize what you have done.")],
            ))
            continue
        function_responses = []
        for call in calls:
            name = call.name or ""
            try:
                arguments = dict(call.args or {})
                result = DISPATCH[name](arguments) if name in DISPATCH else "Error: unknown tool."
            except Exception as exc:
                result = f"Tool error: {type(exc).__name__}: {exc}"
            function_responses.append(types.Part.from_function_response(
                name=name or "unknown_tool",
                response={"result": str(result)[:15000]},
            ))
        contents.append(types.Content(role="user", parts=function_responses))
    return "Stopped after 8 tool rounds to prevent an endless loop."


_LOOP_GUARD_PATCHED = True


_TOOL_CALL_CAP = True


def ask_agent(history):
    _seen_calls = set()
    _call_counts = {}
    for _round in range(60):
        try:
            if RICH:
                with console.status("[cyan]Thinking...[/cyan]"):
                    response = call_api(history)
            else:
                print("Thinking...")
                response = call_api(history)
            choices = response.get("choices") or []
            if not choices:
                err_detail = response.get("error") or response.get("message") or "empty choices"
                raise RuntimeError(f"OpenRouter returned no choices: {err_detail}")
            message = choices[0].get("message") or {}
        except Exception as openrouter_error:
            if RICH:
                with console.status("[cyan]Trying native Gemini...[/cyan]"):
                    try:
                        return call_gemini_native(history)
                    except Exception as gemini_error:
                        raise RuntimeError(
                            "AI providers failed.\n"
                            f"Chat API: {openrouter_error}\n"
                            f"Native Gemini: {gemini_error}"
                        ) from None
            print("Trying native Gemini...")
            try:
                return call_gemini_native(history)
            except Exception as gemini_error:
                raise RuntimeError(
                    "AI providers failed.\n"
                    f"Chat API: {openrouter_error}\n"
                    f"Native Gemini: {gemini_error}"
                ) from None

        history.append(message)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return message.get("content") or "(No text response)"
        for tool_call in tool_calls:
            function = tool_call.get("function", {})
            name = function.get("name", "")
            args_raw = function.get("arguments") or "{}"
            call_key = (name, args_raw)
            if call_key in _seen_calls:
                history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", ""),
                    "content": f"Skipped: identical '{name}' call was already executed this turn. Do not repeat it.",
                })
                continue
            _seen_calls.add(call_key)
            _UNLIMITED_AUTOPILOT = True
            # Tool limits removed for full website building capability
            try:
                arguments = json.loads(args_raw)
                result = DISPATCH[name](arguments) if name in DISPATCH else "Error: unknown tool."
            except Exception as exc:
                result = f"Tool error: {type(exc).__name__}: {exc}"
            history.append({
                "role": "tool",
                "tool_call_id": tool_call.get("id", ""),
                "content": str(result)[:15000],
            })
    return "Stopped after 8 tool rounds to prevent an endless loop."


def command_help():
    commands = {
        "/help": "Show available commands",
        "/listen": "Recognize speech and send it to the AI",
        "/speak on": "Enable spoken AI replies",
        "/speak off": "Disable spoken AI replies",
        "/approve on": "Skip approval for designated low-risk actions",
        "/approve off": "Require approval for protected actions",
        "/project init": "Initialize project memory for this workspace",
        "/project save": "Save current project state summary",
        "/project resume": "Load memory and report status (read-only)",
        "/project sync": "Copy source files to $PREFIX install dir",
        "/clear": "Clear conversation history",
        "/exit": "Exit the assistant",
    }
    if ui is not None and hasattr(ui, "show_help"):
        try:
            ui.show_help(commands)
            return
        except Exception:
            pass
    print("\nAvailable commands:")
    for command, description in commands.items():
        print(f"  {command:<16} {description}")


def show_welcome():
    if ui is not None and hasattr(ui, "show_welcome"):
        try:
            ui.show_welcome()
            return
        except Exception as exc:
            print(f"Welcome screen fallback: {exc}")
    os.system("clear")
    now = datetime.datetime.now()
    print("╔══════════════════════════════════════════════════════╗")
    print("║                 AI ASSISTANT ONLINE                 ║")
    print("╚══════════════════════════════════════════════════════╝")
    print(f"DATE      >> {now:%A, %d %B %Y}")
    print(f"TIME      >> {now:%I:%M:%S %p}")
    print(f"WORKSPACE >> {AI_WORKDIR}")
    print("COMMAND   >> /help\n")


def get_input():
    if ui is not None and hasattr(ui, "get_input"):
        try:
            return ui.get_input("YOU").strip()
        except Exception:
            pass
    return input("\nYou > ").strip()


def show_reply(answer):
    if ui is not None and hasattr(ui, "show_reply"):
        try:
            ui.show_reply(answer)
            return
        except Exception:
            pass
    if RICH:
        console.print(Panel(str(answer), title="AI ASSISTANT", border_style="bright_cyan"))
    else:
        print(f"\nAI > {answer}")


# ---------------------------------------------------------------------------
# Project memory / resume feature
# ---------------------------------------------------------------------------

def _pm_available():
    return project_memory is not None


# ---- Auto-save + prompt refresh helpers ----
_AUTOSAVE_PATCHED = True
# Tracks whether project memory exists for this session (so autosave stays silent otherwise).
_SESSION_HAS_MEMORY = False


def _refresh_memory_flag():
    """Update _SESSION_HAS_MEMORY based on whether memory file exists."""
    global _SESSION_HAS_MEMORY
    if not _pm_available():
        _SESSION_HAS_MEMORY = False
        return
    try:
        _SESSION_HAS_MEMORY = project_memory.load(str(AI_WORKDIR), store=str(PROJECT_MEMORY_STORE)) is not None
    except Exception:
        _SESSION_HAS_MEMORY = False


_AUTOSAVE_VISIBLE = True


def _autosave_after_mutation(tool_name):
    """Checkpoint after a file mutation. Returns True if saved."""
    if not _pm_available() or not _SESSION_HAS_MEMORY:
        return False
    try:
        mem = project_memory.load(str(AI_WORKDIR), store=str(PROJECT_MEMORY_STORE))
        if mem is None:
            return False
        _AUTOSAVE_DEDUP = True
        note = f"Autosave after {tool_name} at {project_memory._utcnow()}"
        state = mem.get("current_state") or []
        # Skip if the same tool ran within the last 2 seconds (loop/spam guard).
        import time as _time
        recent_same = False
        for prev in state[-3:]:
            if isinstance(prev, str) and prev.startswith(f"Autosave after {tool_name} at "):
                try:
                    ts = prev.rsplit(" at ", 1)[1]
                    prev_epoch = __import__("datetime").datetime.fromisoformat(ts.replace("Z","+00:00")).timestamp()
                    if abs(_time.time() - prev_epoch) < 2.0:
                        recent_same = True
                        break
                except Exception:
                    pass
        if not recent_same:
            state.append(note)
        mem["current_state"] = state[-50:]
        project_memory.checkpoint(str(AI_WORKDIR), mem, store=str(PROJECT_MEMORY_STORE))
        return True
    except Exception:
        return False


def _refresh_system_prompt(history):
    """Rebuild the system prompt so updated memory is visible to the model."""
    if not history or not isinstance(history[0], dict) or history[0].get("role") != "system":
        return False
    if not _pm_available():
        history[0]["content"] = SYSTEM
        return True
    try:
        mem = project_memory.load(str(AI_WORKDIR), store=str(PROJECT_MEMORY_STORE))
    except Exception:
        return False
    if not mem:
        history[0]["content"] = SYSTEM
        return True
    try:
        ctx = project_memory.summarize_for_prompt(mem, max_items=10)
    except Exception:
        ctx = ""
    history[0]["content"] = SYSTEM + ("\n\n" + ctx if ctx else "")
    return True


# ---- Installed-copy sync helper ----
_SYNC_PATCHED = True


def _project_sync():
    """Copy source files to $PREFIX/share/Ai-Assistant if it exists."""
    prefix = os.environ.get("PREFIX", "")
    if not prefix:
        # Try common Termux prefix as fallback.
        candidate = Path("/data/data/com.termux/files/usr")
        if candidate.exists():
            prefix = str(candidate)
    if not prefix:
        show_error("PREFIX is not set; cannot determine install directory.")
        return

    target_dir = Path(prefix) / "share" / "Ai-Assistant"
    if not target_dir.is_dir():
        show_error(f"Install dir not found: {target_dir}. Run setup.sh first.")
        return

    source_dir = Path(__file__).parent.resolve()
    files = ["assistant.py", "ui.py", "voice.py", "project_memory.py", "README.md"]
    copied = []
    missing = []
    for name in files:
        src_file = source_dir / name
        if not src_file.is_file():
            missing.append(name)
            continue
        dst = target_dir / name
        try:
            dst.write_bytes(src_file.read_bytes())
            copied.append(name)
        except Exception as exc:
            show_error(f"Could not copy {name}: {exc}")
            return

    say(f"Synced {len(copied)} file(s) to {target_dir}")
    for name in copied:
        say(f"  - {name}")
    if missing:
        say(f"Skipped (missing in source): {', '.join(missing)}")
    say("Restart the 'ai' launcher to use the new code.")


def _project_init():
    if not _pm_available():
        show_error("project_memory module unavailable.")
        return
    try:
        mem = project_memory.init_from_scan(str(AI_WORKDIR), store=str(PROJECT_MEMORY_STORE))
        mem["current_state"] = ["Initialized from workspace scan (read-only)."]
        mem["next_step"] = "Ask the AI to fill goal/architecture/completed/known_issues, then /project save."
        path = project_memory.save(mem, str(AI_WORKDIR), store=str(PROJECT_MEMORY_STORE))
    except Exception as exc:
        show_error(f"Project init failed: {exc}")
        return
    try:
        short_path = "~/" + str(path.relative_to(Path.home())) if str(path).startswith(str(Path.home())) else str(path)
    except (ValueError, AttributeError):
        short_path = str(path)
    say(f"Project initialized. Memory file: {short_path}")


def _project_save(history=None):
    if not _pm_available():
        show_error("project_memory module unavailable.")
        return
    mem = project_memory.load(str(AI_WORKDIR), store=str(PROJECT_MEMORY_STORE))
    if mem is None:
        try:
            mem = project_memory.init_from_scan(str(AI_WORKDIR), store=str(PROJECT_MEMORY_STORE))
            say("No prior memory found; created a fresh skeleton from a scan.")
        except FileNotFoundError:
            show_error(f"Workspace does not exist: {AI_WORKDIR}. Nothing to save.")
            return
        except Exception as exc:
            show_error(f"Project save failed during scan: {exc}")
            return
    try:
        path = project_memory.checkpoint(str(AI_WORKDIR), mem, store=str(PROJECT_MEMORY_STORE))
    except Exception as exc:
        show_error(f"Project save failed: {exc}")
        return
    try:
        short_path = "~/" + str(path.relative_to(Path.home())) if str(path).startswith(str(Path.home())) else str(path)
    except (ValueError, AttributeError):
        short_path = str(path)
    say(f"Project memory saved: {short_path}")
    _refresh_memory_flag()
    if history is not None and _refresh_system_prompt(history):
        say("(System prompt refreshed with latest project memory.)")


def _project_resume():
    if not _pm_available():
        show_error("project_memory module unavailable.")
        return
    mem = project_memory.load(str(AI_WORKDIR), store=str(PROJECT_MEMORY_STORE))
    if mem is None:
        say("No saved project memory for this workspace.")
        say("Run /project init to create one.")
        return
    try:
        stale = project_memory.stale_files(mem, str(AI_WORKDIR))
    except Exception:
        stale = []
    say("=" * 60)
    say("RESUMING PROJECT (inspection-only; no files modified)")
    say("=" * 60)
    say(project_memory.summarize_for_prompt(mem, max_items=10))
    if stale:
        say("")
        say("Stale / changed files since last checkpoint (up to 30):")
        for rel in stale[:30]:
            say(f"  - {rel}")
        say("Ask the AI to re-inspect these files before trusting memory.")
    else:
        say("")
        say("No file changes detected since last checkpoint.")
    say("=" * 60)


def _project_help():
    say("Project memory commands:")
    say("  /project init    Initialize memory for this workspace (read-only scan)")
    say("  /project save    Save current project state summary")
    say("  /project resume  Load memory and report status (inspection-only)")
    say("  /project sync    Copy current source files to $PREFIX install dir")


def handle_project_command(arg, history=None):
    sub = (arg or "").strip().lower()
    if sub == "init":
        _project_init()
        _refresh_memory_flag()
    elif sub in {"save", "update"}:
        _project_save(history=history)
    elif sub == "resume":
        _project_resume()
    elif sub == "sync":
        _project_sync()
    elif sub in {"", "help"}:
        _project_help()
    else:
        say(f"Unknown /project subcommand: {sub!r}. Use init, save, or resume.")


def _inject_memory_context(history):
    """Append project memory summary to the system prompt (if memory exists)."""
    if not _pm_available():
        return False
    try:
        mem = project_memory.load(str(AI_WORKDIR), store=str(PROJECT_MEMORY_STORE))
    except Exception:
        return False
    if not mem:
        return False
    try:
        ctx = project_memory.summarize_for_prompt(mem, max_items=8)
    except Exception:
        return False
    if not ctx or not history:
        return False
    history[0]["content"] = history[0]["content"] + "\n\n" + ctx
    return True


def maybe_auto_resume(history=None):
    """Prompt to resume if memory exists. If accepted, also inject context."""
    if not _pm_available():
        return
    try:
        mem = project_memory.load(str(AI_WORKDIR), store=str(PROJECT_MEMORY_STORE))
    except Exception:
        return
    if mem is None:
        return
    say("Saved project found. Resume previous work? [y/N]")
    try:
        answer = input("Resume? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if answer in {"y", "yes"}:
        _project_resume()
        if history is not None:
            injected = _inject_memory_context(history)
            if injected:
                say("(Project memory summary added to AI context.)")



def checkpoint_on_exit():
    if not _pm_available():
        return
    try:
        mem = project_memory.load(str(AI_WORKDIR), store=str(PROJECT_MEMORY_STORE))
        if mem is None:
            return
        project_memory.checkpoint(str(AI_WORKDIR), mem, store=str(PROJECT_MEMORY_STORE))
    except Exception:
        pass


def main():
    show_welcome()
    history = [{"role": "system", "content": SYSTEM}]
    maybe_auto_resume(history)
    _refresh_memory_flag()
    try:
        auto_speech_enabled = SPEAK_MODE_FILE.read_text(encoding="utf-8").strip().lower() == "on"
    except OSError:
        auto_speech_enabled = False

    while True:
        try:
            user_text = get_input()
            if not user_text:
                continue
            _APPROVAL_NOTICE_SHOWN["seen"] = False
            normalized = " ".join(user_text.lower().split())
            # Handle vague "retry" prompts — no reference to retry.
            if normalized in {"retry", "again", "try again", "do it again"}:
                say("Retry what? Please be specific (e.g. 'start server on port 8888', 're-run the test', 'list files').")
                continue
            _RETRY_HINT = True
            if normalized in {"/exit", "exit", "quit"}:
                checkpoint_on_exit()
                print("Bye!")
                break
            if normalized == "/help":
                command_help()
                continue
            if normalized == "/clear":
                history = [{"role": "system", "content": SYSTEM}]
                if RICH:
                    console.clear()
                else:
                    os.system("clear")
                print("Chat cleared.")
                continue
            if normalized.startswith("/project"):
                arg = normalized[len("/project"):].strip()
                handle_project_command(arg, history=history)
                continue
            if normalized == "/approve on":
                set_approval_mode(True)
                print("Approval mode ON: low-risk actions can skip approval; high-risk actions still require it.")
                continue
            if normalized == "/approve off":
                set_approval_mode(False)
                print("Approval mode OFF: all protected actions require approval.")
                continue
            if normalized.startswith("/approve"):
                print("Use /approve on or /approve off.")
                continue
            if normalized == "/speak on":
                auto_speech_enabled = True
                SPEAK_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
                SPEAK_MODE_FILE.write_text("on", encoding="utf-8")
                print("Voice replies ON. Normal AI replies will be spoken.")
                continue
            if normalized == "/speak off":
                auto_speech_enabled = False
                SPEAK_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
                SPEAK_MODE_FILE.write_text("off", encoding="utf-8")
                print("Voice replies OFF. AI replies will remain text-only.")
                continue
            if normalized.startswith("/speak"):
                print("Use /speak on or /speak off.")
                continue
            if normalized == "/listen":
                if voice is None:
                    print("Voice module unavailable: voice.py could not be imported.")
                    continue
                recognized, error = voice.listen()
                if error:
                    show_error(error)
                    continue
                user_text = (recognized or "").strip()
                if not user_text:
                    print("No speech was recognized.")
                    continue
                print(f"Recognized: {user_text}")
            history.append({"role": "user", "content": user_text})
            try:
                answer = ask_agent(history)
            except Exception as exc:
                answer = f"Error: {exc}"
            show_reply(answer)
            if auto_speech_enabled and voice is not None:
                ok, message = voice.speak(answer)
                if not ok:
                    show_error(message)
        except (KeyboardInterrupt, EOFError):
            checkpoint_on_exit()
            print("\nBye!")
            break


if __name__ == "__main__":
    main()

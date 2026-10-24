#!/usr/bin/env python3
"""Termux AI assistant powered by OpenRouter."""

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

HOME = Path.home().resolve()
KEY_FILE = HOME / ".config" / "openrouter" / "key"
GEMINI_KEY_FILE = HOME / ".config" / "gemini" / "key"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
AI_WORKDIR = Path(os.environ.get("AI_WORKDIR", os.getcwd())).expanduser().resolve()
APPROVE_MODE_FILE = HOME / ".config" / "ai-assistant" / "approve_mode"
SPEAK_MODE_FILE = HOME / ".config" / "ai-assistant" / "speak_mode"

def approval_mode():
    try:
        return APPROVE_MODE_FILE.read_text(encoding="utf-8").strip().lower() == "on"
    except OSError:
        return False

def set_approval_mode(enabled):
    APPROVE_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    APPROVE_MODE_FILE.write_text("on" if enabled else "off", encoding="utf-8")


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
File tools are restricted to this workspace. The user must approve every file
change, shell command, server start, or process termination. Never claim an
action succeeded unless its tool confirms success. Be concise and clear.
Do not request root access or use sudo/su. Do not expose API keys or secrets.
Explain commands before asking for approval. Never bypass approval.
"""

TOOLS = [
    {"type": "function", "function": {"name": "list_files", "description": "List files in the active workspace or a subdirectory.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Directory path; defaults to the active workspace."}}, "required": []}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file inside the active workspace.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a text file inside the active workspace. Requires user approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "edit_file", "description": "Replace the first exact text match in a workspace file. Requires user approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}}},
    {"type": "function", "function": {"name": "delete_file", "description": "Delete a file or directory inside the active workspace. Requires user approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "recursive": {"type": "boolean"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "make_directory", "description": "Create a directory inside the active workspace. Requires user approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "run_command", "description": "Run a shell command in the active workspace after explicit approval. Never use sudo or su.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "start_server", "description": "Start a local Python HTTP server for a workspace directory after approval.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "port": {"type": "integer"}}, "required": ["path", "port"]}}},
    {"type": "function", "function": {"name": "stop_process", "description": "Send SIGTERM to a process by PID after approval. Only stop processes you recognize.", "parameters": {"type": "object", "properties": {"pid": {"type": "integer"}}, "required": ["pid"]}}},
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


def approved(action, high_risk=True):
    if approval_mode() and not high_risk:
        say("Approval skipped (low-risk mode ON).")
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


def safe_path(raw="."):
    """Resolve a path and ensure it stays inside the launch workspace."""
    raw_path = Path(str(raw)).expanduser()
    if not raw_path.is_absolute():
        raw_path = AI_WORKDIR / raw_path
    path = raw_path.resolve()
    if path != AI_WORKDIR and AI_WORKDIR not in path.parents:
        raise ValueError(f"Blocked: file tools are limited to the active workspace: {AI_WORKDIR}")
    if is_secret_path(path):
        raise ValueError("Blocked: the OpenRouter API key is protected and cannot be accessed by file tools.")
    return path


def is_secret_path(path):
    try:
        return path.resolve() in (KEY_FILE.resolve(), GEMINI_KEY_FILE.resolve())
    except OSError:
        return path == KEY_FILE or path == GEMINI_KEY_FILE


def tool_list_files(args):
    path = safe_path(args.get("path", "."))
    if not path.is_dir():
        return "Error: not a directory."
    entries = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))[:300]
    return "\n".join(("DIR  " if item.is_dir() else "FILE ") + item.name for item in entries) or "(empty directory)"


def tool_read_file(args):
    path = safe_path(args["path"])
    if not path.is_file():
        return "Error: file not found."
    if path.stat().st_size > 500_000:
        return "Error: file is larger than 500 KB."
    return path.read_text(encoding="utf-8", errors="replace")[:30_000]


def tool_write_file(args):
    path = safe_path(args["path"])
    content = str(args["content"])
    if not approved(f"Write file: {path}\nBytes: {len(content.encode('utf-8'))}"):
        return "Cancelled by user."
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"Saved: {path}"


def tool_edit_file(args):
    path = safe_path(args["path"])
    if not path.is_file():
        return "Error: file not found."
    content = path.read_text(encoding="utf-8", errors="replace")
    old_text = str(args["old_text"])
    if not old_text:
        return "Error: old_text cannot be empty."
    if old_text not in content:
        return "Error: exact old_text was not found; file unchanged."
    updated = content.replace(old_text, str(args["new_text"]), 1)
    if not approved(f"Edit file: {path}\nReplace the first matching text?"):
        return "Cancelled by user."
    path.write_text(updated, encoding="utf-8")
    return f"Updated: {path}"


def tool_delete_file(args):
    path = safe_path(args["path"])
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
    path = safe_path(args["path"])
    if not approved(f"Create directory: {path}", high_risk=False):
        return "Cancelled by user."
    path.mkdir(parents=True, exist_ok=True)
    return f"Directory ready: {path}"


def tool_run_command(args):
    command = str(args["command"]).strip()
    if not command:
        return "Error: empty command."
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    if any(Path(word).name.lower() in {"sudo", "su", "doas", "tsu"} for word in words):
        return "Blocked: root/privilege-escalation commands are not allowed."
    lowered = command.lower().replace("\\", "/")
    secret_patterns = (".config/openrouter/key", ".config/gemini/key", ".config/gemini-assistant/key")
    if any(pattern in lowered for pattern in secret_patterns):
        return "Blocked: commands may not directly reference known API-key files."
    if not approved(f"Run shell command in {AI_WORKDIR}:\n{command}"):
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
    path = safe_path(args["path"])
    try:
        port = int(args["port"])
    except (TypeError, ValueError):
        return "Error: port must be an integer."
    if not path.is_dir():
        return "Error: server path must be an existing workspace directory."
    if not 1024 <= port <= 65535:
        return "Error: choose a port between 1024 and 65535."
    if not approved(f"Start local Python HTTP server:\nDirectory: {path}\nPort: {port}\nURL: http://127.0.0.1:{port}"):
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


DISPATCH = {
    "list_files": tool_list_files,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "delete_file": tool_delete_file,
    "make_directory": tool_make_directory,
    "run_command": tool_run_command,
    "start_server": tool_start_server,
    "stop_process": tool_stop_process,
}


def call_api(messages):
    errors = []

    def strip_nulls(value):
        if isinstance(value, dict):
            return {
                k: strip_nulls(v)
                for k, v in value.items()
                if v is not None
            }
        if isinstance(value, list):
            return [strip_nulls(v) for v in value if v is not None]
        return value

    def request_json(url, payload, key, provider, extra_headers=None):
        headers = {
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        }
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
            # Keep provider errors short; never print raw response bodies or headers.
            try:
                body = exc.read().decode("utf-8", errors="replace").lower()
            except Exception:
                body = ""

            if exc.code == 429:
                if provider == "OpenRouter":
                    msg = "free quota/rate limit exhausted"
                else:
                    msg = "quota or rate limit exceeded"
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

    # Try OpenRouter first.
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
            {
                "HTTP-Referer": "https://openrouter.ai/",
                "X-Title": "Termux AI Assistant",
            },
        )
    except Exception as exc:
        errors.append(str(exc))

    def gemini_compatible_messages(history):
        # Gemini must not receive incomplete historical tool-call records.
        # Preserve system/user/assistant text and retain tool results as text.
        converted = []
        for item in history:
            if not isinstance(item, dict):
                continue

            role = item.get("role", "")
            content = item.get("content")

            if role == "tool":
                tool_id = item.get("tool_call_id", "")
                result_text = str(content or "")
                converted.append({
                    "role": "user",
                    "content": (
                        f"Previous tool result (call {tool_id}):\\n"
                        f"{result_text}"
                    ),
                })
            elif role in ("system", "user", "assistant"):
                if content is not None and content != "":
                    converted.append({
                        "role": role,
                        "content": content,
                    })
                # Deliberately omit old assistant tool_calls: their
                # provider-specific thought signatures may be unavailable.
        return converted

    # If OpenRouter fails, try Gemini Flash.
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

    summary = "\\n".join(f"- {e}" for e in errors)
    raise RuntimeError("All AI providers failed:\\n" + summary)


def call_gemini_native(history):
    """Use Google's native SDK so Gemini function-call signatures survive tool rounds."""
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
        schema = function.get("parameters") or {
            "type": "object", "properties": {}
        }
        declarations.append(types.FunctionDeclaration(
            name=function["name"],
            description=function.get("description", ""),
            parameters_json_schema=schema,
        ))

    system_text = "\n".join(
        str(item.get("content", ""))
        for item in history
        if isinstance(item, dict) and item.get("role") == "system"
        and item.get("content")
    )

    # Rebuild only plain-text conversation context. Do not replay old provider-
    # specific tool-call records; native Gemini Content objects are kept intact
    # during the current turn below.
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
        contents = [types.Content(
            role="user", parts=[types.Part.from_text(text="Hello")]
        )]

    config = types.GenerateContentConfig(
        system_instruction=system_text or None,
        tools=[types.Tool(function_declarations=declarations)] if declarations else None,
        temperature=0.3,
    )

    for _ in range(8):
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=contents,
            config=config,
        )

        candidate_content = None
        if response.candidates:
            candidate_content = response.candidates[0].content

        if candidate_content is not None:
            # Keep the original SDK Content, including thought_signature bytes.
            contents.append(candidate_content)

        calls = list(response.function_calls or [])
        if not calls:
            answer = response.text
            if answer:
                return answer
            raise RuntimeError("Gemini returned neither text nor function calls.")

        function_responses = []
        for call in calls:
            name = call.name or ""
            try:
                arguments = dict(call.args or {})
                if name in DISPATCH:
                    result = DISPATCH[name](arguments)
                else:
                    result = "Error: unknown tool."
            except Exception as exc:
                result = f"Tool error: {type(exc).__name__}: {exc}"

            function_responses.append(types.Part.from_function_response(
                name=name or "unknown_tool",
                response={"result": str(result)[:15000]},
            ))

        contents.append(types.Content(role="user", parts=function_responses))

    return "Stopped after 8 tool rounds to prevent an endless loop."


def ask_agent(history):
    for _ in range(8):
        try:
            if RICH:
                with console.status("[cyan]Thinking...[/cyan]"):
                    response = call_api(history)
            else:
                print("Thinking...")
                response = call_api(history)

            message = response["choices"][0]["message"]
        except Exception as openrouter_error:
            # call_api already tries its compatible Gemini endpoint. If both
            # providers fail, use Gemini's native SDK for signature-safe tools.
            if RICH:
                with console.status("[cyan]Trying native Gemini...[/cyan]"):
                    return call_gemini_native(history)
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
            try:
                arguments = json.loads(function.get("arguments") or "{}")
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
        print(f"  {command:<12} {description}")


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


def main():
    show_welcome()
    history = [{"role": "system", "content": SYSTEM}]
    try:
        auto_speech_enabled = SPEAK_MODE_FILE.read_text(encoding="utf-8").strip().lower() == "on"
    except OSError:
        auto_speech_enabled = False

    while True:
        try:
            user_text = get_input()
            if not user_text:
                continue
            normalized = " ".join(user_text.lower().split())
            if normalized in {"/exit", "exit", "quit"}:
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
            print("\nBye!")
            break


if __name__ == "__main__":
    main()

"""
Persistent per-project memory for the Termux AI Assistant.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1
DEFAULT_STORE = Path.home() / ".config" / "ai-assistant" / "projects"

_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|bearer)\b\s*[:=]\s*['\"]?([^\s'\"]{6,})"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
]

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".idea", ".vscode", "dist", "build", ".ai-assistant",
}


def _utcnow() -> str:
    # Use local system time instead of UTC
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _hash_path(abs_path: str) -> str:
    return hashlib.sha256(abs_path.encode("utf-8")).hexdigest()[:16]


def _safe_slug(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._-")
    return name or "project"


def redact_secrets(text: str) -> str:
    if not text:
        return text
    out = text
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


def workspace_id(project_root: str) -> str:
    abs_root = str(Path(project_root).resolve())
    return _hash_path(abs_root)


def memory_dir(store: Optional[str] = None) -> Path:
    base = Path(store).expanduser() if store else DEFAULT_STORE
    base.mkdir(parents=True, exist_ok=True)
    return base


def memory_path(project_root: str, store: Optional[str] = None) -> Path:
    abs_root = str(Path(project_root).resolve())
    wid = workspace_id(abs_root)
    slug = _safe_slug(Path(abs_root).name)
    return memory_dir(store) / f"{slug}-{wid}.json"


def _empty_memory(project_root: str) -> Dict[str, Any]:
    abs_root = str(Path(project_root).resolve())
    return {
        "schema_version": SCHEMA_VERSION,
        "project_root": abs_root,
        "project_name": Path(abs_root).name,
        "goal": "",
        "architecture": [],
        "important_files": [],
        "completed": [],
        "current_state": [],
        "known_issues": [],
        "pending_tasks": [],
        "decisions": [],
        "tests": [],
        "file_index": {},
        "last_checkpoint": None,
        "next_step": "",
    }


def load(project_root: str, store: Optional[str] = None) -> Optional[Dict[str, Any]]:
    p = memory_path(project_root, store)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    abs_root = str(Path(project_root).resolve())
    if data.get("project_root") != abs_root:
        return None
    if data.get("schema_version") != SCHEMA_VERSION:
        return None
    return data


def save(data: Dict[str, Any], project_root: str, store: Optional[str] = None) -> Path:
    abs_root = str(Path(project_root).resolve())
    data = dict(data)
    data["schema_version"] = SCHEMA_VERSION
    data["project_root"] = abs_root
    data["project_name"] = data.get("project_name") or Path(abs_root).name
    data["last_checkpoint"] = _utcnow()
    for key, val in list(data.items()):
        if isinstance(val, str):
            data[key] = redact_secrets(val)
        elif isinstance(val, list):
            data[key] = [redact_secrets(v) if isinstance(v, str) else v for v in val]
    p = memory_path(abs_root, store)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return p


def build_file_index(project_root: str, max_files: int = 500) -> Dict[str, Dict[str, float]]:
    root = Path(project_root).resolve()
    idx: Dict[str, Dict[str, float]] = {}
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".ai-assistant")]
        for fn in filenames:
            if fn.startswith(".ai-assistant"):
                continue
            full = Path(dirpath) / fn
            try:
                st = full.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                continue
            try:
                rel = str(full.relative_to(root))
            except ValueError:
                continue
            idx[rel] = {"size": int(st.st_size), "mtime": float(st.st_mtime)}
            count += 1
            if count >= max_files:
                return idx
    return idx


def stale_files(memory: Dict[str, Any], project_root: str) -> List[str]:
    old = memory.get("file_index") or {}
    if not old:
        return []
    current = build_file_index(project_root)
    changed: List[str] = []
    for rel, meta in old.items():
        cur = current.get(rel)
        if cur is None:
            changed.append(rel + " (missing)")
        elif cur.get("mtime") != meta.get("mtime") or cur.get("size") != meta.get("size"):
            changed.append(rel)
    for rel in current:
        if rel not in old:
            changed.append(rel + " (new)")
    return sorted(changed)


def summarize_for_prompt(memory: Dict[str, Any], max_items: int = 12) -> str:
    def _fmt(key: str, label: str) -> str:
        val = memory.get(key)
        if not val:
            return ""
        if isinstance(val, list):
            items = [str(x) for x in val[:max_items]]
            body = "\n".join(f"  - {i}" for i in items)
            return f"{label}:\n{body}"
        return f"{label}: {val}"

    parts = [
        f"PROJECT MEMORY (schema v{memory.get('schema_version', SCHEMA_VERSION)})",
        f"Project: {memory.get('project_name', '?')} ({memory.get('project_root', '?')})",
        _fmt("goal", "Goal"),
        _fmt("architecture", "Architecture"),
        _fmt("important_files", "Important files"),
        _fmt("completed", "Completed"),
        _fmt("current_state", "Current state"),
        _fmt("known_issues", "Known issues"),
        _fmt("pending_tasks", "Pending tasks"),
        _fmt("decisions", "Decisions"),
        _fmt("tests", "Tests"),
        _fmt("next_step", "Next step"),
        f"Last checkpoint: {memory.get('last_checkpoint')}",
    ]
    return "\n".join(p for p in parts if p).strip()


def init_from_scan(project_root: str, store: Optional[str] = None) -> Dict[str, Any]:
    root = Path(project_root).resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Not a directory: {root}")
    mem = _empty_memory(str(root))
    mem["file_index"] = build_file_index(str(root))
    try:
        entries = sorted(p.name for p in root.iterdir() if not p.name.startswith(".ai-assistant"))
    except OSError:
        entries = []
    mem["architecture"] = [f"Top-level: {', '.join(entries[:40])}"] if entries else []
    for candidate in ("README.md", "README.txt", "README.rst"):
        rp = root / candidate
        if rp.is_file():
            try:
                excerpt = rp.read_text(encoding="utf-8", errors="replace")[:1200]
            except OSError:
                break
            mem["goal"] = redact_secrets(excerpt.strip())[:800]
            break
    return mem


def checkpoint(project_root: str, data: Dict[str, Any], store: Optional[str] = None) -> Path:
    root = str(Path(project_root).resolve())
    data["file_index"] = build_file_index(root)
    return save(data, root, store)

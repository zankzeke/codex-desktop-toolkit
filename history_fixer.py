"""
history_fixer.py — Offline repair of Codex Desktop session JSONL files.
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, Tuple

from id_rewriter import prepare_rewrite_context, sanitise_input_array


def is_codex_running() -> bool:
    """Check if Codex.exe is running."""
    import subprocess
    try:
        res = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Codex.exe", "/NH"],
            capture_output=True, text=True, timeout=2
        )
        return "codex.exe" in res.stdout.lower()
    except Exception:
        return False


def atomic_write_jsonl(path: Path, lines: list[str]) -> None:
    """Atomically write lines to path."""
    import uuid
    temp_path = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line)
                if not line.endswith("\n"):
                    f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        with open(temp_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    json.loads(stripped)

        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def backup_file(path: Path) -> Path | None:
    import shutil
    import uuid
    if not path.exists():
        return None
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    unique_id = uuid.uuid4().hex[:6]
    bak_path = path.with_suffix(f"{path.suffix}.bak.{timestamp}-{unique_id}")
    shutil.copy2(path, bak_path)
    return bak_path


def _backup_original_path(backup_path: Path) -> Path | None:
    """Derive the original JSONL path from Toolkit's backup naming scheme."""
    text = str(backup_path)
    marker = ".jsonl.bak."
    idx = text.lower().rfind(marker)
    if idx < 0:
        return None
    return Path(text[: idx + len(".jsonl")])


def list_session_backups(sessions_dir: Path) -> list[dict]:
    """List Toolkit-created session backups newest-first."""
    results: list[dict] = []
    if not sessions_dir.exists():
        return results
    for bak in sessions_dir.rglob("*.jsonl.bak.*"):
        original = _backup_original_path(bak)
        if original is None:
            continue
        try:
            stat = bak.stat()
        except OSError:
            continue
        name = bak.name
        stamp = ""
        match = re.search(r"\.bak\.(\d{8}-\d{6})-[0-9a-fA-F]+$", name)
        if match:
            stamp = match.group(1)
        results.append({
            "backup": bak,
            "original": original,
            "timestamp": stamp,
            "mtime": stat.st_mtime,
            "size": stat.st_size,
            "exists_original": original.exists(),
        })
    results.sort(key=lambda item: float(item.get("mtime", 0)), reverse=True)
    return results


def _collect_id_tokens(path: Path) -> set[str]:
    """Collect only structured ID-like tokens; never expose message text."""
    tokens: set[str] = set()
    id_re = re.compile(r'\b(?:resp_[0-9a-fA-F-]{16,}_msg|item_[0-9a-fA-F]{16,}|(?:msg|fc|fco|cc|cco|rs)_[A-Za-z0-9_-]{8,})\b')
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                tokens.update(id_re.findall(line))
    except OSError:
        pass
    return tokens


def compare_backup_ids(backup_path: Path, original_path: Path | None = None) -> dict:
    """Return a privacy-safe ID-only diff between a backup and current session."""
    backup = Path(backup_path)
    original = Path(original_path) if original_path else _backup_original_path(backup)
    if original is None:
        raise ValueError("无法从备份文件名推导原始会话路径")
    old_ids = _collect_id_tokens(backup)
    current_ids = _collect_id_tokens(original) if original.exists() else set()
    removed = sorted(old_ids - current_ids)
    added = sorted(current_ids - old_ids)
    return {
        "backup": backup,
        "original": original,
        "backup_ids": len(old_ids),
        "current_ids": len(current_ids),
        "removed_ids": removed,
        "added_ids": added,
    }


def restore_session_backup(backup_path: Path, original_path: Path | None = None) -> Path:
    """Restore one backup atomically, first backing up the current session."""
    import shutil
    import uuid

    backup = Path(backup_path)
    if not backup.exists():
        raise FileNotFoundError(backup)
    original = Path(original_path) if original_path else _backup_original_path(backup)
    if original is None:
        raise ValueError("无法从备份文件名推导原始会话路径")

    # Validate the selected backup before touching the current file.
    with open(backup, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                json.loads(stripped)

    if original.exists():
        backup_file(original)
    original.parent.mkdir(parents=True, exist_ok=True)
    temp_path = original.with_suffix(f"{original.suffix}.restore.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    try:
        shutil.copy2(backup, temp_path)
        with open(temp_path, "r", encoding="utf-8", errors="strict") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    json.loads(stripped)
        os.replace(temp_path, original)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
    return original


def fast_check_bad_ids(path: Path) -> bool:
    """Fast unanchored scan before full structural parsing."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            tail = ""
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                text = tail + chunk
                if "resp_" in text and "_msg" in text:
                    if re.search(r'resp_[0-9a-fA-F\-]{36}_msg', text):
                        return True
                if "item_" in text:
                    if re.search(r'item_[0-9a-fA-F]{16,}', text):
                        return True
                tail = text[-128:]
        return False
    except OSError:
        return False


def fix_rollout_file(path: Path, dry_run: bool = False) -> Tuple[int, int, Dict[str, str]]:
    """Fix a JSONL rollout using a file-wide two-pass rewrite context."""
    total_fixes = 0
    total_drops = 0
    id_map: Dict[str, str] = {}
    dropped_ids: set[str] = set()

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw_lines = f.readlines()

    records: list[tuple[str, object]] = []
    parsed_objects: list[dict] = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            records.append(("raw", line))
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            records.append(("raw", line))
            continue
        records.append(("json", obj))
        if isinstance(obj, dict):
            parsed_objects.append(obj)

    prepare_rewrite_context(
        parsed_objects,
        id_map=id_map,
        dropped_ids=dropped_ids,
        safe_reasoning=True,
    )

    out_lines: list[str] = []
    for kind, payload in records:
        if kind == "raw":
            out_lines.append(str(payload))
            continue

        obj = payload
        if not isinstance(obj, dict):
            out_lines.append(json.dumps(obj, ensure_ascii=False) + "\n")
            continue

        arr, id_map, fixes, drops = sanitise_input_array(
            [obj],
            id_map=id_map,
            dropped_ids=dropped_ids,
            safe_reasoning=True,
            context_prepared=True,
        )
        total_fixes += fixes
        total_drops += drops
        if arr:
            out_lines.append(json.dumps(arr[0], ensure_ascii=False) + "\n")

    if not dry_run and (total_fixes > 0 or total_drops > 0):
        backup_file(path)
        atomic_write_jsonl(path, out_lines)

    return total_fixes, total_drops, id_map

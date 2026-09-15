"""backup_manager.py — Management, structured diff, and atomic rollback for session backups.

Ensures that rollback is safe, atomic, refuses to execute while Codex is running,
and computes privacy-preserving structured diffs (showing only ID changes and reasoning drops,
never exposing chat body content).
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Tuple

from history_fixer import backup_file, is_codex_running
from id_rewriter import MSG_ID_RE, prepare_rewrite_context


@dataclass
class StructuredDiff:
    session_file: str
    backup_file: str
    id_changes: list[dict[str, str]]
    reference_changes: list[dict[str, str]]
    reasoning_drops: int
    lines_original: int
    lines_backup: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_default_sessions_dir() -> Path:
    return Path.home() / ".codex" / "sessions"


def list_session_backups(sessions_dir: Path | None = None) -> list[dict[str, Any]]:
    """Scan and list available session backup files sorted by creation time (newest first)."""
    sdir = sessions_dir or get_default_sessions_dir()
    if not sdir.exists():
        return []

    backups: list[dict[str, Any]] = []
    for p in sdir.rglob("*.bak*"):
        if not p.is_file():
            continue
        try:
            stat = p.stat()
            # Derive original session filename
            orig_name = p.name.split(".bak")[0]
            orig_path = p.parent / orig_name
            # Human readable timestamp
            ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))

            backups.append({
                "backup_path": str(p),
                "original_path": str(orig_path),
                "session_name": orig_name,
                "timestamp": ts_str,
                "mtime": stat.st_mtime,
                "size_bytes": stat.st_size,
                "original_exists": orig_path.exists(),
            })
        except OSError:
            continue

    backups.sort(key=lambda x: x["mtime"], reverse=True)
    return backups


def compute_structured_diff(current_path: Path, backup_path: Path) -> StructuredDiff:
    """Compute a privacy-safe structural diff using deterministic ID mappings."""
    def load_records(path: Path) -> tuple[list[dict[str, Any]], int]:
        records: list[dict[str, Any]] = []
        line_count = 0
        if not path.exists():
            return records, line_count
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                if not raw.strip():
                    continue
                line_count += 1
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
        return records, line_count

    def collect_ids_and_refs(obj: Any) -> tuple[set[str], set[str]]:
        ids: set[str] = set()
        refs: set[str] = set()
        ref_keys = {"item_id", "message_id", "previous_item_id", "parent_id", "response_id"}

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                raw_id = value.get("id")
                if isinstance(raw_id, str):
                    ids.add(raw_id)
                for key in ref_keys:
                    ref = value.get(key)
                    if isinstance(ref, str):
                        refs.add(ref)
                for key, child in value.items():
                    if key not in {"content", "text"} and isinstance(child, (dict, list)):
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(obj)
        return ids, refs

    current_records, current_lines = load_records(current_path)
    backup_records, backup_lines = load_records(backup_path)

    id_map: dict[str, str] = {}
    dropped_ids: set[str] = set()
    prepare_rewrite_context(
        backup_records,
        id_map=id_map,
        dropped_ids=dropped_ids,
        safe_reasoning=True,
    )

    current_ids: set[str] = set()
    current_refs: set[str] = set()
    backup_ids: set[str] = set()
    backup_refs: set[str] = set()
    for obj in current_records:
        ids, refs = collect_ids_and_refs(obj)
        current_ids.update(ids)
        current_refs.update(refs)
    for obj in backup_records:
        ids, refs = collect_ids_and_refs(obj)
        backup_ids.update(ids)
        backup_refs.update(refs)

    for old_id in backup_ids:
        match = MSG_ID_RE.match(old_id)
        if match:
            id_map.setdefault(old_id, f"msg_{match.group(1)}")

    id_changes = [
        {"old_id": old, "new_id": new}
        for old, new in sorted(id_map.items())
        if new in current_ids
    ]
    reference_changes = [
        {"old_ref": old, "new_ref": new}
        for old, new in sorted(id_map.items())
        if old in backup_refs and new in current_refs
    ]
    reasoning_drops = sum(1 for old in dropped_ids if old not in current_ids)

    return StructuredDiff(
        session_file=current_path.name,
        backup_file=backup_path.name,
        id_changes=id_changes,
        reference_changes=reference_changes,
        reasoning_drops=reasoning_drops,
        lines_original=current_lines,
        lines_backup=backup_lines,
    )

def rollback_session(
    current_path: Path,
    backup_path: Path,
    *,
    force: bool = False,
) -> Tuple[bool, str]:
    """Safely and atomically restore current_path from backup_path.

    1. Checks if Codex.exe is running; if so, refuses unless force=True.
    2. Validates backup_path integrity.
    3. Creates a pre-rollback backup of current_path.
    4. Performs an atomic write/replacement to prevent file corruption.
    """
    if not force and is_codex_running():
        return False, "Codex Desktop 正在运行中。为避免文件占用和数据冲突，请先退出 Codex 后再执行回滚。"

    if not backup_path.exists():
        return False, f"备份文件不存在: {backup_path.name}"

    # Verify backup is readable and has valid content
    lines: list[str] = []
    try:
        with open(backup_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    json.loads(stripped)  # Validate JSON line
                    lines.append(stripped + "\n")
    except Exception as exc:
        return False, f"备份文件损坏或非有效 JSONL: {exc}"

    if not lines:
        return False, "备份文件为空，拒绝恢复空文件。"

    # Create safety backup of the current state before rolling back
    pre_rollback_bak: Path | None = None
    if current_path.exists():
        pre_rollback_bak = backup_file(current_path)

    # Atomic write
    temp_path = current_path.with_suffix(f"{current_path.suffix}.tmp.rollback.{os.getpid()}")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, current_path)
    except Exception as exc:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        return False, f"回滚写入失败: {exc}"

    bak_info = f" (已在回滚前自动创建安全备份: {pre_rollback_bak.name})" if pre_rollback_bak else ""
    return True, f"已成功恢复至备份版本 {backup_path.name}{bak_info}。"

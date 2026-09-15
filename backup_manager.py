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
    """Compute a privacy-safe structural diff between backup and current session.

    Never extracts or returns full message content / chat text.
    """
    id_changes: list[dict[str, str]] = []
    reference_changes: list[dict[str, str]] = []
    reasoning_drops = 0

    cur_lines: list[str] = []
    bak_lines: list[str] = []

    if current_path.exists():
        with open(current_path, "r", encoding="utf-8", errors="replace") as f:
            cur_lines = [line.strip() for line in f if line.strip()]

    if backup_path.exists():
        with open(backup_path, "r", encoding="utf-8", errors="replace") as f:
            bak_lines = [line.strip() for line in f if line.strip()]

    # Collect IDs from backup vs current lines
    def extract_item_ids(lines: list[str]) -> list[str]:
        ids: list[str] = []
        for line in lines:
            try:
                obj = json.loads(line)
                # Rollout files might be wrapped in a list or dict
                items = obj if isinstance(obj, list) else [obj]
                for item in items:
                    if isinstance(item, dict):
                        # Message or item id
                        iid = item.get("id")
                        if isinstance(iid, str):
                            ids.append(iid)
                        # Payload item
                        sub = item.get("item")
                        if isinstance(sub, dict) and isinstance(sub.get("id"), str):
                            ids.append(sub["id"])
            except Exception:
                pass
        return ids

    bak_ids = extract_item_ids(bak_lines)
    cur_ids = extract_item_ids(cur_lines)

    # Detect synthetic -> fixed ID mappings
    seen_pairs: set[tuple[str, str]] = set()
    for b_id, c_id in zip(bak_ids, cur_ids):
        if b_id != c_id:
            pair = (b_id, c_id)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                if b_id.startswith("resp_") and c_id.startswith("msg_"):
                    id_changes.append({"old_id": b_id, "new_id": c_id})
                elif b_id.startswith("item_"):
                    reference_changes.append({"old_ref": b_id, "new_ref": c_id})
                else:
                    id_changes.append({"old_id": b_id, "new_id": c_id})

    # Count reasoning item differences
    def count_reasoning_items(lines: list[str]) -> int:
        count = 0
        for line in lines:
            if "reasoning" in line.lower() or "thought" in line.lower():
                try:
                    obj = json.loads(line)
                    items = obj if isinstance(obj, list) else [obj]
                    for item in items:
                        if isinstance(item, dict):
                            t = str(item.get("type", "")).lower()
                            if "reasoning" in t:
                                count += 1
                except Exception:
                    pass
        return count

    bak_reasoning = count_reasoning_items(bak_lines)
    cur_reasoning = count_reasoning_items(cur_lines)
    if bak_reasoning > cur_reasoning:
        reasoning_drops = bak_reasoning - cur_reasoning

    return StructuredDiff(
        session_file=current_path.name,
        backup_file=backup_path.name,
        id_changes=id_changes,
        reference_changes=reference_changes,
        reasoning_drops=reasoning_drops,
        lines_original=len(cur_lines),
        lines_backup=len(bak_lines),
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

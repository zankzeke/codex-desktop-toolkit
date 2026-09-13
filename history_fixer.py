"""
history_fixer.py — Offline repair of Codex Desktop session JSONL files.
"""

import json
import os
import time
from pathlib import Path
from typing import Tuple, Dict

from id_rewriter import sanitise_input_array

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
            
        # Parse validation
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

def fast_check_bad_ids(path: Path) -> bool:
    """
    Unanchored scan for bad IDs. Doesn't guarantee structural validity, 
    but prevents scanning the whole JSON every time if clean.
    """
    import re
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        # Unanchored check for patterns
        if "resp_" in text and "_msg" in text:
            if re.search(r'resp_[0-9a-fA-F\-]{36}_msg', text):
                return True
        if "item_" in text:
            if re.search(r'item_[0-9a-fA-F]{16,}', text):
                return True
        return False
    except OSError:
        return False

def fix_rollout_file(path: Path, dry_run: bool = False) -> Tuple[int, int, Dict[str, str]]:
    """Fix a JSONL rollout file using sanitise_input_array logic."""
    total_fixes = 0
    total_drops = 0
    id_map: Dict[str, str] = {}
    dropped_ids: set[str] = set()
    
    out_lines = []
    
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                out_lines.append(line)
                continue
            try:
                obj = json.loads(stripped)
                
                # Treat the single line as an input array of 1 item to reuse logic
                # (Rollout files usually contain the items directly on each line)
                arr, id_map, fixes, drops = sanitise_input_array(
                    [obj], id_map=id_map, dropped_ids=dropped_ids, safe_reasoning=True
                )
                
                if not arr and drops > 0:
                    # Item was entirely dropped
                    total_drops += drops
                    continue
                    
                total_fixes += fixes
                total_drops += drops
                
                if arr:
                    out_lines.append(json.dumps(arr[0], ensure_ascii=False) + "\n")
                
            except json.JSONDecodeError:
                out_lines.append(line)

    if not dry_run and (total_fixes > 0 or total_drops > 0):
        backup_file(path)
        atomic_write_jsonl(path, out_lines)
        
    return total_fixes, total_drops, id_map

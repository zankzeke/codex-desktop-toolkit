"""
fix_codex_ids.py — CLI for offline Codex JSONL history fixing.
"""

import argparse
from pathlib import Path

from history_fixer import fix_rollout_file, fast_check_bad_ids

def run_cli():
    parser = argparse.ArgumentParser(description="Codex Offline ID Fixer")
    parser.add_argument("--fix-all", action="store_true", help="Fix all session files")
    parser.add_argument("--fix", type=str, help="Fix specific keyword/file")
    parser.add_argument("--dry-run", action="store_true", help="Do not write changes")
    parser.add_argument("--force", action="store_true", help="Force fix even if Codex is running")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    if not args.dry_run and not args.force:
        from history_fixer import is_codex_running
        if is_codex_running():
            print("Error: Codex Desktop is currently running.")
            print("Please close it before fixing sessions, or use --force to override.")
            return

    session_dir = Path.home() / ".codex" / "sessions"
    if not session_dir.exists():
        print(f"Error: {session_dir} not found.")
        return
        
    files_to_check = []
    for f in session_dir.rglob("*.jsonl"):
        if args.fix_all:
            files_to_check.append(f)
        elif args.fix and args.fix in str(f):
            files_to_check.append(f)
            
    if not files_to_check:
        print("No files matched criteria.")
        return
        
    print(f"Found {len(files_to_check)} files to check.")
    
    total_fixes = 0
    total_drops = 0
    for f in files_to_check:
        if fast_check_bad_ids(f):
            if args.verbose:
                print(f"Checking {f.name}...")
            fixes, drops, _ = fix_rollout_file(f, dry_run=args.dry_run)
            if fixes > 0 or drops > 0:
                print(f"[{f.name}] Fixes: {fixes}, Drops (reasoning): {drops}")
                total_fixes += fixes
                total_drops += drops
                
    if args.dry_run:
        print(f"\n[DRY RUN] Would fix {total_fixes} IDs and drop {total_drops} reasoning items.")
    else:
        print(f"\nFixed {total_fixes} IDs and dropped {total_drops} reasoning items.")

if __name__ == "__main__":
    run_cli()

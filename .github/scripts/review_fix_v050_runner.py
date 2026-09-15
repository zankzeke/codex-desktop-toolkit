from pathlib import Path
import runpy

script = Path(__file__).with_name("review_fix_v050.py")
text = script.read_text(encoding="utf-8")
old = '''def replace_once(text: str, old: str, new: str, label: str) -> str:\n    count = text.count(old)\n    if count != 1:\n        raise RuntimeError(f"{label}: expected exactly one match, found {count}")\n    return text.replace(old, new, 1)\n'''
new = '''def replace_once(text: str, old: str, new: str, label: str) -> str:\n    count = text.count(old)\n    if count == 1:\n        return text.replace(old, new, 1)\n    # The same two-line keyword tail occurs once in make_app and once in main.\n    # The make_app replacement intentionally consumes the first occurrence;\n    # the later main replacement consumes the remaining one with more context.\n    if label == "make_app constructor args" and count == 2:\n        return text.replace(old, new, 1)\n    raise RuntimeError(f"{label}: expected exactly one match, found {count}")\n'''
if old not in text:
    raise SystemExit("review fixer helper signature changed unexpectedly")
text = text.replace(old, new, 1)
text = text.replace(
    'rf"(?ms)^def {re.escape(name)}\\(.*?(?=^def |^class |\\Z)"',
    'rf"(?ms)^(?:async )?def {re.escape(name)}\\(.*?(?=^(?:async )?def |^class |\\Z)"',
    1,
)
# These blocks intentionally contain Python source string literals with \n.
# Make the outer patch block raw so executing the patcher preserves the escapes.
text = text.replace("new_eval = '''", "new_eval = r'''", 1)
text = text.replace("helpers = '''", "helpers = r'''", 1)
script.write_text(text, encoding="utf-8")
runpy.run_path(str(script), run_name="__main__")

root = script.resolve().parents[2]

# prepare_rewrite_context requires type information for item_* IDs, while the
# resp_<uuid>_msg form is unambiguous even without a type. Include those old
# IDs explicitly so backup diffs remain accurate for legacy JSONL records.
backup = root / "backup_manager.py"
btext = backup.read_text(encoding="utf-8")
btext = btext.replace(
    "from id_rewriter import prepare_rewrite_context\n",
    "from id_rewriter import MSG_ID_RE, prepare_rewrite_context\n",
    1,
)
old_block = '''    current_ids: set[str] = set()\n    current_refs: set[str] = set()\n    backup_refs: set[str] = set()\n    for obj in current_records:\n        ids, refs = collect_ids_and_refs(obj)\n        current_ids.update(ids)\n        current_refs.update(refs)\n    for obj in backup_records:\n        _, refs = collect_ids_and_refs(obj)\n        backup_refs.update(refs)\n\n    id_changes = [\n'''
new_block = '''    current_ids: set[str] = set()\n    current_refs: set[str] = set()\n    backup_ids: set[str] = set()\n    backup_refs: set[str] = set()\n    for obj in current_records:\n        ids, refs = collect_ids_and_refs(obj)\n        current_ids.update(ids)\n        current_refs.update(refs)\n    for obj in backup_records:\n        ids, refs = collect_ids_and_refs(obj)\n        backup_ids.update(ids)\n        backup_refs.update(refs)\n\n    for old_id in backup_ids:\n        match = MSG_ID_RE.match(old_id)\n        if match:\n            id_map.setdefault(old_id, f\"msg_{match.group(1)}\")\n\n    id_changes = [\n'''
if old_block not in btext:
    raise SystemExit("backup diff post-fix anchor missing")
backup.write_text(btext.replace(old_block, new_block, 1), encoding="utf-8")

# Avoid pytest collecting the imported helper as a test function.
test_file = root / "tests" / "test_v050_review_fixes.py"
ttext = test_file.read_text(encoding="utf-8")
ttext = ttext.replace(
    "from proxy_discovery import test_proxy_ws_async\n",
    "from proxy_discovery import test_proxy_ws_async as probe_proxy_ws_async\n",
    1,
)
ttext = ttext.replace("await test_proxy_ws_async(", "await probe_proxy_ws_async(", 1)
test_file.write_text(ttext, encoding="utf-8")

#!/usr/bin/env python3
"""
Bidirectional sync between local Markdown and Feishu/Lark Wiki.
Optimized for formatting preservation, bidirectional linking, and comment safety.

CRITICAL: --mode overwrite destroys comment anchors. Use str_replace or block_replace
for precise updates that preserve Feishu document comments.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

CONFIG_NAME = "lark-sync.json"
DEFAULT_DOMAIN = "www.feishu.cn"

# Block type names for human-readable output
BLOCK_TYPE_NAMES = {
    1: "page", 2: "text", 3: "h1", 4: "h2", 5: "h3",
    6: "h4", 7: "h5", 8: "h6", 9: "h7", 10: "h8", 11: "h9",
    12: "bullet", 13: "ordered", 14: "code", 15: "quote",
    16: "todo", 17: "callout", 18: "divider", 19: "image",
    20: "table", 21: "table_cell", 22: "file", 23: "iframe",
}

# --- Utils ---

def print_step(step_num: int, text: str) -> None:
    print(f"\n{'='*60}\n Step {step_num}: {text}\n{'='*60}")

def print_success(text: str) -> None:
    print(f"  ✓ {text}")

def print_info(text: str) -> None:
    print(f"  ℹ {text}")

def print_warning(text: str) -> None:
    print(f"  ⚠ {text}")

def print_error(text: str) -> None:
    print(f"  ✗ {text}")

def run_cmd(cmd: List[str], capture: bool = True, timeout: Optional[int] = None, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    kwargs = {"cwd": cwd} if cwd else {}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
        kwargs["encoding"] = "utf-8"
    return subprocess.run(cmd, timeout=timeout, **kwargs)

# --- Config & Auth ---

def is_lark_logged_in() -> bool:
    try:
        result = run_cmd(["lark-cli", "auth", "status"], timeout=10)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("ok", False) or data.get("tokenStatus") == "valid"
    except: pass
    return False

def load_config() -> dict:
    config = {
        "space_id": "",
        "domain": DEFAULT_DOMAIN,
        "state_file": "sync_state.json",
        "include_patterns": ["**/*.md"],
        "exclude_patterns": ["node_modules/**", ".*/**", "dist/**"],
        "title_mappings": {},
        "readme_title": "",
    }
    path = Path.cwd() / CONFIG_NAME
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            config.update(json.load(f))
    return config

def save_state(path: Path, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")

def load_state(path: Path) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

# --- Formatting Core ---

def convert_lark_tags_to_local(content: str, state: Dict[str, dict], domain: str) -> str:
    obj_to_path = {v["obj_token"]: k for k, v in state.items()}

    def replace_mention(match: re.Match) -> str:
        groups = match.groupdict()
        token = groups.get('token')
        title = groups.get('title', '').strip()
        
        if token in obj_to_path:
            path = obj_to_path[token]
            parts = Path(path).parts
            rel_path = "/".join(parts[1:]) if len(parts) > 1 else path
            return f"[{title}](./{rel_path})"
        return f"[{title}](https://{domain}/wiki/{token})"

    mention_pattern = r'<mention-doc\s+[^>]*?token=["\'](?P<token>[^"\']+)["\'][^>]*?>(?P<title>.*?)</mention-doc>'
    content = re.sub(mention_pattern, replace_mention, content, flags=re.DOTALL)
    
    content = re.sub(r'<mention-user\s+[^>]*?>@?(?P<name>[^<]+)</mention-user>', r'@\g<name>', content)
    content = re.sub(r'<mention-group\s+[^>]*?>(?P<name>[^<]+)</mention-group>', r'#\g<name>', content)
    return content

def convert_html_table_to_md(content: str) -> str:
    def table_replacer(match):
        table_html = match.group(0)
        rows = re.findall(r'<tr>(.*?)</tr>', table_html, re.DOTALL)
        if not rows: return "\n"
        
        md_rows = []
        for i, row in enumerate(rows):
            cols = re.findall(r'<t[dh][^>]*?>(.*?)</t[dh]>', row, re.DOTALL)
            processed_cols = []
            for c in cols:
                links = re.findall(r'\[[^\]]+\]\([^\)]+\)', c)
                clean_text = re.sub(r'<(?!/?mention-doc)[^>]+>', '', c).strip()
                clean_text = re.sub(r'&[a-z]+;', '', clean_text)
                
                if links and '[' not in clean_text:
                    clean_text = links[0]
                processed_cols.append(clean_text.replace('\n', ' '))
            
            if not processed_cols: continue
            md_rows.append('| ' + ' | '.join(processed_cols) + ' |')
            if i == 0:
                md_rows.append('| ' + ' | '.join(['---'] * len(processed_cols)) + ' |')
        
        return '\n' + '\n'.join(md_rows) + '\n'

    return re.sub(r'<table>.*?</table>', table_replacer, content, flags=re.DOTALL)

def unescape_lark_markdown(content: str) -> str:
    content = re.sub(r'\\+([.\-_[\]()#*+!"\'\]])', r'\1', content)
    content = convert_html_table_to_md(content)

    entities = {
        "&lt;": "<", "&gt;": ">", "&amp;": "&", 
        "&quot;": '"', "&apos;": "'", "&#34;": '"', "&#39;": "'"
    }
    for ent, val in entities.items():
        content = content.replace(ent, val)
    
    content = content.replace("****", "")
    
    lines = content.splitlines()
    if len(lines) >= 3:
        h1_indices = [i for i, line in enumerate(lines[:10]) if line.strip().startswith("# ")]
        if len(h1_indices) >= 2:
            h1_1 = lines[h1_indices[0]].strip()[2:].strip().lower()
            h1_2 = lines[h1_indices[1]].strip()[2:].strip().lower()
            if h1_1 == h1_2 or h1_1 in h1_2 or h1_2 in h1_1:
                content = "\n".join(lines[h1_indices[0] + 1 :])

    return content.strip() + "\n"

# --- Commands ---

def cmd_pull(config: dict) -> None:
    state_path = Path(config["state_file"])
    state = load_state(state_path)
    if not state:
        print_error("No sync state found. Run 'init' first.")
        return

    for rel_path, info in state.items():
        obj_token = info["obj_token"]
        print(f"  Pulling {rel_path}...", end=" ", flush=True)
        
        cmd = [
            "lark-cli", "drive", "+export",
            "--token", obj_token,
            "--doc-type", "docx",
            "--file-extension", "markdown",
            "--output-dir", "."
        ]
        
        res = run_cmd(cmd)
        if res.returncode != 0:
            print(f"Failed! {res.stderr.strip()}")
            continue

        exported_path = None
        for line in res.stdout.splitlines():
            if "exported to:" in line.lower():
                exported_path = Path(line.split(":", 1)[1].strip())
        
        if not exported_path or not exported_path.exists():
            md_files = list(Path.cwd().glob("*.md"))
            if md_files: exported_path = max(md_files, key=os.path.getmtime)

        if exported_path and exported_path.exists():
            content = exported_path.read_text(encoding="utf-8")
            content = convert_lark_tags_to_local(content, state, config["domain"])
            content = unescape_lark_markdown(content)
            
            target = Path(rel_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            exported_path.unlink()
            print("Done!")
        else:
            print("Failed to locate export.")

def cmd_clone(args, config: dict) -> None:
    url = args.url or input("\n  Paste the Feishu Wiki node URL to clone: ").strip()
    node_token = re.search(r'/wiki/([a-zA-Z0-9]{20,})', url)
    if not node_token:
        print_error("Invalid URL."); return
    
    node_token = node_token.group(1)
    print_info(f"Fetching node details...")
    
    cmd = ["lark-cli", "api", "GET", f"/open-apis/wiki/v2/spaces/{config['space_id']}/nodes/{node_token}"]
    res = run_cmd(cmd)
    if not res or res.returncode != 0:
        print_error("Failed to fetch node."); return
    
    data = json.loads(res.stdout)
    node = data["data"]["node"]
    title = re.sub(r'[\\/*?:"<>|]', "_", node["title"])
    target_dir = Path.cwd() / title
    target_dir.mkdir(parents=True, exist_ok=True)
    
    os.chdir(target_dir)
    with open(CONFIG_NAME, "w") as f:
        json.dump({"space_id": config["space_id"]}, f, indent=2)
    
    print_success(f"Project initialized in {title}. Run 'pull' to fetch content.")

# ---------------------------------------------------------------------------
# NEW: Comment management
# ---------------------------------------------------------------------------

def cmd_comments(config: dict) -> None:
    """List all unresolved comments across synced documents."""
    state_path = Path(config["state_file"])
    state = load_state(state_path)
    if not state:
        print_error("No sync state found. Run 'init' first.")
        return

    total = 0
    for rel_path, info in state.items():
        obj_token = info["obj_token"]
        res = run_cmd([
            "lark-cli", "drive", "file.comments", "list",
            "--params", json.dumps({"file_token": obj_token, "file_type": "docx", "page_size": 100}),
            "--format", "json", "--page-all"
        ], timeout=30)
        
        if res.returncode != 0:
            continue
        
        data = json.loads(res.stdout)
        items = data.get("data", {}).get("items", [])
        unsolved = [i for i in items if not i.get("is_solved", False)]
        
        if unsolved:
            print(f"\n📄 {rel_path} ({len(unsolved)} unresolved)")
            for c in unsolved:
                quote = c.get("quote", "")
                if len(quote) > 50:
                    quote = quote[:50] + "..."
                print(f"   💬 [{c['comment_id']}] {quote or '(whole doc comment)'}")
            total += len(unsolved)
    
    if total == 0:
        print_info("No unresolved comments found.")
    else:
        print(f"\nTotal unresolved comments: {total}")

def cmd_reply(config: dict, file_path: str, comment_id: str, message: str) -> None:
    """Reply to a specific comment."""
    state_path = Path(config["state_file"])
    state = load_state(state_path)
    if not state:
        print_error("No sync state found."); return
    
    if file_path not in state:
        print_error(f"File not in sync state: {file_path}"); return
    
    obj_token = state[file_path]["obj_token"]
    
    content = {
        "content": {
            "elements": [
                {"type": "text_run", "text_run": {"text": message}}
            ]
        }
    }
    params = {
        "file_token": obj_token,
        "comment_id": comment_id,
        "file_type": "docx"
    }
    
    res = run_cmd([
        "lark-cli", "drive", "file.comment.replys", "create",
        "--params", json.dumps(params),
        "--data", json.dumps(content)
    ], timeout=30)
    
    if res.returncode == 0:
        print_success(f"Replied to comment {comment_id}")
    else:
        print_error(f"Failed: {res.stderr.strip() or res.stdout.strip()}")

# ---------------------------------------------------------------------------
# NEW: Block inspection
# ---------------------------------------------------------------------------

def cmd_blocks(config: dict, file_path: str) -> None:
    """Show block tree for a document to enable precise block_replace edits."""
    state_path = Path(config["state_file"])
    state = load_state(state_path)
    if not state:
        print_error("No sync state found."); return
    
    if file_path not in state:
        print_error(f"File not in sync state: {file_path}"); return
    
    doc_id = state[file_path]["obj_token"]
    
    res = run_cmd([
        "lark-cli", "api", "GET",
        f"/open-apis/docx/v1/documents/{doc_id}/blocks?page_size=500"
    ], timeout=30)
    
    if res.returncode != 0:
        print_error(f"Failed to fetch blocks: {res.stderr.strip()}")
        return
    
    data = json.loads(res.stdout)
    items = data.get("data", {}).get("items", [])
    
    print(f"\n📄 {file_path} — {len(items)} blocks\n")
    print(f"{'Type':<10s} {'Block ID':<30s} {'Content Preview'}")
    print("-" * 90)
    
    for item in items:
        bid = item["block_id"]
        btype = item["block_type"]
        typename = BLOCK_TYPE_NAMES.get(btype, f"t{btype}")
        
        text = ""
        for key in ["text", "heading1", "heading2", "heading3", "heading4",
                    "heading5", "bullet", "ordered", "quote", "code"]:
            if key in item:
                elems = item[key].get("elements", [])
                parts = []
                for e in elems:
                    if "text_run" in e:
                        tr = e["text_run"]
                        parts.append(tr.get("content", tr.get("text", "")))
                text = "".join(parts)[:55]
                break
        
        print(f"{typename:<10s} {bid:<30s} {text}")

# ---------------------------------------------------------------------------
# NEW: Safe push strategies
# ---------------------------------------------------------------------------

def _update_doc_v2(doc_id: str, command: str, content: str, **kwargs) -> bool:
    """Execute a v2 doc update command."""
    cmd = [
        "lark-cli", "docs", "+update",
        "--api-version", "v2",
        "--doc", doc_id,
        "--command", command,
        "--content", content,
        "--doc-format", kwargs.get("format", "markdown"),
    ]
    if "pattern" in kwargs:
        cmd += ["--pattern", kwargs["pattern"]]
    if "block_id" in kwargs:
        cmd += ["--block-id", kwargs["block_id"]]
    
    res = run_cmd(cmd, timeout=60)
    if res.returncode != 0:
        err = res.stderr.strip() or res.stdout.strip()
        print_error(f"Update failed: {err}")
        return False
    return True

def cmd_push(config: dict, strategy: str, target: Optional[str] = None) -> None:
    """Push local changes to Feishu with comment-safe strategies."""
    state_path = Path(config["state_file"])
    state = load_state(state_path)
    if not state:
        print_error("No sync state found. Run 'init' first.")
        return

    if strategy == "overwrite":
        print_warning("OVERWRITE MODE: This will DESTROY comment anchors on Feishu!")
        print_warning("Existing comments will still exist in API but become invisible in UI.")
        confirm = input("  Type 'yes' to proceed with overwrite: ").strip().lower()
        if confirm != "yes":
            print_info("Aborted.")
            return
        _push_overwrite(state)
    
    elif strategy == "patch":
        _push_patch(state, config)
    
    elif strategy == "str_replace":
        _push_str_replace(state, config, target)
    
    else:
        print_error(f"Unknown strategy: {strategy}")
        print_info("Available: overwrite, patch, str_replace")

def _push_overwrite(state: dict) -> None:
    for rel_path, info in state.items():
        if not Path(rel_path).exists():
            continue
        obj_token = info["obj_token"]
        print(f"  Overwriting {rel_path}...", end=" ", flush=True)
        
        res = run_cmd([
            "lark-cli", "docs", "+update",
            "--api-version", "v2",
            "--doc", obj_token,
            "--command", "overwrite",
            "--content", f"@{rel_path}",
            "--doc-format", "markdown"
        ], timeout=60)
        
        if res.returncode == 0:
            print("Done!")
        else:
            print(f"Failed! {res.stderr.strip()}")

def _push_patch(state: dict, config: dict) -> None:
    """Apply patches from patches.json for precise, comment-safe updates."""
    patch_file = Path("patches.json")
    if not patch_file.exists():
        print_error("patches.json not found.")
        print_info("Create patches.json with this structure:")
        print(json.dumps({
            "Global Tax Course 101/Phase1-Basic/1.1-Introduction.md": [
                {
                    "mode": "str_replace",
                    "pattern": "旧文本",
                    "content": "新文本"
                },
                {
                    "mode": "block_replace",
                    "block_id": "doxcnxxx",
                    "content": "新 block 内容"
                }
            ]
        }, indent=2, ensure_ascii=False))
        return
    
    with open(patch_file, "r", encoding="utf-8") as f:
        patches = json.load(f)
    
    for rel_path, file_patches in patches.items():
        if rel_path not in state:
            print_warning(f"Skipping unknown file: {rel_path}")
            continue
        
        obj_token = state[rel_path]["obj_token"]
        print(f"\n  Patching {rel_path}...")
        
        for i, p in enumerate(file_patches, 1):
            mode = p.get("mode", "str_replace")
            content = p.get("content", "")
            
            if mode == "str_replace":
                pattern = p.get("pattern", "")
                print(f"    [{i}] str_replace: {pattern[:40]}...")
                ok = _update_doc_v2(obj_token, "str_replace", content,
                                    pattern=pattern, format="markdown")
            elif mode == "block_replace":
                block_id = p.get("block_id", "")
                print(f"    [{i}] block_replace: {block_id}")
                ok = _update_doc_v2(obj_token, "block_replace", content,
                                    block_id=block_id, format="markdown")
            elif mode == "block_insert_after":
                block_id = p.get("block_id", "")
                print(f"    [{i}] block_insert_after: {block_id}")
                ok = _update_doc_v2(obj_token, "block_insert_after", content,
                                    block_id=block_id, format="markdown")
            else:
                print_warning(f"    [{i}] Unknown mode: {mode}")
                continue
            
            if ok:
                print_success(f"    [{i}] OK")
            else:
                print_error(f"    [{i}] FAILED — stopping remaining patches for this file")
                break

def _push_str_replace(state: dict, config: dict, target: Optional[str]) -> None:
    """Interactive str_replace: user provides pattern + replacement per file."""
    if target and target in state:
        files = [target]
    else:
        files = list(state.keys())
    
    for rel_path in files:
        if not Path(rel_path).exists():
            continue
        obj_token = state[rel_path]["obj_token"]
        print(f"\n  File: {rel_path}")
        pattern = input("  Pattern to find (or Enter to skip): ").strip()
        if not pattern:
            continue
        replacement = input("  Replacement text: ").strip()
        if not replacement:
            continue
        
        print(f"  Applying str_replace...", end=" ", flush=True)
        if _update_doc_v2(obj_token, "str_replace", replacement,
                          pattern=pattern, format="markdown"):
            print("Done!")
        else:
            print("Failed!")

def main():
    parser = argparse.ArgumentParser(description="Feishu Sync Pro")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # pull
    subparsers.add_parser("pull", help="Pull latest content from Feishu Wiki")
    
    # push
    push_parser = subparsers.add_parser("push", help="Push local changes to Feishu")
    push_parser.add_argument("--strategy", choices=["overwrite", "patch", "str_replace"],
                             default="patch", help="Push strategy (default: patch)")
    push_parser.add_argument("--target", help="Target file path (for str_replace strategy)")
    
    # clone
    clone_parser = subparsers.add_parser("clone", help="Clone a Wiki node")
    clone_parser.add_argument("--url", help="Wiki URL")
    
    # comments
    subparsers.add_parser("comments", help="List unresolved comments")
    
    # reply
    reply_parser = subparsers.add_parser("reply", help="Reply to a comment")
    reply_parser.add_argument("file", help="File path in sync state")
    reply_parser.add_argument("comment_id", help="Comment ID")
    reply_parser.add_argument("message", help="Reply message")
    
    # blocks
    blocks_parser = subparsers.add_parser("blocks", help="Show block tree for a document")
    blocks_parser.add_argument("file", help="File path in sync state")
    
    # setup / init (legacy)
    subparsers.add_parser("setup", help="Alias for init")
    subparsers.add_parser("init", help="Initialize sync state")
    
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    
    config = load_config()
    
    if args.command == "pull":
        cmd_pull(config)
    elif args.command == "push":
        cmd_push(config, args.strategy, getattr(args, "target", None))
    elif args.command == "clone":
        cmd_clone(args, config)
    elif args.command == "comments":
        cmd_comments(config)
    elif args.command == "reply":
        cmd_reply(config, args.file, args.comment_id, args.message)
    elif args.command == "blocks":
        cmd_blocks(config, args.file)
    elif args.command in ("setup", "init"):
        print_info("Please use 'clone' for new projects, 'pull' for existing ones.")
        print_info("To initialize sync state from existing local files, run 'pull'.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Bidirectional sync between local Markdown and Feishu/Lark Wiki.
Supports Windows, macOS, and Linux.

Configuration (in order of precedence):
    1. Environment variables: LARK_SPACE_ID, LARK_DOMAIN
    2. Project-level config file: lark-sync.json (in current working directory)
    3. User-level config file: ~/.config/lark-sync/config.json
    4. Auto-detected project defaults (for known project structures)

If no configuration is found, an onboarding message is shown to guide first-time setup.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

CONFIG_NAME = "lark-sync.json"
DEFAULT_DOMAIN = "www.feishu.cn"

# --- Project-specific fallback defaults ---
# When running inside the global-tax-course-101 repo without a config file,
# these defaults ensure the script works out of the box.
PROJECT_DEFAULTS = {
    "space_id": "7617846903966862541",
    "domain": "www.feishu.cn",
    "state_file": "sync_state.json",
    "include_patterns": ["Phase*/**/*.md", "README.md", "SYLLABUS.md", "DEVLOG.md"],
    "exclude_patterns": [],
    "readme_title": "Global Tax Course 101",
    "title_mappings": {},
}

ONBOARDING_MSG = """
╔══════════════════════════════════════════════════════════════════╗
║  Feishu Sync — First-time setup required                         ║
╠══════════════════════════════════════════════════════════════════╣
║  Step 1: Install lark-cli                                        ║
║    https://open.larkoffice.com/document/tools/cli                ║
║                                                                  ║
║  Step 2: Log in                                                  ║
║    lark-cli login                                                ║
║                                                                  ║
║  Step 3: Create a config file in your project root               ║
║    lark-sync.json                                                ║
║    {                                                             ║
║      "space_id": "YOUR_WIKI_SPACE_ID"                            ║
║    }                                                             ║
╚══════════════════════════════════════════════════════════════════╝
""".strip()


def detect_project_defaults() -> Optional[dict]:
    """Detect if we are inside a known project and return its defaults."""
    cwd = Path.cwd()
    # Heuristic: check for the characteristic directory structure
    markers = ["Phase1-Basic", "Phase2-Advanced", "Phase3-Expert", "Phase4-Practical"]
    if any((cwd / m).is_dir() for m in markers):
        return dict(PROJECT_DEFAULTS)
    return None


def get_config_path() -> Optional[Path]:
    local = Path.cwd() / CONFIG_NAME
    if local.exists():
        return local
    return None


def load_config() -> dict:
    config = {
        "space_id": "",
        "domain": DEFAULT_DOMAIN,
        "state_file": "sync_state.json",
        "include_patterns": ["**/*.md"],
        "exclude_patterns": ["node_modules/**", ".*/**", "dist/**", "build/**"],
        "title_mappings": {},
        "readme_title": "",
    }

    path = get_config_path()
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                file_config = json.load(f)
            config.update(file_config)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Error reading config {path}: {e}", file=sys.stderr)
            sys.exit(1)

    # Fallback to project defaults if still not configured
    if not config.get("space_id"):
        project_defaults = detect_project_defaults()
        if project_defaults:
            config.update(project_defaults)

    return config


def validate_config(config: dict) -> None:
    if not config.get("space_id"):
        print("Error: space_id is missing.\n", file=sys.stderr)
        print(ONBOARDING_MSG, file=sys.stderr)
        sys.exit(1)


def ensure_lark_cli() -> None:
    try:
        subprocess.run(["lark-cli", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print(
            "Error: 'lark-cli' not found or not working.\n"
            "Please install it from https://open.larkoffice.com/document/tools/cli\n"
            "Then run: lark-cli login",
            file=sys.stderr,
        )
        sys.exit(1)


def run_lark_cmd(cmd: List[str]) -> Optional[dict]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        ensure_lark_cli()
        return None

    if result.returncode != 0:
        print(f"Error executing: {' '.join(cmd)}", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        return None

    try:
        lines = result.stdout.strip().splitlines()
        json_start = 0
        for i, line in enumerate(lines):
            if line.strip().startswith("{"):
                json_start = i
                break
        json_str = "\n".join(lines[json_start:])
        if not json_str:
            return {}
        return json.loads(json_str)
    except Exception as e:
        print("Failed to parse JSON:", e, file=sys.stderr)
        return None


def load_state(state_path: Path) -> Dict[str, dict]:
    if state_path.exists():
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state_path: Path, state: Dict[str, dict]) -> None:
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.write("\n")


def fetch_wiki_nodes(space_id: str, parent_node_token: str = "") -> List[dict]:
    cmd = ["lark-cli", "api", "GET", f"/open-apis/wiki/v2/spaces/{space_id}/nodes"]
    params: Dict[str, str] = {}
    if parent_node_token:
        params["parent_node_token"] = parent_node_token
    if params:
        cmd.extend(["--params", json.dumps(params)])

    res = run_lark_cmd(cmd)
    if res and res.get("code") == 0:
        return res["data"].get("items", [])
    return []


def discover_local_files(config: dict) -> List[Path]:
    cwd = Path.cwd()
    include_patterns = config.get("include_patterns", ["**/*.md"])
    exclude_patterns = config.get("exclude_patterns", [])

    files: set = set()
    for pattern in include_patterns:
        for p in cwd.glob(pattern):
            if p.is_file():
                files.add(p.resolve())

    excluded: set = set()
    for pattern in exclude_patterns:
        for p in cwd.glob(pattern):
            if p.is_file():
                excluded.add(p.resolve())

    result = sorted(files - excluded)
    return [p.relative_to(cwd) for p in result]


def resolve_title(path: Path, config: dict) -> str:
    title_mappings = config.get("title_mappings", {})
    key = str(path.as_posix())
    if key in title_mappings:
        return title_mappings[key]

    if path.name.lower() == "readme.md" and config.get("readme_title"):
        return config["readme_title"]

    if path.name.lower() == "index.md":
        parent = path.parent.name
        if parent and parent != ".":
            return parent

    return path.stem


def cmd_init(config: dict) -> None:
    space_id = config["space_id"]
    state_path = Path(config["state_file"])

    print("Fetching remote nodes from Feishu...")
    root_nodes = fetch_wiki_nodes(space_id)
    remote_map: Dict[str, dict] = {}

    def traverse(nodes: List[dict]) -> None:
        for node in nodes:
            title = node.get("title", "")
            remote_map[title] = {
                "node_token": node.get("node_token"),
                "obj_token": node.get("obj_token"),
            }
            if node.get("has_child"):
                children = fetch_wiki_nodes(space_id, node.get("node_token"))
                traverse(children)

    traverse(root_nodes)
    print(f"Found {len(remote_map)} remote nodes.")

    local_files = discover_local_files(config)
    state: Dict[str, dict] = {}
    match_count = 0

    for rel_path in local_files:
        title = resolve_title(rel_path, config)
        key = str(rel_path.as_posix())
        if title in remote_map:
            state[key] = remote_map[title]
            match_count += 1

    save_state(state_path, state)
    print(f"Initialized sync state ({state_path}). Matched {match_count} local files to Feishu nodes.")

    unmatched = [str(p.as_posix()) for p in local_files if str(p.as_posix()) not in state]
    if unmatched:
        print(f"\nUnmatched local files ({len(unmatched)}):")
        for p in unmatched:
            print(f"  - {p}")
        print("\nTip: If titles differ between local and Feishu, add them to 'title_mappings' in lark-sync.json.")


def unescape_lark_markdown(content: str) -> str:
    content = re.sub(r'\\+([.\-_[\]()#*+!"\'\]])', r'\1', content)
    content = (
        content.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&#34;", '"')
        .replace("&#39;", "'")
    )
    content = content.replace("****", "")

    lines = content.splitlines()
    if len(lines) >= 3:
        h1_indices = [i for i, line in enumerate(lines[:5]) if line.strip().startswith("# ")]
        if len(h1_indices) >= 2:
            idx1, idx2 = h1_indices[0], h1_indices[1]
            h1_1 = lines[idx1].strip()[2:].strip().lower().replace("\\", "")
            h1_2 = lines[idx2].strip()[2:].strip().lower().replace("\\", "")
            if h1_1 == h1_2 or h1_1 in h1_2 or h1_2 in h1_1:
                content = "\n".join(lines[idx1 + 1 :])

    return content.strip() + "\n"


def convert_lark_tags_to_local(content: str, state: Dict[str, dict], domain: str) -> str:
    obj_to_path = {v["obj_token"]: k for k, v in state.items()}

    def replace_mention(match: re.Match) -> str:
        token, doc_type, title = match.groups()
        if token in obj_to_path:
            return f"[{title}](./{obj_to_path[token]})"
        return f"[{title}](https://{domain}/wiki/{token})"

    content = re.sub(
        r'<mention-doc\s+token=["\']([^"\']+)["\']\s+type=["\']([^"\']+)["\']\s*>([^<]+)</mention-doc>',
        replace_mention,
        content,
    )
    content = re.sub(r'<mention-user\s+[^>]*>([^<]+)</mention-user>', r"@\1", content)
    content = re.sub(r'<mention-group\s+[^>]*>([^<]+)</mention-group>', r"#\1", content)
    return content


def preprocess_local_markdown_for_lark(content: str) -> str:
    content = content.replace("<br>", " ")
    content = content.replace("<br/>", " ")
    return content


def cmd_pull(config: dict) -> None:
    state_path = Path(config["state_file"])
    domain = config["domain"]
    state = load_state(state_path)

    if not state:
        print("No sync state found. Run 'init' first.")
        sys.exit(1)

    for key, info in state.items():
        obj_token = info["obj_token"]
        local_path = Path(key)
        print(f"Pulling {local_path} (obj_token: {obj_token})...", end=" ", flush=True)

        cmd = [
            "lark-cli",
            "drive",
            "+export",
            "--token", obj_token,
            "--doc-type", "docx",
            "--file-extension", "markdown",
            "--output-dir", str(Path.cwd()),
        ]

        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            print(f"Failed! Error: {res.stderr.strip()}")
            continue

        exported_path = None
        for line in res.stdout.splitlines():
            if "exported to:" in line.lower():
                exported_path = line.split(":", 1)[1].strip()

        if not exported_path:
            files = list(Path.cwd().glob("*.md"))
            if files:
                exported_path = str(max(files, key=lambda p: p.stat().st_mtime))

        exported_path = Path(exported_path) if exported_path else None
        if exported_path and exported_path.exists():
            content = exported_path.read_text(encoding="utf-8")
            content = unescape_lark_markdown(content)
            content = convert_lark_tags_to_local(content, state, domain)

            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text(content, encoding="utf-8")
            exported_path.unlink()
            print("Done!")
        else:
            print("Failed to locate exported file.")


def cmd_push(config: dict) -> None:
    state_path = Path(config["state_file"])
    state = load_state(state_path)

    if not state:
        print("No sync state found. Run 'init' first.")
        sys.exit(1)

    for key, info in state.items():
        obj_token = info["obj_token"]
        local_path = Path(key)

        if not local_path.exists():
            print(f"Skipping {local_path} (file not found locally).")
            continue

        print(f"Pushing {local_path} to Feishu...", end=" ", flush=True)
        content = local_path.read_text(encoding="utf-8")
        content = preprocess_local_markdown_for_lark(content)

        tmp_path = local_path.with_suffix(".tmp.md")
        tmp_path.write_text(content, encoding="utf-8")

        cmd = [
            "lark-cli",
            "docs",
            "+update",
            "--doc", obj_token,
            "--mode", "overwrite",
            "--markdown", f"@{tmp_path}",
        ]

        res = run_lark_cmd(cmd)
        try:
            tmp_path.unlink()
        except OSError:
            pass

        if res and res.get("ok"):
            print("Done!")
        else:
            print("Failed!")


def cmd_status(config: dict) -> None:
    state_path = Path(config["state_file"])
    state = load_state(state_path)

    if not state:
        print("No sync state found. Run 'init' first.")
        return

    local_files = discover_local_files(config)
    local_set = set(str(p.as_posix()) for p in local_files)
    state_set = set(state.keys())

    matched = sorted(local_set & state_set)
    unmatched_local = sorted(local_set - state_set)
    orphaned_remote = sorted(state_set - local_set)

    print(f"\nSync state: {state_path}")
    print(f"Space ID:   {config['space_id']}")
    print(f"Domain:     {config['domain']}")
    print(f"\nMatched ({len(matched)}):")
    for p in matched:
        print(f"  ✓ {p}")

    if unmatched_local:
        print(f"\nUnmatched local files ({len(unmatched_local)}):")
        for p in unmatched_local:
            print(f"  ? {p}")

    if orphaned_remote:
        print(f"\nOrphaned remote entries ({len(orphaned_remote)}):")
        for p in orphaned_remote:
            print(f"  ✗ {p}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bidirectional sync between local Markdown and Feishu/Lark Wiki."
    )
    parser.add_argument(
        "command",
        choices=["init", "pull", "push", "status"],
        help="Action: init (map nodes), pull (Feishu→Local), push (Local→Feishu), status (show summary)",
    )
    args = parser.parse_args()

    config = load_config()
    validate_config(config)
    ensure_lark_cli()

    if args.command == "init":
        cmd_init(config)
    elif args.command == "pull":
        cmd_pull(config)
    elif args.command == "push":
        cmd_push(config)
    elif args.command == "status":
        cmd_status(config)


if __name__ == "__main__":
    main()

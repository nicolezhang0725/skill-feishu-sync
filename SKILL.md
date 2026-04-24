---
name: feishu-sync
description: >
  Bidirectional sync between local Markdown files and Feishu (飞书) / Lark Wiki documents.
  Use this skill whenever the user wants to sync, pull, or push markdown files to/from
  Feishu/Lark, keep a local wiki in sync with a remote Feishu knowledge base, export Feishu
  docs to local markdown, or update Feishu wiki pages from local files. Trigger on phrases
  like 飞书同步, 飞书双向同步, feishu sync, wiki sync, pull docs from feishu, push markdown
  to lark, or bidirectional document synchronization with Feishu.
---

# Feishu Sync

A cross-platform skill for bidirectional synchronization between local Markdown repositories and Feishu (飞书) / Lark Wiki spaces.

## Compatibility

- **OS**: Windows, macOS, Linux
- **Python**: 3.8 or later
- **Dependencies**: `lark-cli` installed and authenticated
  - Install from: https://open.larkoffice.com/document/tools/cli
  - After installation, run `lark-cli login` to authenticate

## Onboarding

If the user is setting this up for the first time or does not have a config file yet, guide them through setup step by step. Do not overwhelm them with options — only ask for the bare minimum required to get started.

### Already in a project with built-in defaults?

Some repositories (e.g., `global-tax-course-101`) ship with a `lark-sync.json` or have project defaults baked into `sync_lark.py`. In these projects, **no manual configuration is needed** — the script auto-detects the correct `space_id`, file patterns, and title mappings. Skip to Step 4 (`init`).

### Step 1: Verify lark-cli

Check whether `lark-cli` is installed:

```bash
lark-cli --version
```

If it fails, instruct the user to install it and run `lark-cli login`.

### Step 2: Ask for the Space ID

The only required piece of information is the Feishu Wiki **space_id**. Prompt the user:

> "Please provide your Feishu Wiki space ID. You can find it in the Wiki URL (the long numeric string after `/space/`)."

### Step 3: Create the minimal config

Write `lark-sync.json` in the project root with **only** the fields the user has explicitly provided. Start with the absolute minimum:

```json
{
  "space_id": "USER_PROVIDED_VALUE"
}
```

If the user mentions that their root `README.md` maps to a differently titled document on Feishu, ask what that title is and add:

```json
{
  "space_id": "USER_PROVIDED_VALUE",
  "readme_title": "The Root Document Title"
}
```

Do **not** add `title_mappings`, custom `include_patterns`, or other advanced fields unless the user explicitly asks for them or `init` fails to match files.

### Step 4: Run init and review matches

Run `init`, then show the user:
- How many files were matched
- Which files were **not** matched (if any)

If unmatched files exist, ask whether the titles on Feishu differ. Only then introduce `title_mappings` for the specific mismatched files.

## Daily workflow (after onboarding)

```bash
# Pull remote changes to local
python3 ~/.claude/skills/feishu-sync/scripts/sync.py pull

# Push local changes to remote
python3 ~/.claude/skills/feishu-sync/scripts/sync.py push

# Check mapping status
python3 ~/.claude/skills/feishu-sync/scripts/sync.py status
```

On Windows PowerShell:
```powershell
python3 $env:USERPROFILE\.claude\skills\feishu-sync\scripts\sync.py pull
```

Or copy `scripts/sync.py` into your project root and run it directly:
```bash
python3 sync.py pull
```

## How it works

This skill wraps a Python script that communicates with the Feishu/Lark Open API via `lark-cli`.

Three core operations:
- **init** — Scans local Markdown files and remote Wiki nodes, then builds a mapping file (`sync_state.json`) by matching document titles.
- **pull** — Exports mapped Feishu documents as Markdown and overwrites local files. Cleans up Feishu-specific formatting (escaped characters, mention tags, duplicate H1s).
- **push** — Reads local Markdown files and overwrites the corresponding Feishu documents via `docs +update --mode overwrite`.

## Configuration reference

The script reads its configuration from **`lark-sync.json`** in the current working directory (project root). No other config sources are used — this keeps setup explicit and self-contained.

### Minimal config

```json
{
  "space_id": "YOUR_WIKI_SPACE_ID"
}
```

### Full config (only add what you need)

```json
{
  "space_id": "YOUR_WIKI_SPACE_ID",
  "domain": "www.feishu.cn",
  "state_file": "sync_state.json",
  "include_patterns": ["**/*.md"],
  "exclude_patterns": ["node_modules/**", ".*/**", "dist/**"],
  "readme_title": "My Project Root Doc",
  "title_mappings": {
    "docs/guide/Index.md": "Guide"
  }
}
```

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `space_id` | **Yes** | — | Feishu Wiki space ID |
| `domain` | No | `www.feishu.cn` | API domain (`www.larksuite.com` for international) |
| `state_file` | No | `sync_state.json` | Path to the local↔remote mapping file |
| `include_patterns` | No | `["**/*.md"]` | Glob patterns for discovering local Markdown files |
| `exclude_patterns` | No | `["node_modules/**", ".*/**", "dist/**"]` | Patterns to exclude |
| `readme_title` | No | — | Title on Feishu that maps to local `README.md` |
| `title_mappings` | No | `{}` | Manual `relative/path -> title` overrides for files whose titles differ |

## Best practices

1. **Run `init` after structural changes** — When files are renamed or new top-level wiki nodes are added, re-run `init` to rebuild `sync_state.json`.
2. **Version-control `sync_state.json`** — It is the source of truth for the local↔remote mapping. Commit it so teammates share the same mappings.
3. **Review before `push`** — `push` uses `--mode overwrite`, which replaces the entire remote document. Make sure local changes are intentional.
4. **Pull before editing** — To avoid overwriting remote changes, always `pull` before starting local edits.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `lark-cli not found` | CLI not in PATH | Re-install or add to PATH, then `lark-cli login` |
| `space_id is missing` | No config file | Follow the Onboarding steps above |
| `No sync state found` | `init` not run yet | Run `init` first |
| 0 matched files on `init` | Titles mismatch | Add `readme_title` or `title_mappings` |
| Push says "Failed!" | Token expired / permissions | Re-run `lark-cli login` and check Wiki edit permissions |

# Mobile Code Context MCP Server

An MCP (Model Context Protocol) server that indexes mobile codebases (Android/iOS) and provides semantic search + architecture context to IDE agents (VS Code Copilot, Claude, Cursor).

## What it does

- **Auto-detects** your project platform (Android Kotlin / iOS Swift)
- **Indexes** your codebase using AST-aware chunking + CodeRankEmbed embeddings
- **Auto-discovers** base architecture files via import frequency (fan-in) analysis
- **Selects exemplars** — complete feature vertical slices for pattern reference
- **Reindexes incrementally** on branch changes (git diff-based delta)
- **Learns** from your feedback — mandatory context improves over time

## Installation

### Quick Install (One Command)

From your Android/iOS project root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/gokhandiker/mobile-code-context/main/scripts/install.sh)
```

This command will:
- clone/update `mobile-code-context` into `~/.mobile-code-context`
- create a dedicated venv and install the package
- auto-write/merge `.vscode/settings.json` with your current folder as `--repo`

Optional flags:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/gokhandiker/mobile-code-context/main/scripts/install.sh) --mode uv
bash <(curl -fsSL https://raw.githubusercontent.com/gokhandiker/mobile-code-context/main/scripts/install.sh) --project /absolute/path/to/project
```

### Manual Install

```bash
# Clone and install
git clone <repo-url>
cd mobile-code-context
python3 -m pip install -e .

# Or with uv
uv pip install -e .
```

## One-Command VS Code Setup

From your Android/iOS project root, run:

```bash
python3 /path/to/mobile-code-context/scripts/setup-vscode-mcp.py
```

This automatically writes/merges `.vscode/settings.json` and sets `--repo` to your current folder.

If you prefer `uv` mode instead of an installed CLI:

```bash
python3 /path/to/mobile-code-context/scripts/setup-vscode-mcp.py --mode uv
```

## Configuration

### VS Code (Copilot / Claude)

Add to your VS Code `settings.json`:

```json
{
  "mcpServers": {
    "mobile-code-context": {
      "command": "mobile-code-context",
      "args": ["--repo", "/path/to/your/mobile/project"],
      "env": {}
    }
  }
}
```

Or using `uv`:

```json
{
  "mcpServers": {
    "mobile-code-context": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mobile-code-context", "mobile-code-context", "--repo", "/path/to/your/mobile/project"]
    }
  }
}
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCC_REPO_PATH` | (required) | Path to mobile project repository |
| `MCC_DATA_DIR` | `.mcp-context` | Directory name for index data (relative to repo) |
| `MCC_EMBEDDING_MODEL` | `nomic-ai/CodeRankEmbed` | Embedding model name |
| `MCC_EMBEDDING_BATCH_SIZE` | `32` | Batch size for embedding generation |
| `MCC_SEARCH_TOP_K` | `10` | Default search results count |
| `MCC_REINDEX_ON_TOOL_CALL` | `true` | Auto-reindex when HEAD changes |

## Tools

| Tool | Description |
|------|-------------|
| `search_code` | Semantic search over indexed codebase |
| `get_architecture_context` | Returns base architecture + exemplar patterns |
| `get_module_info` | Module details (dependencies, packages, files) |
| `find_feature_module` | Find feature module by name |
| `expand_to_siblings` | Find related MVI/MVVM files (ViewModel → Screen, State, etc.) |
| `get_project_overview` | High-level project summary |
| `suggest_mandatory_addition` | Add a file to mandatory context |

## How it works

### First Run (~3-5 minutes)
1. Detects platform (Android/iOS) from build files
2. Scans all source files (.kt/.swift)
3. Parses with tree-sitter (AST extraction)
4. Chunks at function/class boundaries (size-adaptive)
5. Generates embeddings (CodeRankEmbed, 768-dim)
6. Stores in LanceDB (local, file-based)
7. Runs fan-in analysis → detects base architecture
8. Scores feature modules → selects best exemplar

### Subsequent Runs (~5-10 seconds)
1. Checks if git HEAD changed
2. If yes: `git diff` → only re-processes changed files
3. If base files changed → re-extracts mandatory context
4. All tool calls use fresh index

### Data Storage

All data stored in `<repo>/.mcp-context/`:
```
.mcp-context/
├── index.lance/          # LanceDB vector store
├── mandatory.json        # Auto-detected + user-confirmed base files
├── last_indexed_commit   # Git commit tracking
└── server.log            # Server logs
```

Add `.mcp-context/` to your `.gitignore`.

## Supported Platforms

| Platform | Language | Parser | Module Detection |
|----------|----------|--------|-----------------|
| Android | Kotlin (.kt/.kts) | tree-sitter-kotlin | Gradle modules |
| iOS | Swift (.swift) | tree-sitter-swift | Xcode targets / SPM |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/

# Type check
mypy src/
```

## Architecture

```
MCP Server (stdio)
├── Platform Detector (Android/iOS auto-detect)
├── Indexer Pipeline
│   ├── Scanner (file discovery)
│   ├── Parser (tree-sitter AST)
│   ├── Chunker (size-adaptive, AST-aware)
│   ├── Embedder (CodeRankEmbed)
│   └── Store (LanceDB)
├── Context Engine
│   ├── Fan-in Analyzer (import graph → base files)
│   ├── Vertical Slice Scorer (layer completeness)
│   ├── Anchored Extractor (method extraction)
│   └── Learner (user feedback persistence)
├── Reindexer (git diff delta)
└── MCP Tools (7 tools)
```

## License

MIT

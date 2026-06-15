#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/gokhandiker/mobile-code-context.git"
INSTALL_DIR="${HOME}/.mobile-code-context"
PROJECT_DIR="${PWD}"
MODE="pip"
INDEX_CHECK="1"

log_step() {
  echo
  echo "[$1] $2"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --)
      shift
      ;;
    --project)
      PROJECT_DIR="$2"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="$2"
      shift 2
      ;;
    --mode)
      MODE="$2"
      shift 2
      ;;
    --repo-url)
      REPO_URL="$2"
      shift 2
      ;;
    --skip-index-check)
      INDEX_CHECK="0"
      shift
      ;;
    *)
      echo "Unknown arg: $1"
      echo "Usage: install.sh [--project <path>] [--install-dir <path>] [--mode pip|uv] [--repo-url <url>] [--skip-index-check]"
      exit 1
      ;;
  esac
done

if ! command -v git >/dev/null 2>&1; then
  echo "git not found"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found"
  exit 1
fi

if [[ "${MODE}" != "pip" && "${MODE}" != "uv" ]]; then
  echo "--mode must be 'pip' or 'uv'"
  exit 1
fi

if [[ ! -d "${PROJECT_DIR}" ]]; then
  echo "Project directory not found: ${PROJECT_DIR}"
  exit 1
fi

echo "Installing mobile-code-context"
echo "Project: ${PROJECT_DIR}"
echo "Install dir: ${INSTALL_DIR}"
echo "Mode: ${MODE}"

log_step "1/5" "Fetching installer repository"
if [[ -d "${INSTALL_DIR}/.git" ]]; then
  git -C "${INSTALL_DIR}" pull --ff-only
else
  rm -rf "${INSTALL_DIR}"
  git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

log_step "2/5" "Creating virtual environment"
python3 -m venv "${INSTALL_DIR}/.venv"
"${INSTALL_DIR}/.venv/bin/python" -m pip install --upgrade pip >/dev/null

log_step "3/5" "Installing package dependencies"
"${INSTALL_DIR}/.venv/bin/python" -m pip install -e "${INSTALL_DIR}"

log_step "4/5" "Writing VS Code MCP configuration"
if [[ "${MODE}" == "uv" ]]; then
  "${INSTALL_DIR}/.venv/bin/python" "${INSTALL_DIR}/scripts/setup-vscode-mcp.py" \
    --project "${PROJECT_DIR}" \
    --mode uv
else
  "${INSTALL_DIR}/.venv/bin/python" "${INSTALL_DIR}/scripts/setup-vscode-mcp.py" \
    --project "${PROJECT_DIR}" \
    --mode pip \
    --command "${INSTALL_DIR}/.venv/bin/mobile-code-context"
fi

if [[ "${INDEX_CHECK}" == "1" ]]; then
  log_step "5/5" "Indexing check (this can take a few minutes on first run)"
  MCC_PROJECT_DIR="${PROJECT_DIR}" "${INSTALL_DIR}/.venv/bin/python" - <<'PY'
import asyncio
import os
from pathlib import Path

from mobile_code_context.config import Settings
from mobile_code_context.engine import Engine


async def _run() -> None:
    repo = Path(os.environ["MCC_PROJECT_DIR"]).resolve()
    engine = Engine(Settings(repo_path=repo))
    result = await engine.initialize()
    print("INDEX_STATUS: SUCCESS")
    print(f"INDEX_MESSAGE: {result}")


try:
    asyncio.run(_run())
except Exception as exc:
    print("INDEX_STATUS: FAILED")
    print(f"INDEX_ERROR: {exc}")
    raise
PY
else
  log_step "5/5" "Indexing check skipped (--skip-index-check)"
fi

echo
echo "Done."
echo "Status: READY"
echo "VS Code MCP config updated at: ${PROJECT_DIR}/.vscode/settings.json"
echo "Restart VS Code or reload window if MCP server is not listed yet."

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ "${1:-}" != "--yes" ]]; then
  cat <<'EOF'
This will permanently delete Prompt Viewer runtime data:
  - photos/
  - .prompt_viewer_thumbs/
  - docker-data/
  - prompt_viewer.sqlite3*

It will not delete source files or test fixtures.
EOF
  read -r -p "Type RESET to continue: " confirmation
  if [[ "$confirmation" != "RESET" ]]; then
    echo "Reset cancelled."
    exit 0
  fi
fi

echo "Stopping Docker Compose services if they are running..."
docker compose down --remove-orphans >/dev/null 2>&1 || true
docker compose -f compose.remote.yaml down --remove-orphans >/dev/null 2>&1 || true

echo "Removing Prompt Viewer runtime data..."
rm -rf \
  photos \
  .prompt_viewer_thumbs \
  prompt_viewer.sqlite3 \
  prompt_viewer.sqlite3-shm \
  prompt_viewer.sqlite3-wal

if ! rm -rf docker-data 2>/dev/null; then
  echo "docker-data contains files owned by another user; retrying with sudo..."
  sudo rm -rf docker-data
fi

mkdir -p \
  photos/comfyui \
  photos/chatgpt \
  .prompt_viewer_thumbs \
  docker-data

echo "Data reset complete."

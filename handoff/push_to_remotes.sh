#!/usr/bin/env bash
# Run on WSL after gh auth login. Pushes existing bundle histories to
# Origin and GitHub. Does not rewrite commits.
set -euo pipefail

GH_USER="${GH_USER:-Ashishkosana}"
ORIGIN_NS="${ORIGIN_NS:-ashishkosanagmailcom}"
DEST="${DEST:-$HOME/repos}"
BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

need git
need gh

if ! gh auth status >/dev/null 2>&1; then
  echo "gh is not logged in. Run: gh auth login" >&2
  exit 1
fi

mkdir -p "$DEST"

if ! gh repo view "$GH_USER/platform-forge" >/dev/null 2>&1; then
  echo "creating GitHub repo $GH_USER/platform-forge (private, wiki/issues off)"
  gh repo create "$GH_USER/platform-forge" --private --disable-wiki --disable-issues
else
  echo "GitHub $GH_USER/platform-forge already exists"
fi

push_one() {
  local name="$1"
  local bundle="$BUNDLE_DIR/${name}.bundle"
  local dir="$DEST/$name"

  if [[ ! -f "$bundle" ]]; then
    echo "missing bundle: $bundle" >&2
    echo "Put the three .bundle files next to this script." >&2
    exit 1
  fi

  echo
  echo "=== $name ==="
  git bundle verify "$bundle"

  if [[ -e "$dir" ]]; then
    echo "refusing to overwrite existing directory: $dir" >&2
    echo "Move it aside or set DEST to a new path: DEST=/tmp/repos $0" >&2
    exit 1
  fi

  git clone "$bundle" "$dir"
  git -C "$dir" remote remove origin
  git -C "$dir" remote add origin "https://origin.cursor.com/${ORIGIN_NS}/${name}.git"
  git -C "$dir" remote add github "https://github.com/${GH_USER}/${name}.git"

  echo "pushing github main"
  git -C "$dir" push -u github main

  echo "pushing origin (Cursor Origin) main"
  if git -C "$dir" push -u origin main; then
    echo "origin push ok"
  else
    echo "GitHub has the history. Origin push failed — check 'origin auth status' and retry:" >&2
    echo "  git -C $dir push -u origin main" >&2
  fi
}

push_one platform-forge
push_one ai-reliability-control-plane
push_one realtime-event-platform

echo
echo "done. remotes:"
for name in platform-forge ai-reliability-control-plane realtime-event-platform; do
  echo "--- $name ---"
  git -C "$DEST/$name" remote -v
  git -C "$DEST/$name" log -1 --oneline
done

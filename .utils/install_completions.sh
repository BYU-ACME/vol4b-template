#!/usr/bin/env bash
# Student repo — installs lint, check, and acme from .utils/
set -e

UTILS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$UTILS_DIR/.." && pwd)"

BASHRC="$HOME/.bashrc"
BASHPROFILE="$HOME/.bash_profile"
LINT_SRC="$UTILS_DIR/lint"
LINT_DEST="$HOME/.local/bin/lint"
CHECK_DEST="$HOME/.local/bin/check"
ACME_DEST="$HOME/.local/bin/acme"

START="# >>> acme_shell_tools >>>"
END="# <<< acme_shell_tools <<<"
OLD_START="# >>> grading_completions >>>"
OLD_END="# <<< grading_completions <<<"
LEGACY_START="# >>> simplify_completions >>>"
LEGACY_END="# <<< simplify_completions <<<"

remove_block() {
    local path="$1" start="$2" end="$3"
    if grep -qF "$start" "$path"; then
        python3 - <<'PY' "$path" "$start" "$end"
import sys
path, start, end = sys.argv[1:4]
text = open(path).read()
while start in text:
    a, b = text.index(start), text.index(end, text.index(start)) + len(end)
    text = text[:a] + text[b:]
open(path, "w").write(text.lstrip("\n"))
PY
    fi
}

for required in "$LINT_SRC" "$UTILS_DIR/check.py" "$UTILS_DIR/acme"; do
    if [[ ! -f "$required" ]]; then
        echo "error: $required not found" >&2
        exit 1
    fi
done

mkdir -p "$(dirname "$LINT_DEST")"
install -m 755 "$LINT_SRC" "$LINT_DEST"

cat > "$CHECK_DEST" <<EOF
#!/usr/bin/env bash
export ACME_REPO_ROOT="$REPO_ROOT"
export ACME_UTILS_DIR="$UTILS_DIR"
exec python3 "$UTILS_DIR/check.py" "\$@"
EOF
chmod +x "$CHECK_DEST"

cat > "$ACME_DEST" <<EOF
#!/usr/bin/env bash
export ACME_REPO_ROOT="$REPO_ROOT"
export ACME_UTILS_DIR="$UTILS_DIR"
exec bash "$UTILS_DIR/acme" "\$@"
EOF
chmod +x "$ACME_DEST"

BLOCK="
$START
export PATH=\"\$HOME/.local/bin:\$PATH\"
$END
"

touch "$BASHPROFILE"
if ! grep -qF "source ~/.bashrc" "$BASHPROFILE" && ! grep -qF ".bashrc" "$BASHPROFILE"; then
  cat >> "$BASHPROFILE" <<'EOF'

# Source .bashrc for interactive shells
if [ -f "$HOME/.bashrc" ]; then
  . "$HOME/.bashrc"
fi
EOF
fi

touch "$BASHRC"
remove_block "$BASHRC" "$LEGACY_START" "$LEGACY_END"
remove_block "$BASHRC" "$OLD_START" "$OLD_END"

if grep -qF "$START" "$BASHRC"; then
  python3 - <<'PY' "$BASHRC" "$START" "$END" "$BLOCK"
import sys
path, start, end, block = sys.argv[1:5]
text = open(path).read()
a, b = text.index(start), text.index(end, text.index(start)) + len(end)
open(path, "w").write(text[:a] + block.strip() + "\n" + text[b:])
PY
else
  cat >> "$BASHRC" <<EOF

$BLOCK
EOF
fi

echo "ACME shell tools installed."
echo "  lint  -> $LINT_DEST"
echo "  check -> $CHECK_DEST"
echo "  acme  -> $ACME_DEST  (lint | check | download_data)"
echo "Open a new terminal, then try:"
echo "  acme check"
echo "  acme lint path/to/file.py"
echo "  acme download_data"

if [[ -t 0 ]]; then
  exec bash
fi

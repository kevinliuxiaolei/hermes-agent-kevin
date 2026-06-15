#!/bin/bash
# Install git hooks for routing documentation auto-sync.

set -e

# Get repo root directory
REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOK_FILE="${REPO_ROOT}/.git/hooks/pre-commit"

echo "Installing git pre-commit hook..."

cat << 'EOF' > "$HOOK_FILE"
#!/bin/bash
# Pre-commit hook to auto-sync routing documentation.

# Detect if python3 is available
if ! command -v python3 &> /dev/null; then
    echo "Warning: python3 not found. Skipping routing documentation sync."
    exit 0
fi

# Run the sync script in git-hook mode
python3 tools/sync_routing_docs.py --git-hook
EOF

chmod +x "$HOOK_FILE"
echo "Pre-commit hook successfully installed at $HOOK_FILE"

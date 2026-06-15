#!/bin/bash
# Automated git backup and sync-fork script for Hermes on VPS.

set -e

# Always run from the repository root
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Ensure remote 'kevin' uses the SSH URL for passwordless authentication
git remote set-url kevin git@github.com:kevinliuxiaolei/hermes-agent-kevin.git

CURRENT_BRANCH="$(git branch --show-current)"

echo "=== Starting Backup Process: $(date) ==="
echo "Current branch: $CURRENT_BRANCH"

# Check if there are modified or untracked changes
if [ -n "$(git status --porcelain)" ]; then
    echo "Local modifications detected. Staging and committing..."
    # Stage all changes except the ignored/untracked files that shouldn't be backed up
    git add -A
    git commit -m "Auto-backup: $(date +'%Y-%m-%d %H:%M:%S')"
else
    echo "No local modifications to commit."
fi

# Try to sync fork (merge upstream main)
echo "Fetching from upstream (origin)..."
git fetch origin

echo "Attempting to merge origin/main into $CURRENT_BRANCH..."
if git merge origin/main --no-edit; then
    echo "Successfully merged origin/main."
else
    echo "Warning: Merge conflict detected during sync fork. Aborting merge so local code remains runnable."
    git merge --abort
fi

# Push current branch to personal backup remote
echo "Pushing changes to remote 'kevin'..."
if git push kevin "$CURRENT_BRANCH"; then
    echo "Successfully backed up and pushed to GitHub!"
else
    echo "Error: Failed to push to GitHub. Please check your SSH key settings."
    exit 1
fi

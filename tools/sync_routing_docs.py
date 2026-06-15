#!/usr/bin/env python3
"""Automated documentation sync tool for model routing strategies."""
# @routing-fix: [Sync Docs] Add automated synchronization script for routing strategies
import sys
import re
import subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
STRATEGY_FILE = ROOT / "MODEL_ROUTING_STRATEGY.md"
PROGRESS_FILE = ROOT / "MODEL_ROUTING_IMPLEMENTATION_PROGRESS.md"

VERSION_FILE = ROOT / "agent" / "routing_version.py"

def get_modified_files(staged: bool = True) -> list[str]:
    """Get modified files (staged or unstaged)."""
    cmd = ["git", "diff", "--cached", "--name-only"] if staged else ["git", "diff", "--name-only"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]

def extract_tags(staged_files: list[str], unstaged_files: list[str]) -> list[str]:
    """Scan modified files for @routing-fix annotations."""
    fixes = []
    pattern = re.compile(r"(?:#|//)\s*@routing-fix:\s*(.*)")
    valid_suffixes = {".py", ".js", ".ts", ".tsx", ".jsx", ".vue", ".yaml", ".yml", ".sh"}
    
    # Process staged files
    for f in staged_files:
        path = ROOT / f
        if not path.exists() or path.suffix not in valid_suffixes:
            continue
        diff = subprocess.run(["git", "diff", "--cached", "-U0", str(path)], capture_output=True, text=True)
        for line in diff.stdout.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                match = pattern.search(line)
                if match:
                    fixes.append(match.group(1).strip())
                    
    # Process unstaged files
    for f in unstaged_files:
        path = ROOT / f
        if not path.exists() or path.suffix not in valid_suffixes:
            continue
        diff = subprocess.run(["git", "diff", "-U0", str(path)], capture_output=True, text=True)
        for line in diff.stdout.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                match = pattern.search(line)
                if match:
                    fixes.append(match.group(1).strip())
                    
    return list(set(fixes))

def get_routing_version() -> str:
    """Read routing version from the single source of truth python file."""
    content = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'ROUTING_VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"', content)
    if not match:
        raise ValueError("Could not find ROUTING_VERSION in agent/routing_version.py")
    return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"

def update_routing_version(new_version: str):
    """Write the new routing version to the single source of truth python file."""
    content = VERSION_FILE.read_text(encoding="utf-8")
    new_content = re.sub(
        r'ROUTING_VERSION\s*=\s*"\d+\.\d+\.\d+"',
        f'ROUTING_VERSION = "{new_version}"',
        content
    )
    VERSION_FILE.write_text(new_content, encoding="utf-8")

def update_documents(fixes: list[str]) -> bool:
    if not fixes:
        return False
        
    # 1. Get current version and bump it
    current_ver = get_routing_version()
    major, minor, patch = map(int, current_ver.split("."))
    new_ver = f"{major}.{minor}.{patch + 1}"
    
    # 2. Write new version to single source of truth python file
    update_routing_version(new_ver)
    
    # 3. Update Strategy File Version
    strategy_content = STRATEGY_FILE.read_text(encoding="utf-8")
    strategy_content = re.sub(r"Version:\s*\d+\.\d+\.\d+", f"Version: {new_ver}", strategy_content, count=1)
    STRATEGY_FILE.write_text(strategy_content, encoding="utf-8")
    
    # 4. Update Progress File Log
    progress_content = PROGRESS_FILE.read_text(encoding="utf-8")
    progress_content = re.sub(r"Design version:\s*v\d+\.\d+\.\d+", f"Design version: v{new_ver}", progress_content, count=1)
    
    # Format log entry
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_entry = f"\n### Deployment and Post-Review Fixes ({date_str} v{new_ver})\n\n"
    for fix in fixes:
        log_entry += f"- **Fix**: {fix}\n"
    
    # Append log entry to progress file before "## Remaining Work"
    if "## Remaining Work" in progress_content:
        progress_content = progress_content.replace("## Remaining Work", f"{log_entry}\n## Remaining Work")
    else:
        # Append before "## Verification"
        if "## Verification" in progress_content:
            progress_content = progress_content.replace("## Verification", f"{log_entry}\n## Verification")
        else:
            progress_content += f"\n{log_entry}"
        
    PROGRESS_FILE.write_text(progress_content, encoding="utf-8")
    return True

def main():
    is_git_hook = "--git-hook" in sys.argv
    
    if is_git_hook:
        staged_files = get_modified_files(staged=True)
        unstaged_files = []
        print("Running in git hook mode. Scanning staged changes only...")
    else:
        staged_files = get_modified_files(staged=True)
        unstaged_files = get_modified_files(staged=False)
        print("Running in manual mode. Scanning both staged and unstaged changes...")
        
    fixes = extract_tags(staged_files, unstaged_files)
    
    if fixes:
        print(f"Detected routing fixes: {fixes}")
        if update_documents(fixes):
            print("Successfully updated strategy and progress documentation.")
            # If run in git hook mode, stage the updated files
            if is_git_hook:
                subprocess.run(["git", "add", str(STRATEGY_FILE), str(PROGRESS_FILE), str(VERSION_FILE)])
                print("Staging updated documentation and version files.")
    else:
        print("No routing fixes detected.")

if __name__ == "__main__":
    main()

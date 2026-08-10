#!/bin/bash

# Count core agent lines of code (Python only, excluding tests and __pycache__)

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo $REPO_ROOT

count_lines() {
    find "$REPO_ROOT/$1" -name '*.py' -not -path '*/__pycache__/*' -exec cat {} + | wc -l
}

backend=$(count_lines "heyclaw/app")
satellite=$(count_lines "satellite/app")
shared=$(count_lines "shared/heyclaw_shared")

echo "Core agent lines (heyclaw/app/**/*.py):        $backend"
echo "Satellite lines (satellite/app/**/*.py):       $satellite"
echo "Shared lines (shared/heyclaw_shared/**/*.py):  $shared"
echo "Total:                                         $((backend + satellite + shared))"

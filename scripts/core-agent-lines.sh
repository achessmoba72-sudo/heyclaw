#!/bin/bash

# Count core agent lines of code (Python only, excluding tests and __pycache__)

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo $REPO_ROOT

count=$(find "$REPO_ROOT/heyclaw/app" -name '*.py' -not -path '*/__pycache__/*' | xargs wc -l | tail -1 | awk '{print $1}')

echo "Core agent lines (heyclaw/app/**/*.py): $count"

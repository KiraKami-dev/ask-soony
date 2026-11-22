#!/bin/sh
# Session-start check: tell the agent if this clone is behind origin, and show who is doing what.
# Never fails the session; prints nothing if git or the network is unavailable.
cd "$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || exit 0
timeout 10 git fetch -q origin 2>/dev/null || git fetch -q origin 2>/dev/null || exit 0
behind=$(git rev-list --count "HEAD..origin/$branch" 2>/dev/null || echo 0)
if [ "$behind" -gt 0 ]; then
  echo "SYNC CHECK: this clone is $behind commit(s) behind origin/$branch. Before any other work, run 'git pull --rebase --autostash', then re-read PROGRESS.md and docs/plan.md, and tell the user what changed:"
  git log --oneline "HEAD..origin/$branch" | head -10
else
  echo "SYNC CHECK: up to date with origin/$branch."
fi
echo "Team status (PROGRESS.md, Now):"
git show "origin/$branch:PROGRESS.md" 2>/dev/null | sed -n '/^## Now/,/^## /p' | grep '^- '
exit 0

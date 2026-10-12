#!/bin/sh
# Commit the page, scanner and latest summary, and push to GitHub Pages.
# History, state and the token cache stay on this Mac (see .gitignore).
set -e
export PATH="/opt/homebrew/bin:/usr/bin:/bin:$PATH"
cd "$(dirname "$0")"
git add -A
if git diff --cached --quiet; then
  echo "Publish: nothing changed."
  exit 0
fi
git commit -q -m "Update $(date '+%Y-%m-%d %H:%M')"
git push -q origin main
echo "Publish: pushed to GitHub Pages."

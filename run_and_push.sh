#!/bin/bash
# Blogger Auto-Poster with GitHub Auto-Commit
# Runs every hour via cron

cd /home/userland/blogger-auto-poster

# Activate venv and run auto-poster
source venv/bin/activate
python3 auto_poster.py >> auto_poster.log 2>&1

# Git auto-commit and push
git add -A
if git diff --cached --quiet; then
    echo "$(git log --oneline -1)"
else
    git commit -m "auto: Hourly update - $(date '+%Y-%m-%d %H:%M UTC')"
    git push origin master 2>&1
fi

#!/bin/sh
# Bong-watcher 容器入口：确保 /data/Bong 克隆存在后启动 watcher。
set -eu

REPO_URL="${BONG_REPO_URL:-https://github.com/Kizunad/Bong.git}"
: "${BONG_REPO:=/data/Bong}"

if [ ! -d "$BONG_REPO/.git" ]; then
    echo "[entrypoint] cloning $REPO_URL -> $BONG_REPO (blob:none 部分克隆)"
    # blob 按需懒取：watcher 只要 commit/tree + 少数 blob（module-map html），克隆量最小
    git clone --filter=blob:none "$REPO_URL" "$BONG_REPO"
fi

# volume 属主可能与容器用户不一致，放开 safe.directory
git config --global --add safe.directory '*'

exec python3 /app/watcher.py

#!/bin/sh
# Bong-watcher 容器入口：尽力保证 /data/Bong 克隆存在，然后无条件启动 watcher。
#
# 克隆失败不阻塞 HTTP 服务：watcher 的 refresher 会在 /api/state.json 报
# 采集错误（error 字段），页面显示错误横幅——绝不进入容器崩溃重启循环。
set -u

REPO_URL="${BONG_REPO_URL:-https://github.com/Kizunad/Bong.git}"
: "${BONG_REPO:=/data/Bong}"

if [ ! -d "$BONG_REPO/.git" ]; then
    # 有界重试（3 次退避），全失败也继续启动
    for delay in 5 15 30; do
        echo "[entrypoint] cloning $REPO_URL -> $BONG_REPO (blob:none 部分克隆)"
        if git clone --filter=blob:none "$REPO_URL" "$BONG_REPO"; then
            break
        fi
        rm -rf "$BONG_REPO"
        echo "[entrypoint] clone 失败，${delay}s 后重试"
        sleep "$delay"
    done
    if [ ! -d "$BONG_REPO/.git" ]; then
        echo "[entrypoint] WARN: 克隆最终失败——watcher 仍将启动，dashboard 会显示采集错误；修复网络/URL 后重启容器即可"
    fi
fi

# volume 属主可能与容器用户不一致，放开 safe.directory
git config --global --add safe.directory '*' || true

exec python3 /app/watcher.py

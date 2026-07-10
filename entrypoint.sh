#!/bin/sh
# Bong-watcher 容器入口：HTTP 服务立即启动，仓库初始化放后台。
#
# 设计约束（review 契约）：
# - clone 挂起/失败都不得阻塞或杀死 HTTP 服务（watcher 用 error 快照响应）
# - 绝不 rm -rf 用户可配置的 BONG_REPO：无效仓库隔离改名，克隆走脚本
#   自建的临时目录、成功后原子 mv 切换（避免读到半成品仓库）
# - .git 目录存在 ≠ 仓库有效：用 rev-parse + origin 配置校验，损坏可自愈
set -u

REPO_URL="${BONG_REPO_URL:-https://github.com/Kizunad/Bong.git}"
: "${BONG_REPO:=/data/Bong}"
CLONE_TIMEOUT="${CLONE_TIMEOUT:-600}"

repo_valid() {
    git -C "$BONG_REPO" rev-parse --verify HEAD > /dev/null 2>&1 &&
        git -C "$BONG_REPO" config remote.origin.url > /dev/null 2>&1
}

init_repo() {
    if repo_valid; then
        echo "[entrypoint] 仓库有效: $BONG_REPO"
        return 0
    fi
    if [ -e "$BONG_REPO" ]; then
        quarantine="${BONG_REPO}.broken.$(date +%s)"
        echo "[entrypoint] 仓库无效/损坏，隔离 -> $quarantine（不删除任何既有数据）"
        mv "$BONG_REPO" "$quarantine" || {
            echo "[entrypoint] WARN: 隔离失败，跳过克隆（HTTP 服务照常，dashboard 报采集错误）"
            return 1
        }
    fi
    parent="$(dirname "$BONG_REPO")"
    for delay in 5 15 30; do
        tmp="$parent/.clone-tmp.$$"
        rm -rf "$tmp"   # 只清理本进程自建的临时目录
        echo "[entrypoint] cloning $REPO_URL -> $tmp (blob:none, timeout ${CLONE_TIMEOUT}s)"
        if timeout "$CLONE_TIMEOUT" git clone --filter=blob:none "$REPO_URL" "$tmp"; then
            mv "$tmp" "$BONG_REPO"
            echo "[entrypoint] 克隆完成，已原子切换到 $BONG_REPO"
            return 0
        fi
        rm -rf "$tmp"
        echo "[entrypoint] clone 失败，${delay}s 后重试"
        sleep "$delay"
    done
    echo "[entrypoint] WARN: 克隆最终失败——HTTP 服务照常运行，dashboard 显示采集错误；修复网络/URL 后重启容器"
    return 1
}

git config --global --add safe.directory '*' || true

# 仓库初始化放后台：clone 慢/挂起/失败都不影响 HTTP 立即可用
init_repo &

exec python3 /app/watcher.py

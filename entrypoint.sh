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
CLONE_RETRY_DELAYS="${CLONE_RETRY_DELAYS:-5 15 30}"

normalize_url() {
    # 先去全部尾斜杠、再去尾部 .git、再去残余尾斜杠——
    # Bong / Bong.git / Bong/ / Bong.git/ / Bong.git// 全部归一等价
    printf '%s' "$1" | sed -e 's:/*$::' -e 's/\.git$//' -e 's:/*$::'
}

repo_valid() {
    git -C "$BONG_REPO" rev-parse --verify HEAD > /dev/null 2>&1 || return 1
    actual="$(git -C "$BONG_REPO" config remote.origin.url 2>/dev/null)" || return 1
    if [ "$(normalize_url "$actual")" != "$(normalize_url "$REPO_URL")" ]; then
        echo "[entrypoint] 持久卷仓库 origin ($actual) 与 BONG_REPO_URL ($REPO_URL) 不一致，按无效处理"
        return 1
    fi
    return 0
}

# 同步阶段：校验 + 隔离（快操作）。返回 0=仓库已有效 1=槽位已清空需克隆 2=隔离失败
prepare_repo_slot() {
    if repo_valid; then
        echo "[entrypoint] 仓库有效: $BONG_REPO"
        return 0
    fi
    if [ -e "$BONG_REPO" ]; then
        quarantine="${BONG_REPO}.broken.$(date +%s)"
        echo "[entrypoint] 仓库无效/损坏，隔离 -> $quarantine（不删除任何既有数据）"
        mv "$BONG_REPO" "$quarantine" || {
            echo "[entrypoint] WARN: 隔离失败，跳过克隆（HTTP 服务照常，dashboard 报采集错误）"
            return 2
        }
    fi
    return 1
}

# 异步阶段：仅克隆（慢操作）
clone_repo() {
    parent="$(dirname "$BONG_REPO")"
    mkdir -p "$parent" || {
        echo "[entrypoint] WARN: 无法创建父目录 $parent，跳过克隆（HTTP 服务照常）"
        return 1
    }
    for delay in $CLONE_RETRY_DELAYS; do
        tmp="$parent/.clone-tmp.$$"
        rm -rf "$tmp"   # 只清理本进程自建的临时目录
        echo "[entrypoint] cloning $REPO_URL -> $tmp (blob:none, timeout ${CLONE_TIMEOUT}s)"
        if timeout "$CLONE_TIMEOUT" git clone --filter=blob:none "$REPO_URL" "$tmp"; then
            if mv "$tmp" "$BONG_REPO"; then
                echo "[entrypoint] 克隆完成，已原子切换到 $BONG_REPO"
                return 0
            fi
            echo "[entrypoint] WARN: 临时目录切换失败（mv $tmp -> $BONG_REPO）"
        fi
        rm -rf "$tmp"
        echo "[entrypoint] clone/切换失败，${delay}s 后重试"
        sleep "$delay"
    done
    echo "[entrypoint] WARN: 克隆最终失败——HTTP 服务照常运行，dashboard 显示采集错误；修复网络/URL 后重启容器"
    return 1
}

# watcher 实际使用的仓库路径：隔离失败(rc=2)时改指确定不存在的安全路径，
# 绝不让 watcher 读到无效/错误仓库
watcher_repo_path() { # $1 = prepare_repo_slot 返回码
    if [ "$1" = 2 ]; then
        printf '%s' "${BONG_REPO}.unavailable"
    else
        printf '%s' "$BONG_REPO"
    fi
}

# 组合语义（测试用同一入口）：同步 prepare + 克隆
init_repo() {
    prepare_repo_slot
    case $? in
        0) return 0 ;;
        2) return 1 ;;
    esac
    clone_repo
}

# ENTRYPOINT_LIB_ONLY=1 时仅暴露函数（供 test_entrypoint.sh 无网络测试）
if [ -z "${ENTRYPOINT_LIB_ONLY:-}" ]; then
    git config --global --add safe.directory '*' || true
    # 校验/隔离同步完成（快）——watcher 采集绝不可能读到错误/损坏仓库
    prepare_repo_slot
    rc=$?
    BONG_REPO="$(watcher_repo_path "$rc")"
    export BONG_REPO
    if [ "$rc" = 2 ]; then
        echo "[entrypoint] WARN: 隔离失败——watcher 改指安全空路径 $BONG_REPO，不采集无效仓库"
    fi

    # 轻量 supervisor：同时持有 watcher 与 clone worker 的 PID，
    # TERM/INT 转发给两者并 wait 回收——后台克隆不再是无人管理的孤儿
    python3 /app/watcher.py &
    WATCHER_PID=$!
    CLONE_PID=""
    if [ "$rc" = 1 ]; then
        clone_repo &
        CLONE_PID=$!
    fi
    on_term() {
        kill -TERM "$WATCHER_PID" ${CLONE_PID:+"$CLONE_PID"} 2>/dev/null
    }
    trap on_term TERM INT
    wait "$WATCHER_PID"
    code=$?
    # watcher 退出（或收到停止信号）后：终止并回收 clone worker
    [ -n "$CLONE_PID" ] && kill -TERM "$CLONE_PID" 2>/dev/null
    wait 2>/dev/null || true
    exit "$code"
fi

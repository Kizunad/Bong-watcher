#!/bin/sh
# entrypoint.sh 仓库状态机契约测试（无网络，本地 file:// 源）：sh test_entrypoint.sh
set -u
cd "$(dirname "$0")"
FAILS=0
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

check() { # $1=描述 $2=上一命令退出码期望0
    if [ "$2" -eq 0 ]; then echo "ok   $1"; else echo "FAIL $1"; FAILS=$((FAILS + 1)); fi
}

no_tmp_residue() { # 隐藏目录也要能查到——裸 ls 看不见 dotfile
    [ -z "$(find "$CASE_DIR" -maxdepth 1 -name '.clone-tmp.*' -print -quit)" ]
}

# 本地源仓库
SRC="$T/src"
git init -q "$SRC"
git -C "$SRC" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init

ENTRYPOINT_LIB_ONLY=1
export ENTRYPOINT_LIB_ONLY
. ./entrypoint.sh

new_env() {
    CASE_DIR="$T/case_$1"
    mkdir -p "$CASE_DIR"
    BONG_REPO="$CASE_DIR/Bong"
    REPO_URL="file://$SRC"
    CLONE_TIMEOUT=30
    CLONE_RETRY_DELAYS="0 0"
}

# ── normalize_url 等价表 ──────────────────────────────
base="$(normalize_url 'https://x/Bong')"
for v in 'https://x/Bong.git' 'https://x/Bong/' 'https://x/Bong.git/' 'https://x/Bong.git//'; do
    [ "$(normalize_url "$v")" = "$base" ]
    check "normalize 等价: $v" $?
done
[ "$(normalize_url 'https://x/Other')" != "$base" ]
check "normalize 不同仓库不误判等价" $?

# ── 首次克隆 + 有效卷复用 ─────────────────────────────
new_env fresh
init_repo > /dev/null 2>&1
check "首次克隆成功" $?
repo_valid
check "克隆后 repo_valid" $?
init_repo > /dev/null 2>&1
check "有效卷复用（第二次 init 直接通过）" $?
no_tmp_residue
check "无 clone-tmp 残留" $?

# ── 等价 origin 变体复用（不误隔离） ──────────────────
git -C "$BONG_REPO" remote set-url origin "file://$SRC/"
init_repo > /dev/null 2>&1
check "尾斜杠等价 origin 复用" $?
[ -z "$(ls "$CASE_DIR" | grep broken)" ]
check "等价 origin 未被误隔离" $?

# ── origin 不一致 → 隔离 + 重克隆 ─────────────────────
git -C "$BONG_REPO" remote set-url origin "file://$T/other"
init_repo > /dev/null 2>&1
check "origin 不一致时自愈成功" $?
[ -n "$(ls "$CASE_DIR" | grep broken)" ]
check "不一致仓库被隔离保留（未删除）" $?
[ "$(git -C "$BONG_REPO" config remote.origin.url)" = "file://$SRC" ]
check "重克隆后 origin 正确" $?

# ── 损坏仓库（.git 是垃圾）→ 隔离 + 重克隆 ────────────
new_env corrupt
mkdir -p "$BONG_REPO/.git"
echo garbage > "$BONG_REPO/.git/HEAD"
init_repo > /dev/null 2>&1
check "损坏仓库自愈成功" $?
[ -n "$(ls "$CASE_DIR" | grep broken)" ]
check "损坏仓库被隔离保留" $?

# ── 克隆失败：返回非零 + 无残留 + 目标不存在 ──────────
new_env clonefail
REPO_URL="file://$T/nonexistent"
init_repo > /dev/null 2>&1
[ $? -ne 0 ]
check "克隆失败返回非零" $?
[ ! -e "$BONG_REPO" ]
check "失败后目标目录不存在（无半成品）" $?
no_tmp_residue
check "失败后无 clone-tmp 残留" $?

# ── 深层父目录：mkdir -p 兜底 ─────────────────────────
new_env deep
BONG_REPO="$CASE_DIR/a/b/c/Bong"
init_repo > /dev/null 2>&1
check "深层父目录自动创建并克隆成功" $?

# ── mv 切换失败：返回非零 + 清理临时目录 ──────────────
new_env mvfail
mv() { return 1; }
init_repo > /dev/null 2>&1
rc=$?
unset -f mv
[ $rc -ne 0 ]
check "mv 失败返回非零（不假宣告成功）" $?
no_tmp_residue
check "mv 失败后临时目录被清理" $?

# ── prepare_repo_slot 同步隔离：错误 origin 在启动前就被移走 ──
new_env race
init_repo > /dev/null 2>&1
git -C "$BONG_REPO" remote set-url origin "file://$T/wrong"
prepare_repo_slot > /dev/null 2>&1
rc=$?
[ "$rc" = 1 ]
check "prepare_repo_slot 对错误 origin 返回需克隆" $?
[ ! -e "$BONG_REPO" ]
check "prepare 后目标已同步移走（采集不可能读到错误仓库）" $?
clone_repo > /dev/null 2>&1
check "prepare 后 clone_repo 补齐" $?
repo_valid
check "补齐后 repo_valid" $?

# ── 隔离失败：watcher 必须改指安全空路径 ──────────────
new_env quarfail
init_repo > /dev/null 2>&1
git -C "$BONG_REPO" remote set-url origin "file://$T/wrong2"
mv() { return 1; }
prepare_repo_slot > /dev/null 2>&1
rc=$?
unset -f mv
[ "$rc" = 2 ]
check "隔离失败返回 rc=2" $?
wp="$(watcher_repo_path "$rc")"
[ "$wp" != "$BONG_REPO" ] && [ ! -e "$wp" ]
check "rc=2 时 watcher 路径改指不存在的安全路径" $?
[ "$(watcher_repo_path 0)" = "$BONG_REPO" ] && [ "$(watcher_repo_path 1)" = "$BONG_REPO" ]
check "rc=0/1 时 watcher 路径不变" $?

# ── 残留检查自检：主动制造隐藏残留，断言必须能发现 ────
new_env selfcheck
mkdir -p "$CASE_DIR/.clone-tmp.9999"
if no_tmp_residue; then
    echo "FAIL 残留自检：断言未发现主动制造的 .clone-tmp 残留"
    FAILS=$((FAILS + 1))
else
    echo "ok   残留自检：断言能发现隐藏残留目录"
fi
rm -rf "$CASE_DIR/.clone-tmp.9999"

echo "----"
if [ "$FAILS" -eq 0 ]; then
    echo "entrypoint 契约测试全部通过"
else
    echo "entrypoint 契约测试失败 $FAILS 项"
    exit 1
fi

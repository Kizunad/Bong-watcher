#!/usr/bin/env python3
"""Bong-watcher — 本地 WebUI：盯 Kizunad/Bong 的 GitHub 合并 + module-map 融合视图。

零埋点：所有状态取自 GitHub 原语（PR / checks / squash 合并 commit / Model trailer）
和 origin/main 上的 module-map DATA。stdlib only，无第三方依赖。

用法:
    python3 watcher.py                 # http://0.0.0.0:8901
环境变量:
    BONG_REPO    Bong 仓库路径（默认 ~/Code/Bong）
    PORT         监听端口（默认 8901）
    REFRESH_SEC  刷新周期秒（默认 300）
"""

import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BONG = Path(os.environ.get("BONG_REPO", str(Path.home() / "Code" / "Bong")))


def pos_int_env(name, default):
    """环境变量解析为正整数；非法（非数字/零/负）回退默认并告警——
    绝不让坏配置传进 time.sleep 无声杀死刷新线程。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = 0
    if v <= 0:
        print(f"[watcher] WARN: {name}={raw!r} 非法，回退默认 {default}")
        return default
    return v


PORT = pos_int_env("PORT", 8901)
REFRESH_SEC = pos_int_env("REFRESH_SEC", 300)
BACKOFF_SEC = pos_int_env("BACKOFF_SEC", 15)
HERE = Path(__file__).resolve().parent

_LOCK = threading.Lock()
_STATE: dict = {"generated_at": None, "error": "尚未完成首次刷新"}
_MM_HTML: bytes = "<p>module-map 尚未加载</p>".encode("utf-8")


# ---------------------------------------------------------------- pure helpers

PR_NUM_RE = re.compile(r"\(#(\d+)\)\s*$")


def parse_pr_number(subject: str):
    """squash 合并 commit 标题末尾的 (#1234) → 1234；没有则 None。"""
    m = PR_NUM_RE.search(subject or "")
    return int(m.group(1)) if m else None


def scrape_modules(html: str):
    """从 module-map DATA 块轻量刮出 [{id, path, title, layer}]。

    只依赖 id/path/title/layer 四个字段的字面量形态，不解析整个 JS。
    """
    block = html
    start = html.find("=== DATA:START ===")
    end = html.find("=== DATA:END ===")
    if start != -1 and end != -1:
        block = html[start:end]
    mods = []
    q = '"?'  # 键名兼容 JSON（"id":）与 JS（id:）两种写法
    entry_re = re.compile(
        rf'{q}id{q}\s*:\s*"([^"]+)"[^{{}}]*?{q}layer{q}\s*:\s*"([^"]+)"[^{{}}]*?'
        rf'{q}name{q}\s*:\s*"[^"]*"[^{{}}]*?{q}path{q}\s*:\s*"([^"]+)"[^{{}}]*?{q}title{q}\s*:\s*"([^"]+)"',
        re.S,
    )
    for m in entry_re.finditer(block):
        mods.append({"id": m.group(1), "layer": m.group(2), "path": m.group(3), "title": m.group(4)})
    return mods


def infer_modules(files, modules):
    """文件路径列表 → 命中的 module id 列表（最长 path 前缀优先，去重保序）。

    没有任何 module 命中的文件归并为其顶层目录（如 "docs"），加 "dir:" 前缀区分。
    """
    hits, seen = [], set()
    for f in files or []:
        best = None
        for mod in modules:
            p = mod["path"]
            if f.startswith(p) and (best is None or len(p) > len(best["path"])):
                best = mod
        key = best["id"] if best else "dir:" + (f.split("/", 1)[0] if "/" in f else f)
        if key not in seen:
            seen.add(key)
            hits.append(key)
    return hits


def tally_lines(text: str, strip_email: bool = False):
    """逐行计数（空行忽略），返回按次数降序的 [{name, count}]。"""
    counts: dict = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if strip_email:
            line = re.sub(r"\s*<[^>]*>\s*$", "", line)
        counts[line] = counts.get(line, 0) + 1
    return [
        {"name": k, "count": v}
        for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def next_sleep(error, has_snapshot, refresh, backoff):
    """短退避仅用于「报错且从未有过有效快照」（容器首启等克隆落地）；
    已有有效快照后的偶发失败沿用正常周期，不高频重试打爆 git/gh。"""
    return max(1, backoff if (error and not has_snapshot) else refresh)


def parse_merged_log(raw: str):
    """`git log --format=%H%x09%s%x09%cs --name-only` 输出 → merged PR 列表。"""
    prs = []
    cur = None
    for line in raw.splitlines():
        if "\t" in line:
            sha, subject, date = line.split("\t", 2)
            num = parse_pr_number(subject)
            cur = None
            if num is not None:
                cur = {
                    "number": num,
                    "title": PR_NUM_RE.sub("", subject).strip(),
                    "date": date,
                    "sha": sha[:8],
                    "files": [],
                }
                prs.append(cur)
        elif line.strip() and cur is not None:
            cur["files"].append(line.strip())
    return prs


# ---------------------------------------------------------------- collectors

def run(cmd, cwd=None, timeout=90):
    r = subprocess.run(
        cmd, cwd=str(cwd or BONG), capture_output=True, text=True, timeout=timeout
    )
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}… → {r.returncode}: {r.stderr.strip()[:300]}")
    return r.stdout


def gh_json(args):
    return json.loads(run(["gh"] + args))


def collect_open_prs(modules, gh=gh_json):
    """采集 open PR + 门禁 + 触及模块。gh 可注入供测试。"""
    open_prs = gh(["pr", "list", "--state", "open", "--json",
                   "number,title,headRefName,createdAt,statusCheckRollup"])
    for pr in open_prs:
        checks = []
        for c in pr.pop("statusCheckRollup") or []:
            checks.append({
                "name": c.get("name") or c.get("context") or "?",
                "state": (c.get("conclusion") or c.get("state") or "PENDING").upper(),
            })
        pr["checks"] = checks
        # files 查询失败必须让整轮采集失败（由 safe_open_prs 沿用旧快照）——
        # 吞成空列表会把上一轮的模块归属静默清空
        files = gh(["pr", "view", str(pr["number"]), "--json", "files",
                    "--jq", "[.files[].path]"])
        pr["modules"] = infer_modules(files, modules)
    return open_prs


def safe_open_prs(prev, fetch):
    """open PR 采集失败只降级本栏：沿用上轮值 + 字段级错误，绝不中止整轮。"""
    try:
        return fetch(), None
    except Exception as e:
        return list(prev or []), f"{type(e).__name__}: {e}"


def collect():
    """一轮全量采集，返回 (state_dict, module_map_html_bytes)。"""
    run(["git", "fetch", "origin", "--quiet"], timeout=120)

    def count_tree(path):
        out = run(["git", "ls-tree", "-r", "--name-only", "origin/main", path])
        return sum(1 for line in out.splitlines() if line.endswith(".md"))

    mm_html = run(["git", "show", "origin/main:module-map/index.html"])
    modules = scrape_modules(mm_html)

    merged = parse_merged_log(
        run(["git", "log", "origin/main", "-n", "60",
             "--format=%H%x09%s%x09%cs", "--name-only"])
    )[:30]
    for pr in merged:
        pr["modules"] = infer_modules(pr.pop("files"), modules)

    with _LOCK:
        prev_open = list(_STATE.get("open_prs") or [])
    open_prs, open_prs_error = safe_open_prs(
        prev_open, lambda: collect_open_prs(modules)
    )

    model_trailers = tally_lines(
        run(["git", "log", "origin/main", "--format=%(trailers:key=Model,valueonly)"])
    )
    coauthors = tally_lines(
        run(["git", "log", "origin/main", "--format=%(trailers:key=Co-Authored-By,valueonly)"]),
        strip_email=True,
    )[:10]

    gaps = {
        "critical": len(re.findall(r'"?severity"?\s*:\s*"critical"', mm_html)),
        "warn": len(re.findall(r'"?severity"?\s*:\s*"warn"', mm_html)),
    }

    state = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "error": None,
        "repo": "Kizunad/Bong",
        "pipeline": {
            "skeleton": count_tree("docs/plans-skeleton"),
            "active": count_tree("docs") - count_tree("docs/plans-skeleton")
                      - count_tree("docs/finished_plans") - count_tree("docs/library"),
            "open": len(open_prs),
            "finished": count_tree("docs/finished_plans"),
        },
        "open_prs": open_prs,
        "open_prs_error": open_prs_error,
        "merged": merged,
        "model_trailers": model_trailers,
        "coauthors": coauthors,
        "module_index": {m["id"]: m for m in modules},
        "module_count": len(modules),
        "gaps": gaps,
    }
    return state, mm_html.encode("utf-8")


def refresher():
    global _STATE, _MM_HTML
    while True:
        try:
            state, mm = collect()
            with _LOCK:
                _STATE, _MM_HTML = state, mm
        except Exception as e:  # 刷新失败不杀服务，报到 UI
            with _LOCK:
                _STATE = dict(_STATE, error=f"{type(e).__name__}: {e}",
                              generated_at=_STATE.get("generated_at"))
        with _LOCK:
            err = _STATE.get("error")
            has_snap = _STATE.get("generated_at") is not None
        time.sleep(next_sleep(err, has_snap, REFRESH_SEC, BACKOFF_SEC))


# ---------------------------------------------------------------- http server

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8",
                       (HERE / "index.html").read_bytes())
        elif path == "/api/state.json":
            with _LOCK:
                body = json.dumps(_STATE, ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
        elif path in ("/module-map", "/module-map/", "/module-map/index.html"):
            with _LOCK:
                body = _MM_HTML
            self._send(200, "text/html; charset=utf-8", body)
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def log_message(self, fmt, *args):  # 安静
        pass


def main():
    threading.Thread(target=refresher, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Bong-watcher on http://0.0.0.0:{PORT}  (repo={BONG}, refresh={REFRESH_SEC}s)")
    srv.serve_forever()


if __name__ == "__main__":
    main()

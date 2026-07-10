# Bong-watcher

本地 WebUI：盯 Kizunad/Bong 的 GitHub 合并 + module-map 融合视图。零埋点——所有状态取自 GitHub 原语（open PR + checks、squash 合并 commit、`Model:` 署名 trailer）和 origin/main 上的 `module-map/` DATA 块。stdlib only，无第三方依赖。

## 运行

```bash
python3 watcher.py           # http://localhost:8901（绑 0.0.0.0，LAN 可访问）
python3 tests.py             # 纯函数单测
```

环境变量：`BONG_REPO`（默认 `~/Code/Bong`）、`PORT`（默认 8901）、`REFRESH_SEC`（默认 300）。

依赖 `gh` CLI 已登录（读 open PR 用，容器内用 `GH_TOKEN` env）；合并史 / 署名统计 / module-map 全走本地 git（每轮刷新先 `git fetch origin`，不碰工作区）。

## Docker + Watchtower 持续部署

```bash
GH_TOKEN=ghp_xxx docker compose up -d     # 拉 ghcr.io/kizunad/bong-watcher:latest
```

链路：**push/merge main → Actions `publish.yml`（单测冒烟 → 构建 → 推 GHCR `:latest` + `:sha`）→ 服务器 Watchtower 检测到新 `:latest` 自动拉取重启容器**。容器首启在 `/data` volume 里对 Bong 做 `--filter=blob:none` 部分克隆，之后每轮刷新只 fetch 增量。

- Watchtower 若跑在 `--label-enable` 白名单模式，compose 里已带 `com.centurylinklabs.watchtower.enable=true` 标签
- **首次发布后需把 GHCR package 设为 public**（GitHub → Packages → bong-watcher → Package settings → Change visibility），否则 Watchtower 匿名拉不到；不想公开就给 Watchtower 配 registry 凭据
- `GH_TOKEN` 只需 repo read 权限（fine-grained：Kizunad/Bong 的 Pull requests: read）

## 页面

- `/` —— 合并监视 dashboard：plan 管线计数、open PR（门禁芯片 + 触及模块芯片）、最近 30 个合并（按 module-map path 前缀映射到模块，芯片直达模块页）、模型署名统计（`Model:` trailer 精确口径 + Co-Authored-By 全史）
- `/module-map/` —— origin/main 最新版模块图谱 SPA 原样托管（每轮刷新用 `git show origin/main:module-map/index.html` 拉最新，不依赖本地 checkout 新旧）
- `/api/state.json` —— 采集结果（给页面或别的工具消费）

## 设计要点

- 采集失败不杀服务：报错横幅 + 沿用上次快照
- module 映射 = 最长 path 前缀优先；无命中归并为顶层目录芯片（`dir:docs` → `docs/`）
- 本地 Bong checkout 常年 stale 没关系——一切读 `origin/main` ref

# codie-agent-build

构建 codie 用的 **agent 容器镜像**,并多架构推送到 **ghcr.io**(后续镜像同步到阿里 ACR / 腾讯 TCR)。

## 这个仓库是干什么的

codie 的 agent 镜像 **不是** 上游项目本身,而是 **上游源码 + codie 的一层包装**:

```
上游 fork（毛坯）  +  本仓库（codie 包装：Dockerfile + shim/emitter/patch）  →  ghcr.io/<owner>/<image>
```

三个上游 fork **保持纯净**(只当构建上下文,随时 `git pull` 同步上游):

- `shanhaobo/openhuman`  → 镜像 `openhuman`
- `shanhaobo/openclaw`   → 镜像 `openclaw`
- `shanhaobo/hermes-agent` → 镜像 `hermes-agent`

codie 的包装(本仓库 `docker-registry/agent-dockerfiles/<agent>/`)注入 codie 专属集成:

| Agent | codie 加了什么 | 机制(不改 fork) |
|---|---|---|
| openhuman | 自带 endpoint、绕过登录闸的 hook | 构建时打 **patch**(`patches/`)+ Python **shim**(连 Gateway) |
| openclaw | 边界事件发射器 | openclaw **插件**(`extensions/events-emitter`) |
| hermes-agent | 事件发射器 + MCP 桥 | hermes **扩展**(`extensions/hermes-codie-emitter`) |

外加每个镜像都烤进的共享工具链 `docker-registry/shared/install-agent-baseline.sh`。

## 目录

```
docker-registry/
  scripts/                build-<agent>-docker.sh + build-<x>-mcp-docker.sh + _lib.sh
  agent-dockerfiles/      codie 的包装 Dockerfile + patch/shim/emitter（每个 agent 一份）
  sidecars/<name>/        MCP sidecar 自包含上下文(Dockerfile + server.py + pyproject)
  shared/                 共享工具链脚本
  agents/<agent>/         ← 上游 fork 在此被 checkout/clone（gitignored,绝不入库）
.github/workflows/
  build-agent-images.yml    agent 多架构构建(openhuman/openclaw/hermes)→ ghcr.io
  build-sidecar-images.yml  sidecar 多架构构建(media/browser/search/memory/home)→ ghcr.io
```

## sidecar 与 agent 的区别

| | agent(openhuman/openclaw/hermes) | sidecar(media/browser/search/memory/home MCP) |
|---|---|---|
| 源码 | 外部 fork(checkout 当上下文) | **本仓库自带**(`docker-registry/sidecars/<name>/`,无 fork) |
| workflow | `build-agent-images.yml`(`workflow_dispatch` / `v*` tag) | `build-sidecar-images.yml`(`workflow_dispatch` / `sidecar-v*` tag) |
| 构建方式 | 同一套:原生 runner per-arch → push-by-digest → `imagetools create` 合并多架构 + 打 tag | 同左 |

> `codie_host` sidecar 是 **PyInstaller 二进制**、随 Bridge 打包,**不是容器镜像**,不在此流水线。CodieClaw(`codie-claw`)押后:它的上下文是整个私有 monorepo,形态不同。

## CI 怎么跑(GitHub Actions)

`build-agent-images.yml`,**每个架构在各自的原生 runner 上构建,无 QEMU**(amd64 → `ubuntu-latest`,arm64 → `ubuntu-24.04-arm`)。两段式:

**Stage 1 `build`**(矩阵 3 agent × 2 架构 = 6 个 job,各自原生):
1. checkout 本仓库(包装)→ 工作区根
2. checkout 对应 fork → `docker-registry/agents/<agent>`(纯上游,当上下文)
3. setup Buildx(**不装 QEMU**——runner 本身就是目标架构)
4. 登录 ghcr.io(内置 `GITHUB_TOKEN`)
5. 跑 `build-<agent>-docker.sh`,`BUILD_MODE=digest` + `PLATFORMS=<单架构>` → 按 **digest 推送**(无 tag),`--metadata-file` 取回 digest,上传为 artifact

**Stage 2 `merge`**(矩阵 3 agent,`needs: build`):下载该 agent 两个架构的 digest → `docker buildx imagetools create` 合并成**多架构 manifest list**,打 `:latest` + `:YYYYMMDD-HHMM-<sha>` 两个 tag。

**触发**:`workflow_dispatch`(手动)或打 `v*` tag。要增减架构,直接改 `build` 的矩阵 `include`。

> **为什么原生而非 QEMU**:openhuman(Rust 全量编译)、openclaw(Node/tsdown 打包)是 CPU 密集型,QEMU 模拟 arm64 会慢 5～10 倍、动辄数小时甚至撞 6 小时 job 上限。原生 arm runner 把这些降到几十分钟。公开仓库的 GitHub 托管 arm runner 免费。

> **本地/单机多架构**:`_lib.sh` 默认 `BUILD_MODE=manifest`(老路径,`-t tag --push` 一次出多架构 manifest),本地构建行为不变;`digest` 模式仅 CI 用。

## 本地构建(可选)

```bash
git clone https://github.com/shanhaobo/openhuman docker-registry/agents/openhuman
REGISTRIES=ghcr.io/<owner> bash docker-registry/scripts/build-openhuman-docker.sh
```

## 加 ACR / TCR(以后)

见 `build-agent-images.yml` 末尾注释:加 ACR/TCR 的 `docker/login-action` 步骤 + secrets,把 `REGISTRIES` 改成逗号列表。一次 buildx 构建同时推三处,digest 一致、多架构保留。

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
  scripts/                build-<agent>-docker.sh + _lib.sh(已公网安全化:只把 localhost 判 insecure)
  agent-dockerfiles/      codie 的包装 Dockerfile + patch/shim/emitter（每个 agent 一份）
  shared/                 共享工具链脚本
  agents/<agent>/         ← 上游 fork 在此被 checkout/clone（gitignored,绝不入库）
.github/workflows/
  build-agent-images.yml  多架构构建 → ghcr.io
```

## CI 怎么跑(GitHub Actions)

`build-agent-images.yml`,矩阵跑三个 agent,每个:

1. checkout 本仓库(包装)→ 工作区根
2. checkout 对应 fork → `docker-registry/agents/<agent>`(纯上游,当上下文)
3. setup QEMU(arm64 模拟)+ Buildx
4. 登录 ghcr.io(内置 `GITHUB_TOKEN`)
5. 跑 `build-<agent>-docker.sh`,`REGISTRIES=ghcr.io/<owner>` → buildx 多架构 `--push`

**触发**:`workflow_dispatch`(手动,可选 `platforms`)或打 `v*` tag。

> **首次验证提速**:openhuman 是 Rust,arm64 走 QEMU 模拟很慢。先用 `workflow_dispatch` 把 `platforms` 填 `linux/amd64` 跑通,再开 `linux/arm64,linux/amd64`。

## 本地构建(可选)

```bash
git clone https://github.com/shanhaobo/openhuman docker-registry/agents/openhuman
REGISTRIES=ghcr.io/<owner> bash docker-registry/scripts/build-openhuman-docker.sh
```

## 加 ACR / TCR(以后)

见 `build-agent-images.yml` 末尾注释:加 ACR/TCR 的 `docker/login-action` 步骤 + secrets,把 `REGISTRIES` 改成逗号列表。一次 buildx 构建同时推三处,digest 一致、多架构保留。

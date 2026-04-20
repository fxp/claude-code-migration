# claude-code-migration

> Python 工具集 + Claude Code Skills，把 Claude Code / Chat / Cowork 的本地数据迁到 Hermes / OpenCode / Cursor / Windsurf / neuDrive Hub

[![PyPI](https://img.shields.io/pypi/v/claude-code-migration)](https://pypi.org/project/claude-code-migration/)
[![tests](https://img.shields.io/badge/tests-129%20passing-brightgreen)](./tests/)
[![python](https://img.shields.io/pypi/pyversions/claude-code-migration)](./pyproject.toml)
[![license](https://img.shields.io/pypi/l/claude-code-migration)](./LICENSE)
[![changelog](https://img.shields.io/badge/changelog-keepachangelog-brightgreen)](./CHANGELOG.md)

---

## 背景

Claude 账号风控收紧，担心积累的 CLAUDE.md / 对话 / 项目 / 自定义 agents / 技能丢失？

这个仓库提供两种等效的使用方式：

1. **Python 包**（推荐）— `pip install` 后用 `ccm` CLI，跑得起测试、可集成进 CI
2. **Claude Code Skills** — 在 Claude Code 里直接 `/claude-full-migration`，适合无 Python 环境场景

**两种运行模式**：
- **一次性迁移**（默认）：`ccm export` / `ccm apply` / `ccm migrate` / `ccm panic-backup`
- **always-on hub**（v1.1+，`[hub]` extra）：`ccm hub serve` 让 Claude Code / Cursor / Codex
  等 agent 实时共享同一份 Workspace Dossier，靠 Supabase 后端 + 本地 SQLite buffer 离线也能工作

## 工具架构：N×M → N+M via Workspace Dossier

不做点对点的 N×M 适配器组合，而是通过 **Workspace Dossier（项目档案）** 做 N+M 转换。
任意 source → Dossier → 任意 target，**新增平台只需一个 parser**。

```
SOURCES (N)                 WORKSPACE DOSSIER               TARGETS (M)
─────────────────           ─────────────────               ─────────────────
💻 Claude Code         ┐                                    ┌  🔱 Hermes Agent
💬 Claude.ai Chat      │    ┌────────────────────┐          │  ◇ OpenCode
👥 Claude Cowork       ├───▶│ dossier.json        │──────────┤  ✎ Cursor
✎ Cursor               │    │  (vendor-neutral,   │          │  ⚡ Windsurf
◇ OpenCode             │    │   redacted, 0600,   │          │  🔱 neuDrive Hub
🔱 Hermes              │    │   belongs to you)   │          │  (any new target)
⚡ Windsurf            ┘    └────────────────────┘          └
```

> **术语**：代码里叫 `CanonicalData` / 内部习惯叫 IR；**对外统称 Workspace Dossier（项目档案）**。
> 这是一份归你所有、厂商中立的工作记录——你的 memory、agents、skills、sessions、MCP 配置——
> 未来换 Agent 时带着它走就行。

任何 Agent 之间互迁：

```bash
# Cursor → OpenCode（任意两目标互迁）
ccm migrate --source cursor --project ~/my-proj --target opencode

# Hermes → Windsurf（迁回非 Hermes）
ccm migrate --source hermes --target windsurf

# OpenCode → Cursor
ccm migrate --source opencode --project ~/oc-proj --target cursor
```

## Python 包（推荐）

### 安装

只做迁移（最小体积）：

```bash
pip install claude-code-migration
```

想用 hub 模式（需要 fsnotify + supabase + psycopg）：

```bash
pip install 'claude-code-migration[hub]'
```

或 pipx（推荐，全局 CLI 隔离）：

```bash
pipx install claude-code-migration
# 加 hub extras：
pipx install 'claude-code-migration[hub]'
```

从源码安装（开发者）：

```bash
git clone https://github.com/fxp/claude-code-migration
cd claude-code-migration
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[hub,dev]'
```

CLI 入口：`ccm` 或 `claude-code-migration`。

### 3 步使用（与你的心智模型对齐）

```
 ┌── Step 1 ─────────────┐    ┌── Step 2 ────────────────┐    ┌── Step 3 ───────────────┐
 │ 告诉工具项目在哪      │ →  │ 自动识别 + 导出为项目档案 │ →  │ 按选定目标框架生成项目  │
 │ (--project /path)     │    │ (dossier.json)           │    │ (--target hermes/…)     │
 └───────────────────────┘    └──────────────────────────┘    └─────────────────────────┘
```

```bash
# Step 2 · 扫描并导出为 Workspace Dossier（项目档案）
ccm export --project /path/to/your-project --out ./ccm-output/dossier.json

# Step 3 · 把同一份档案生成为任意目标（可反复跑，换 target 不必重扫）
ccm apply --dossier ./ccm-output/dossier.json --target hermes   --out ./ccm-output
ccm apply --dossier ./ccm-output/dossier.json --target opencode --out ./ccm-output
ccm apply --dossier ./ccm-output/dossier.json --target cursor,windsurf --out ./ccm-output

# 一次跑完（export + apply 打包）
ccm migrate --project /path/to/your-project --target hermes,opencode,cursor,windsurf

# 含 Claude.ai / Cowork ZIP（从 Settings → Privacy → Export data 拿到）
ccm migrate --project /path/to/your-project \
            --cowork-zip ~/Downloads/claude-data-export.zip \
            --target hermes,opencode

# neuDrive Hub 推送（走 legacy scan.json；ccm scan 产出该格式）
ccm scan     --project /path/to/your-project --out ./ccm-output/scan.json
ccm push-hub --scan   ./ccm-output/scan.json --token $NEUDRIVE_TOKEN
```

> 老用户：`--ir` 作为别名保留，`ccm apply --ir old-ir.json` 仍然工作。

**为什么分三步？** 一次 `export` 后，`apply` 可以针对不同 target 反复跑，而不用再扫一遍
（大项目的 session JSONL 常常几十 MB）。Dossier 是纯 JSON，可审计、可版本控制。

**关于安全性**：写盘的 `dossier.json` / `scan.json` 自动完成：
- **明文密钥 redact**：MCP headers 里的 Bearer、环境变量里的 `*_API_KEY`、会话正文/历史里粘贴的
  `sk-ant-*` / `ghp_*` / `AKIA*` / PEM private keys / BigModel `32hex.16alnum` 全部替换为
  `${CC_<PATH>}` 占位符，adapters 会把这些占位符原样传到 target config（运行时读 env）。
- **文件权限 0o600**：user-only，防止共享机器上其他账户 `cat` 走。
- **同目录生成 `*.secrets-manifest.json`**：只含 SHA256 前缀和建议 env var 名，不含原文；
  可以用来审计"这次导出哪些密钥被脱敏了"。
- 即便是经过脱敏，Dossier 仍然包含 session 正文、shell 快照等隐私内容 —— **不建议 commit 到公共仓库**。
  自用备份 / 本地迁移是完全安全的。

### 安全默认

⚠️ 默认 **不动你的真实项目目录**。所有产物包括那些"本应放到项目根"的文件（`.cursor/rules/`, `.windsurfrules`, `AGENTS.md`, `.hermes.md`）都被 staging 到 `<out>/<target>-target/<project-name>/` 下。

确认无误后复制过去，或用 `--in-place` 让工具直接写入项目（**仅在干净 git 分支上使用**）。

### Hub 模式（v1.1+，一次性迁移之外的连续同步）

上面的 3 步是 **一次性** 的（你跑一下、产出一份 dossier、复制到新 agent）。从 v1.1 开始，
ccm 带了一个可选的 **always-on hub 模式** —— 捕获 Claude Code 的每一次对话、tool 调用、
shell 快照等，实时写进一个 Supabase 后端；任何 agent 未来都能通过 MCP 读到相同数据。

```bash
# 第一次：装 hub 依赖（watchdog / supabase / psycopg）
pip install 'claude-code-migration[hub]'

# L4 本地 buffer 初始化
ccm hub init                # 创建 ~/.dossier-hub/buffer.db

# 纯本地模式（不上云；outbox 累积，MCP 读本地 mirror）
ccm hub serve --local-only

# 真跑：推到你的 Supabase
export SUPABASE_URL=https://xxx.supabase.co
export SUPABASE_SERVICE_KEY=...
export SUPABASE_DB_URL=postgresql://...
ccm hub migrate                     # 一次性：把 20 张 dossier 表建起来
ccm hub serve --remote              # 长驻：captures → outbox → Supabase
ccm hub status                      # 查看 outbox / dead-letter / 同步水位
```

四层架构：**L1+L2 Supabase (pgvector+tsvector)** · **L3 hub-agent 守护进程（captures +
drain worker + mirror subscriber）** · **L4 SQLite outbox + mirror（离线保险）**。
详细设计见 [`docs/HUB_ARCHITECTURE.md`](docs/HUB_ARCHITECTURE.md)。

**核心特性**：
- **离线优先**：captures 毫秒级写进 L4 outbox，network 断了也不丢数据
- **幂等**：同一条消息被 capture 两次，Supabase UPSERT + source_uuid UNIQUE 自动去重
- **Redactor 全程过滤**：每个 capture payload 先过 ccm 的 redactor，Bearer / `sk-ant-*`
  / `ghp_*` / PEM keys 等自动替换成 `${CC_*}` 占位符
- **实时镜像**：Supabase Realtime 把 `dossier_*` 表 push 到 L4 的 `mirror_*` 表，
  agent 通过 MCP 查询 mirror（< 1ms），零网络往返
- **dead-letter 死信**：重试 ≥ 10 次的行挪到 `dead_letter` 表，人工复盘

### 架构

```
src/claude_code_migration/
├── scanner.py              Claude Code 数据扫描（60+ 种）
├── canonical.py            Workspace Dossier 数据类型（CanonicalData）
├── redactor.py             密钥脱敏（Bearer/sk-ant-*/ghp_*/AKIA*/PEM/...）
├── secrets.py              API key / Bearer token 检测（_archive/secrets-manifest.json）
├── cowork.py               Claude.ai ZIP 解析（2026 schema）
├── neudrive.py             neuDrive Hub HTTP 客户端（push-hub verb 用）
├── panic_backup.py         应急全量备份
├── __main__.py             顶层 CLI
├── sources/                反向 source parsers（cursor/opencode/hermes/windsurf → Dossier）
├── adapters/               目标 writers（Dossier → hermes/opencode/cursor/windsurf）
│   ├── base.py             Adapter 抽象类 + AGENTS.md 合成器
│   ├── hermes.py           config.yaml + memories/ + state.db SQLite FTS5
│   ├── opencode.py         opencode.json + mcp 本地/远程 + skills
│   ├── cursor.py           .cursor/rules/*.mdc + .cursor/mcp.json
│   └── windsurf.py         .windsurfrules + .windsurf/rules/ + mcp_config.json
└── hub/                    ★ v1.1 新增：always-on hub 模式（[hub] extra）
    ├── buffer.py           L4 SQLite outbox + mirror + FTS5
    ├── drain.py            后台 drain worker（outbox → Supabase）
    ├── mirror.py           Realtime subscriber + bootstrap + delta_resync
    ├── daemon.py           HubDaemon / HubConfig
    ├── redact.py           capture 中间件（包 ccm.redactor）
    ├── supabase_client.py  HubClient 抽象 + InMemory/DryRun/真实 Supabase
    ├── __main__.py         `ccm hub` 子命令注册
    ├── captures/           插件化 capture 实现
    │   ├── base.py         Capture 基类
    │   └── claude_code_fs.py  ~/.claude/projects/*/*.jsonl 实时追加
    └── sql/                Supabase schema / indexes / RLS / RPC migrations
```

### 测试

```bash
pip install -e '.[hub,dev]'     # 装全套
pytest tests/                    # 129 个测试全部通过
```

- **`test_e2e.py`** (7)：format-level 验证
- **`test_e2e_live.py`** (11)：**真实子进程执行**（`opencode models` 等）
- **`test_cowork.py`** (12)：插件清单 + org metadata 传播
- **`test_cowork_full.py`** (13)：Cowork Projects + `_archive` + 不可迁移项
- **`test_roundtrip.py`** (13)：**任意 source → Dossier → 任意 target** 互迁

## 目标框架支持矩阵

| 目标 | 项目上下文 | MCP | 记忆/技能 | 会话恢复 | 已验证 |
|------|-----------|-----|----------|---------|-------|
| **Hermes Agent** | CLAUDE.md 原生 + `.hermes.md` | `config.yaml custom_providers` | `~/.hermes/memories/` + skills/cc-* | ✅ SQLite FTS5 `state.db` | ✅ |
| **OpenCode** | AGENTS.md + CLAUDE.md 原生 | `opencode.json mcp` (local/remote) | `.opencode/agents` + `cc-*` skills | `opencode export/import` | ✅ 真机验证 |
| **Cursor** | AGENTS.md + `.cursor/rules/*.mdc` | `.cursor/mcp.json` | rules 系统 | ❌ 无会话恢复 | ✅ schema |
| **Windsurf** | `.windsurfrules` + `.windsurf/rules/` | `mcp_config.json` (serverUrl) | rules 系统 | ❌ 无会话恢复 | ✅ schema |
| **neuDrive Hub** | `/memory/profile/*` | `/agent/vault/` | `/conversations/{platform}/` | `hermes --resume` via MCP | ✅ |

## BigModel GLM-5（推荐配置）

- 注册：https://open.bigmodel.cn/ （新账号送 2000 万 tokens 免费额度）
- API key 管理：https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys
- 适配器自动生成对应 provider 配置：

```yaml
# Hermes (config.yaml)
model: { provider: custom, model_name: glm-5 }
custom_providers:
  bigmodel:
    base_url: https://open.bigmodel.cn/api/paas/v4
    api_key: ${OPENAI_API_KEY}
```

```json
// OpenCode (opencode.json)
{
  "model": "bigmodel/glm-5",
  "provider": {
    "bigmodel": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "https://open.bigmodel.cn/api/paas/v4",
        "apiKey": "{env:GLM_API_KEY}"
      }
    }
  }
}
```

## Claude Code Skills（备选）

如果不想装 Python，skills 版本提供同等能力（通过 Claude Code 内驱动）：

```bash
mkdir -p ~/.claude/skills
cp -r skills/* ~/.claude/skills/
```

然后在 Claude Code 里：

```
/claude-full-migration      # meta-skill，编排 4 个子 skill
/code-migration             # 多目标泛化
/hermes-migration           # Hermes 专用
/chat-migration             # Claude.ai 官方 ZIP 解析
/cowork-migration           # 团队 workspace
/neudrive-sync              # 推送到 neuDrive Hub
```

## 验证过的真实项目

在两个数据特征不同的项目上做过真实端到端测试：

**OpenClaw Course**（CLAUDE.md+memory+skills+sessions 场景）
- 5 memory files, 4 sessions, 18 subagents, 48 global skills, 1 MCP Bearer token
- Hermes: SQLite state.db imported 5 sessions + 133 messages, FTS5 "OpenClaw" 25 hits
- OpenCode: 55 files including all skills with cc- prefix

**IdeaToProd**（hooks+`.mcp.json`+env+launch 场景）
- 1 session (106 messages), Linear MCP via `.mcp.json`, PostToolUse hooks
- OpenCode 真实运行：`opencode models` 输出含 `bigmodel/glm-5`，`opencode mcp list` 显示 `cc-web-search-prime` 和 `cc-proj-linear` 都被加载

## 安全

1. **密钥不明文**：所有 MCP headers 中的 Bearer token 在输出里都替换为各目标的 env 引用（`${OPENAI_API_KEY}` / `{env:VAR}` / `${env:VAR}`）
2. **测试断言**：`test_all_targets_zero_plaintext_secrets` 扫描每个 adapter 的每个输出文件，断言无 secret 值泄漏
3. **项目不被污染**：默认 staging 模式，需要 `--in-place` 显式才写真实项目

## 参考链接

- neuDrive: https://github.com/agi-bar/neuDrive
- Hermes Agent: https://github.com/nousresearch/hermes-agent
- OpenCode: https://github.com/sst/opencode
- Claude Code 文档: https://code.claude.com/docs/en/overview

## License

MIT

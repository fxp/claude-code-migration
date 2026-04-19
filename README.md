# claude-code-migration

> Python 工具集 + Claude Code Skills，把 Claude Code / Chat / Cowork 的本地数据迁到 Hermes / OpenCode / Cursor / Windsurf / neuDrive Hub

[![package](https://img.shields.io/badge/package-v0.2.0-blue)](./pyproject.toml)
[![tests](https://img.shields.io/badge/tests-56%20passing-brightgreen)](./tests/)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](./pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](./LICENSE)

---

## 背景

Claude 账号风控收紧，担心积累的 CLAUDE.md / 对话 / 项目 / 自定义 agents / 技能丢失？

这个仓库提供两种等效的使用方式：

1. **Python 包**（推荐）— `pip install` 后用 `ccm` CLI，跑得起测试、可集成进 CI
2. **Claude Code Skills** — 在 Claude Code 里直接 `/claude-full-migration`，适合无 Python 环境场景

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

```bash
git clone https://github.com/fxp/claude-code-migration
cd claude-code-migration
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
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

### 架构

```
src/claude_code_migration/
├── scanner.py              Claude Code 数据扫描（60+ 种：含 session 正文、subagents、tool-results、shell-snapshots、file-history、per-project state）
├── secrets.py              API Key / Bearer token 检测
├── cowork.py               Claude.ai ZIP 解析（2026 schema）
├── hub.py                  neuDrive HTTP 客户端（调 API，不拷代码）
├── __main__.py             CLI
└── adapters/
    ├── base.py             Adapter 抽象类 + AGENTS.md 合成器
    ├── hermes.py           config.yaml + memories/ + state.db SQLite FTS5
    ├── opencode.py         opencode.json + mcp 本地/远程 + skills
    ├── cursor.py           .cursor/rules/*.mdc + .cursor/mcp.json
    └── windsurf.py         .windsurfrules + .windsurf/rules/ + mcp_config.json
```

### 测试

```bash
pip install pytest
pytest tests/            # 56 个测试全部通过
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

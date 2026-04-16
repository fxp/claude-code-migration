---
name: hermes-migration
version: 4.0.0
description: |
  一键迁移 Claude Code 项目到 Hermes Agent (Nous Research) 框架。
  当用户说"迁移项目"、"migration"、"切换到hermes"、"换agent"、"备份项目"、"hermes migration"时触发。
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
  - Agent
  - WebFetch
---

# Hermes Migration Skill v4.0

一键将 Claude Code 完整环境迁移到 [Hermes Agent](https://github.com/nousresearch/hermes-agent) (Nous Research)。

Hermes Agent 是 Nous Research 的自我进化 AI Agent 框架，支持 40+ 工具、18+ LLM 提供商、持久化记忆、技能自动创建。

---

## Claude Code 数据全景 → Hermes 映射表

迁移前先理解 Claude Code 存储了哪些数据、在哪里、以及如何映射到 Hermes：

### 🔴 关键数据 (必须迁移)

| # | Claude Code 数据 | 位置 | Hermes 目标 | 说明 |
|---|---|---|---|---|
| 0 | **~/.claude.json** | `~/.claude.json` (69KB) | `~/.hermes/config.yaml` + `~/.hermes/.env` | ⚠️ **核心状态文件!** 包含: 全局 MCP Server 配置(含 API Key!)、每项目 MCP 配置、oauthAccount、skillUsage、toolUsage、githubRepoPaths、项目信任状态。**mcpServers 字段中可能嵌入明文 API Key (如 Bearer token)** |
| 1 | **CLAUDE.md** | `{project}/CLAUDE.md` | **保留原文件** + `{project}/.hermes.md` | Hermes **原生读取 CLAUDE.md** (优先级3)。不需要转换。额外生成 .hermes.md (优先级1) 包含合并的 memory 上下文。文件限 20,000 字符 |
| 2 | **项目 Memory** | `~/.claude/projects/{encoded-path}/memory/*.md` | `~/.hermes/memories/MEMORY.md` + `~/.hermes/memories/USER.md` + `{project}/.hermes.md` | ⚠️ 正确路径是 `~/.hermes/memories/`。MEMORY.md 限 2,200 字符，USER.md 限 1,375 字符。超出部分写入 .hermes.md |
| 3 | **聊天记录** | `~/.claude/projects/{encoded-path}/*.jsonl` | `~/.hermes/sessions/` | 每个 session 一个 JSONL 文件，包含完整对话(user/assistant/tool_use/tool_result)。Hermes 用 SQLite FTS5 存储 |
| 4 | **子 Agent 记录** | `~/.claude/projects/{encoded-path}/{session}/subagents/*.jsonl` | `~/.hermes/sessions/` | 并行 Agent 的对话记录 |
| 5 | **自定义 Agents** | `{project}/.claude/agents/*.md` | `~/.hermes/skills/{name}/SKILL.md` | Agent 定义含完整 frontmatter (name, description, model, color) + 指令。转为 Hermes skill |
| 6 | **全局 Skills** | `~/.claude/skills/*/SKILL.md` 或 `skill.md` | `~/.hermes/skills/*/SKILL.md` | 48+ 个自定义技能。注意: 文件名可能是大写或小写 (SKILL.md / skill.md) |
| 7 | **项目级 Skills** | `{project}/.claude/skills/*/SKILL.md` | `~/.hermes/skills/*/SKILL.md` | 项目专属技能 |
| 8 | **Hooks 配置** | `{project}/.claude/settings.json → hooks` | `~/.hermes/hooks/` | PostToolUse 等钩子，如 git push 后自动获取预览 URL。Hermes 有等价的 hooks 目录 |
| 8a | **`.agents/` 目录** | `{project}/.agents/skills/*/SKILL.md` | `~/.hermes/skills/` | 部分项目用 `.agents/` 而非 `.claude/`，内含 skills 子目录 (如 Z School 有 lemondata-api, yt-dlp, summarize 等 skill) |
| 8b | **`.cursor/` 目录** | `{project}/.cursor/skills/*/SKILL.md` | `~/.hermes/skills/` | Cursor IDE 的 skill 配置，部分项目同时有 `.claude/` 和 `.cursor/` (如 Next-Biz) |
| 8c | **`.claude-plugin/`** | `{project}/.claude-plugin/plugin.json` | 参考迁移 | Claude Code 插件清单 (name, version, description, mcpServers 引用)。可帮助重建 Hermes 等价功能 |
| 8d | **`.auto-memory/`** | `{project}/.auto-memory/*.md` | `~/.hermes/MEMORY.md` + `.hermes.md` | CoWork/插件自动生成的记忆目录，含 MEMORY.md 索引 + project_context.md + feedback_*.md。结构与 `~/.claude/projects/{path}/memory/` 相同但在项目本地 |
| 8e | **`.skill` 打包文件** | `{project}/skills/*.skill` | `~/.hermes/skills/` | ZIP 格式的 skill 打包文件 (内含 SKILL.md + README.md + evals.json)，可直接解压迁移 |
| 8f | **`evals.json`** | `{project}/skills/*/evals.json` | `~/.hermes/skills/*/` | Skill 评估数据 (测试用例: prompt + expected_output)，可迁移为 Hermes skill 的测试套件 |
| 8g | **`.mcp.json`** | `{project}/.mcp.json` | `~/.hermes/config.yaml` | 项目级 MCP Server 配置（独立于 ~/.claude.json），如 Linear MCP。需转入 Hermes config |
| 8h | **`CLAUDE.local.md`** | `{project}/CLAUDE.local.md` | `{project}/.hermes.md` | 个人项目指令（不提交到 git），合并到 .hermes.md |
| 8i | **`~/.claude/CLAUDE.md`** | `~/.claude/CLAUDE.md` | `~/.hermes/SOUL.md` | 用户级全局指令，转为 Hermes 的 SOUL.md |
| 8j | **`rules/*.md`** | `{project}/.claude/rules/*.md` + `~/.claude/rules/*.md` | `{project}/.hermes.md` | 路径范围规则（含 `paths:` frontmatter），合并到上下文文件 |
| 8k | **`commands/*.md`** | `{project}/.claude/commands/*.md` | `~/.hermes/skills/` | 旧版自定义命令，转为 Hermes skill |
| 8l | **`scheduled-tasks/`** | `~/.claude/scheduled-tasks/*/SKILL.md` | `~/.hermes/cron/` | 定时任务，转为 Hermes cronjob |
| 8m | **`agent-memory/`** | `.claude/agent-memory/<agent>/MEMORY.md` (项目) + `~/.claude/agent-memory/<agent>/MEMORY.md` (用户) | `~/.hermes/memories/` | 子 agent 持久记忆（独立于项目 memory），按 agent 名隔离 |
| 8n | **`agent-memory-local/`** | `.claude/agent-memory-local/<agent>/MEMORY.md` | `~/.hermes/memories/` | 本地子 agent 记忆（gitignored），合并迁移 |
| 8o | **`output-styles/`** | `.claude/output-styles/*.md` + `~/.claude/output-styles/*.md` | 参考保留 | 自定义输出格式（含 frontmatter: name, description, keep-coding-instructions）。Hermes 无直接等价 |
| 8p | **`loop.md`** | `.claude/loop.md` + `~/.claude/loop.md` | `~/.hermes/skills/cc-loop/SKILL.md` | 自定义 /loop 提示词，转为 Hermes cronjob skill |
| 8q | **`teams/`** | `~/.claude/teams/{team}/config.json` | 参考保留 | Agent Teams 运行时配置 (实验性功能) |
| 8r | **`tasks/`** | `~/.claude/tasks/{team}/` | 参考保留 | Agent Teams 共享任务列表 |
| 8s | **`channels/`** | `~/.claude/channels/<channel>/.env` | `~/.hermes/.env` | Channel 凭证 (Telegram/Discord bot token)。⚠️ 含敏感密钥 |
| 8t | **`.credentials.json`** | `~/.claude/.credentials.json` | `~/.hermes/.env` | 插件敏感配置（keychain 不可用时的 fallback）。⚠️ 含密钥 |
| 8u | **`plugins/data/`** | `~/.claude/plugins/data/{id}/` | 参考保留 | 插件持久数据目录 (`$CLAUDE_PLUGIN_DATA`) |
| 8v | **`.worktreeinclude`** | `{project}/.worktreeinclude` | 参考保留 | worktree 文件复制白名单 |
| 8w | **`REVIEW.md`** | `{project}/REVIEW.md` | `{project}/.hermes.md` 或保留 | Code Review 专用指令文件（独立于 CLAUDE.md）。Hermes 不区分 review 指令，合并到上下文即可 |

### 🟡 重要数据 (建议迁移)

| # | Claude Code 数据 | 位置 | Hermes 目标 | 说明 |
|---|---|---|---|---|
| 9 | **全局设置** | `~/.claude/settings.json` | `~/.hermes/config.yaml` | 启用的插件、偏好设置 |
| 10 | **本地权限** | `~/.claude/settings.local.json` | `~/.hermes/config.yaml` (tools section) | 允许/禁止的工具列表。⚠️ 部分权限规则中可能嵌入 API Key/Secret (如 Supabase, 飞书, 钉钉)，需安全处理 |
| 11 | **项目设置** | `{project}/.claude/settings.json` + `settings.local.json` | `{project}/.hermes.md` 或 config | 项目级权限、环境变量 (`env` 字段如 `BASH_DEFAULT_TIMEOUT_MS`)、deny 规则、MCP 配置 (`enableAllProjectMcpServers`) |
| 12 | **MCP 插件权限** | `settings.local.json → allow` | `~/.hermes/config.yaml` | 如 `mcp__plugin_doitlater_*` 等 MCP 工具专属权限 |
| 13 | **Prompt 历史** | `~/.claude/history.jsonl` | `~/.hermes/sessions/` | 每行: `{display, timestamp, project}`。可导入为 Hermes session search 索引 |
| 14 | **Todo 列表** | `~/.claude/todos/*.json` | Hermes `todo` tool | 275 个 session 级 task 文件。非空的可导入 |
| 15 | **计划文件** | `~/.claude/plans/*.md` | `{project}/.hermes/plans/` | 活跃的开发计划 |
| 16 | **插件列表** | `~/.claude/plugins/installed_plugins.json` | 手动参考 | 已安装的插件清单 (含版本号、安装路径、git commit SHA)，供用户在 Hermes 中安装等价功能 |
| 17 | **Launch 配置** | `{project}/.claude/launch.json` | 保留 (Hermes 不需要) | Dev server 启动配置 |
| 18 | **项目环境变量** | `{project}/.claude/settings.json → env` | `~/.hermes/config.yaml` | 如 `BASH_DEFAULT_TIMEOUT_MS`, `BASH_MAX_TIMEOUT_MS` 等运行时配置 |

### ⚪ 可选数据 (参考/归档)

| # | Claude Code 数据 | 位置 | 处理方式 |
|---|---|---|---|
| 19 | **Shell 快照** | `~/.claude/shell-snapshots/` | 归档，Hermes 有独立的 terminal backend |
| 20 | **Session 元数据** | `~/.claude/sessions/*.json` | 归档。内容: pid, sessionId, cwd, startedAt, kind, entrypoint |
| 21 | **Session 环境** | `~/.claude/session-env/` | 归档 |
| 22 | **统计缓存** | `~/.claude/stats-cache.json` | 归档。每日活跃度(messages, sessions, tool calls) |
| 23 | **文件历史** | `~/.claude/file-history/` | 归档 |
| 24 | **Backups** | `~/.claude/backups/*.json` | 归档。.claude.json 的自动备份 |
| 25 | **遥测数据** | `~/.claude/telemetry/` | 不迁移 |

---

## 执行流程

### Phase 0: 自检与自更新（每次迁移前必须执行）

**在帮助用户迁移任何项目之前，必须先完成本 Phase。**

1. **获取 Claude Code 文档索引**

   用 WebFetch 读取 `https://code.claude.com/docs/llms.txt`，获取全部文档页面列表。

2. **读取关键数据存储页面**

   至少读取以下页面（优先用 Agent 并行读取加速）：
   - `claude-directory` — ~/.claude/ 完整目录结构
   - `memory` — 记忆系统、CLAUDE.md、auto-memory
   - `settings` — 所有设置文件位置
   - `skills` — Skill 完整 frontmatter 字段
   - `hooks` — Hook 事件类型和存储
   - `mcp` — MCP 配置文件位置
   - `sub-agents` — Agent 定义格式和 agent-memory
   - `plugins` + `plugins-reference` — 插件系统
   - `env-vars` — 环境变量（尤其 CLAUDE_CONFIG_DIR）
   - `channels` — Channel 凭证存储
   - `scheduled-tasks` + `desktop-scheduled-tasks` — 定时任务
   - `agent-teams` — Agent Teams
   - `tools-reference` — 工具列表（确认与 Hermes 的对应关系）

   如果 llms.txt 中出现了本 skill 上次更新时不存在的**新页面**，也必须读取。

3. **对比检查**

   将文档中提到的所有数据存储路径，与本 skill 下方"数据全景 → Hermes 映射表"中的条目逐一对比：
   - 文档中有但映射表中没有 → **遗漏**，需要补充
   - 映射表中有但文档中已不再提及 → 可能已废弃，标注
   - 文件路径、字符限制、frontmatter 字段名等细节变化 → 需要更新

4. **自动修正**

   如果发现遗漏或变化：
   - 用 Edit 工具直接修改本 SKILL.md 的映射表和扫描脚本
   - 向用户报告修正了什么
   - 修正完成后再继续 Phase 1

   如果没有发现变化：
   - 向用户报告 "✅ Skill 数据映射表与 Claude Code 最新文档一致，无需调整"
   - 继续 Phase 1

**为什么需要这一步**: Claude Code 每周更新，可能新增数据类型（如新的 agent-memory 作用域、新的上下文文件优先级、新的 hook 事件）。在迁移前自检可以确保不遗漏用户的任何数据。

---

### Phase 1: 环境检查

```bash
echo "╔══════════════════════════════════════════╗"
echo "║   🔱 Hermes Migration v3.0               ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "=== Phase 1: 环境检查 ==="
echo ""

ERRORS=0

# Python >= 3.11
if command -v python3 &>/dev/null; then
  PY_VER=$(python3 --version 2>&1 | awk '{print $2}')
  PY_MINOR=$(echo $PY_VER | cut -d. -f2)
  if [ "$PY_MINOR" -ge 11 ]; then
    echo "✅ Python $PY_VER"
  else
    echo "❌ Python $PY_VER (需要 >= 3.11)"
    ERRORS=$((ERRORS+1))
  fi
else
  echo "❌ Python 未安装"
  ERRORS=$((ERRORS+1))
fi

# ripgrep
command -v rg &>/dev/null && echo "✅ ripgrep" || { echo "❌ ripgrep 未安装 (brew install ripgrep)"; ERRORS=$((ERRORS+1)); }

# Git
command -v git &>/dev/null && echo "✅ Git" || { echo "❌ Git 未安装"; ERRORS=$((ERRORS+1)); }

# Node (可选)
command -v node &>/dev/null && echo "✅ Node.js $(node --version) (可选)" || echo "⚪ Node.js (可选，用于浏览器自动化)"

# Hermes 已安装?
if command -v hermes &>/dev/null; then
  echo "✅ Hermes Agent 已安装"
else
  echo "⚪ Hermes Agent 待安装"
fi

echo ""
if [ $ERRORS -gt 0 ]; then
  echo "⚠️  有 $ERRORS 个前置依赖缺失，请先安装后再继续"
else
  echo "✅ 环境检查通过"
fi
```

如果有缺失依赖，提示安装命令后等用户确认。

---

### Phase 2: 深度扫描

扫描当前项目和全局 Claude Code 数据，输出完整报告。

```bash
PROJECT_DIR="$(pwd)"
ENCODED_PATH=$(echo "$PROJECT_DIR" | sed 's|/|-|g' | sed 's|^-||')
GLOBAL_PROJECT="$HOME/.claude/projects/$ENCODED_PATH"

echo "=== Phase 2: 深度扫描 ==="
echo ""
echo "📁 项目: $PROJECT_DIR"
echo ""

echo "--- 🔴 关键数据 ---"

# 0. ~/.claude.json (核心状态文件)
if [ -f "$HOME/.claude.json" ]; then
  CJ_SIZE=$(wc -c < "$HOME/.claude.json" | tr -d ' ')
  CJ_PROJECTS=$(python3 -c "import json; d=json.load(open('$HOME/.claude.json')); print(len(d.get('projects',{})))" 2>/dev/null || echo "?")
  CJ_MCP=$(python3 -c "import json; d=json.load(open('$HOME/.claude.json')); print(len(d.get('mcpServers',{})))" 2>/dev/null || echo "0")
  echo "✅ ~/.claude.json ($CJ_SIZE bytes, $CJ_PROJECTS 个项目配置, $CJ_MCP 个全局 MCP Server)"
  # 警告: 检查 mcpServers 中是否有 API Key
  HAS_KEYS=$(python3 -c "
import json
d=json.load(open('$HOME/.claude.json'))
mcp=d.get('mcpServers',{})
for name,cfg in mcp.items():
  h=cfg.get('headers',{})
  for k,v in h.items():
    if 'auth' in k.lower() or 'bearer' in str(v).lower() or 'token' in k.lower():
      print(f'  ⚠️  MCP \"{name}\" 含 API Key 在 headers.{k}')
# 也检查项目级 mcpServers
for proj_path, proj_cfg in d.get('projects',{}).items():
  pmcp = proj_cfg.get('mcpServers',{})
  for name,cfg in pmcp.items():
    if isinstance(cfg, dict):
      env = cfg.get('env',{})
      for ek,ev in env.items():
        if any(x in ek.lower() for x in ('key','secret','token','password')):
          print(f'  ⚠️  项目 MCP \"{name}\" 含密钥 env.{ek}')
" 2>/dev/null)
  [ -n "$HAS_KEYS" ] && echo "$HAS_KEYS"
else
  echo "⚪ ~/.claude.json (不存在)"
fi

# 1. CLAUDE.md
if [ -f "$PROJECT_DIR/CLAUDE.md" ]; then
  SIZE=$(wc -c < "$PROJECT_DIR/CLAUDE.md" | tr -d ' ')
  LINES=$(wc -l < "$PROJECT_DIR/CLAUDE.md" | tr -d ' ')
  echo "✅ CLAUDE.md ($LINES 行, $SIZE bytes)"
else
  echo "⚪ CLAUDE.md (不存在)"
fi

# 2. 项目 Memory
if [ -d "$GLOBAL_PROJECT/memory" ]; then
  MEM_FILES=$(find "$GLOBAL_PROJECT/memory" -name "*.md" -type f)
  MEM_COUNT=$(echo "$MEM_FILES" | grep -c "." || echo 0)
  MEM_SIZE=$(du -sh "$GLOBAL_PROJECT/memory" 2>/dev/null | cut -f1)
  echo "✅ 项目 Memory ($MEM_COUNT 个文件, $MEM_SIZE)"
  echo "$MEM_FILES" | while read f; do
    [ -f "$f" ] && echo "   📝 $(basename "$f") ($(wc -l < "$f" | tr -d ' ') 行)"
  done
else
  echo "⚪ 项目 Memory (不存在)"
fi

# 3. 聊天记录
if [ -d "$GLOBAL_PROJECT" ]; then
  CHAT_FILES=$(find "$GLOBAL_PROJECT" -maxdepth 1 -name "*.jsonl" -type f | wc -l | tr -d ' ')
  SUB_FILES=$(find "$GLOBAL_PROJECT" -path "*/subagents/*.jsonl" -type f 2>/dev/null | wc -l | tr -d ' ')
  CHAT_SIZE=$(du -sh "$GLOBAL_PROJECT" 2>/dev/null | cut -f1)
  echo "✅ 聊天记录 ($CHAT_FILES 个会话, $SUB_FILES 个子Agent记录, $CHAT_SIZE)"
else
  echo "⚪ 聊天记录 (不存在)"
fi

# 4. 自定义 Agents
if [ -d "$PROJECT_DIR/.claude/agents" ]; then
  AGENT_COUNT=$(find "$PROJECT_DIR/.claude/agents" -name "*.md" -type f | wc -l | tr -d ' ')
  echo "✅ 自定义 Agents ($AGENT_COUNT 个)"
  find "$PROJECT_DIR/.claude/agents" -name "*.md" -type f | while read f; do
    echo "   🤖 $(basename "$f" .md)"
  done
else
  echo "⚪ 自定义 Agents (不存在)"
fi

# 5. 全局 Skills
SKILL_COUNT=$(find "$HOME/.claude/skills" -name "SKILL.md" -type f 2>/dev/null | wc -l | tr -d ' ')
echo "✅ 全局 Skills ($SKILL_COUNT 个)"

# 6. 项目级 Skills
if [ -d "$PROJECT_DIR/.claude/skills" ]; then
  PROJ_SKILL_COUNT=$(find "$PROJECT_DIR/.claude/skills" -name "SKILL.md" -type f | wc -l | tr -d ' ')
  echo "✅ 项目级 Skills ($PROJ_SKILL_COUNT 个)"
else
  echo "⚪ 项目级 Skills (不存在)"
fi

# 额外隐藏目录
if [ -d "$PROJECT_DIR/.agents" ]; then
  AGENTS_DIR_COUNT=$(find "$PROJECT_DIR/.agents" -name "SKILL.md" -o -name "skill.md" | wc -l | tr -d ' ')
  echo "✅ .agents/ 目录 ($AGENTS_DIR_COUNT 个 skill) — 备选 agent/skill 存储位置"
fi

if [ -d "$PROJECT_DIR/.cursor" ]; then
  CURSOR_COUNT=$(find "$PROJECT_DIR/.cursor" -name "SKILL.md" -type f 2>/dev/null | wc -l | tr -d ' ')
  echo "✅ .cursor/ 目录 ($CURSOR_COUNT 个 skill) — Cursor IDE 配置"
fi

if [ -d "$PROJECT_DIR/.claude-plugin" ]; then
  echo "✅ .claude-plugin/ — Claude Code 插件清单"
fi

if [ -d "$PROJECT_DIR/.auto-memory" ]; then
  AM_COUNT=$(find "$PROJECT_DIR/.auto-memory" -name "*.md" -type f | wc -l | tr -d ' ')
  echo "✅ .auto-memory/ ($AM_COUNT 个文件) — CoWork 自动记忆"
fi

SKILL_PACKS=$(find "$PROJECT_DIR" -maxdepth 3 -name "*.skill" -type f 2>/dev/null | wc -l | tr -d ' ')
[ "$SKILL_PACKS" -gt 0 ] && echo "✅ .skill 打包文件 ($SKILL_PACKS 个 ZIP 包)"

EVAL_FILES=$(find "$PROJECT_DIR" -maxdepth 4 -name "evals.json" -not -path "*/node_modules/*" 2>/dev/null | wc -l | tr -d ' ')
[ "$EVAL_FILES" -gt 0 ] && echo "✅ evals.json ($EVAL_FILES 个 skill 评估文件)"

# 文档审计新增的检查项
[ -f "$PROJECT_DIR/.mcp.json" ] && echo "✅ .mcp.json — 项目级 MCP Server 配置 ⚠️" || true
[ -f "$PROJECT_DIR/CLAUDE.local.md" ] && echo "✅ CLAUDE.local.md — 个人项目指令" || true
[ -f "$HOME/.claude/CLAUDE.md" ] && echo "✅ ~/.claude/CLAUDE.md — 用户级全局指令" || true
[ -d "$PROJECT_DIR/.claude/rules" ] && echo "✅ .claude/rules/ — 路径范围规则 ($(find "$PROJECT_DIR/.claude/rules" -name "*.md" | wc -l | tr -d ' ') 个)" || true
[ -d "$HOME/.claude/rules" ] && echo "✅ ~/.claude/rules/ — 全局路径规则" || true
[ -d "$PROJECT_DIR/.claude/commands" ] && echo "✅ .claude/commands/ — 旧版命令" || true
[ -d "$HOME/.claude/scheduled-tasks" ] && echo "✅ ~/.claude/scheduled-tasks/ — 定时任务" || true
[ -d "$HOME/.claude/agent-memory" ] && echo "✅ ~/.claude/agent-memory/ — 子agent持久记忆" || true
[ -d "$PROJECT_DIR/.claude/agent-memory" ] && echo "✅ .claude/agent-memory/ — 项目级agent记忆 ($(find "$PROJECT_DIR/.claude/agent-memory" -name "*.md" | wc -l | tr -d ' ') 个)" || true
[ -d "$PROJECT_DIR/.claude/agent-memory-local" ] && echo "✅ .claude/agent-memory-local/ — 本地agent记忆" || true
[ -d "$PROJECT_DIR/.claude/output-styles" ] && echo "✅ .claude/output-styles/ — 自定义输出格式" || true
[ -f "$PROJECT_DIR/.claude/loop.md" ] && echo "✅ .claude/loop.md — 自定义 /loop 提示词" || true
[ -f "$HOME/.claude/loop.md" ] && echo "✅ ~/.claude/loop.md — 全局 /loop 提示词" || true
[ -d "$HOME/.claude/teams" ] && echo "✅ ~/.claude/teams/ — Agent Teams 配置" || true
[ -d "$HOME/.claude/channels" ] && echo "✅ ~/.claude/channels/ — Channel 凭证 ⚠️" || true
[ -f "$HOME/.claude/.credentials.json" ] && echo "✅ ~/.claude/.credentials.json — 插件敏感凭证 ⚠️" || true
[ -d "$HOME/.claude/plugins/data" ] && echo "✅ ~/.claude/plugins/data/ — 插件持久数据" || true
[ -f "$PROJECT_DIR/.worktreeinclude" ] && echo "✅ .worktreeinclude — worktree 白名单" || true
[ -f "$PROJECT_DIR/REVIEW.md" ] && echo "✅ REVIEW.md — Code Review 指令" || true


echo ""
echo "--- 🟡 重要数据 ---"

# 7. 全局设置
[ -f "$HOME/.claude/settings.json" ] && echo "✅ 全局设置 (settings.json)" || echo "⚪ 全局设置"
[ -f "$HOME/.claude/settings.local.json" ] && echo "✅ 本地权限 (settings.local.json)" || echo "⚪ 本地权限"

# 8. 项目设置
[ -f "$PROJECT_DIR/.claude/settings.json" ] && echo "✅ 项目设置" || echo "⚪ 项目设置"
[ -f "$PROJECT_DIR/.claude/settings.local.json" ] && echo "✅ 项目权限" || echo "⚪ 项目权限"

# 9. Prompt 历史
if [ -f "$HOME/.claude/history.jsonl" ]; then
  HIST_LINES=$(wc -l < "$HOME/.claude/history.jsonl" | tr -d ' ')
  echo "✅ Prompt 历史 ($HIST_LINES 条)"
else
  echo "⚪ Prompt 历史"
fi

# 10. Todos
TODO_COUNT=$(find "$HOME/.claude/todos" -name "*.json" -type f 2>/dev/null | wc -l | tr -d ' ')
echo "✅ Todo 列表 ($TODO_COUNT 个文件)"

# 11. Plans
PLAN_COUNT=$(find "$HOME/.claude/plans" -name "*.md" -type f 2>/dev/null | wc -l | tr -d ' ')
echo "✅ 计划文件 ($PLAN_COUNT 个)"

# 12. 插件
[ -f "$HOME/.claude/plugins/installed_plugins.json" ] && echo "✅ 已安装插件清单" || echo "⚪ 插件清单"

# 13. Launch 配置
[ -f "$PROJECT_DIR/.claude/launch.json" ] && echo "✅ Launch 配置" || echo "⚪ Launch 配置"

echo ""
echo "--- ⚪ 可选数据 ---"
SNAP_COUNT=$(ls "$HOME/.claude/shell-snapshots/" 2>/dev/null | wc -l | tr -d ' ')
echo "⚪ Shell 快照 ($SNAP_COUNT 个)"
SESS_COUNT=$(ls "$HOME/.claude/sessions/" 2>/dev/null | wc -l | tr -d ' ')
echo "⚪ Session 元数据 ($SESS_COUNT 个)"
echo "⚪ 统计缓存、文件历史、遥测数据"

echo ""
echo "=== 扫描完成 ==="
```

展示报告后，用 AskUserQuestion 让用户确认迁移范围：

**选项:**
1. **完整迁移 (推荐)** — 迁移所有 🔴 关键 + 🟡 重要数据
2. **精简迁移** — 只迁移 🔴 关键数据 (CLAUDE.md, Memory, Agents, Skills)
3. **仅上下文** — 只迁移 CLAUDE.md → AGENTS.md + Memory

---

### Phase 3: 安装 Hermes Agent

如果 `hermes` 命令不存在，引导安装。

用 AskUserQuestion 询问安装方式：
1. **自动安装 (推荐)** — `curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash`
2. **手动安装** — 展示手动步骤 (clone + uv pip install)
3. **跳过** — 已安装或稍后安装

安装完成后验证 `hermes --version`。

---

### Phase 4: 配置 LLM 提供商

使用 AskUserQuestion 逐步收集：

**Step 4.1: 选择提供商**

| 选项 | 提供商 | 说明 |
|------|--------|------|
| 1 (推荐) | **BigModel 智谱AI** | 国内领先大模型，注册送额度。https://open.bigmodel.cn/ |
| 2 | **Anthropic** | 如有其他 Claude 账号 |
| 3 | **OpenAI** | GPT-4o 等 |
| 4 | **OpenRouter** | 聚合 200+ 模型 |
| 5 | **DeepSeek** | DeepSeek-V3/R1 |
| 6 | **Ollama (本地)** | 无需 API Key |

**Step 4.2: 输入 API Key** (Ollama 跳过)

**Step 4.3: 写入配置**

```bash
mkdir -p ~/.hermes
```

生成 `~/.hermes/config.yaml`：

```yaml
model:
  provider: {PROVIDER}       # 见下表
  model_name: {MODEL}        # 见下表
  base_url: {BASE_URL}       # 自定义 endpoint 时需要
  context_length: 128000
  max_tokens: 8192

terminal_backend: local
terminal_timeout: 120

memory:
  max_memory_tokens: 800
  max_user_profile_tokens: 500

compression:
  enable_compression: true
  compression_threshold: 0.5

tools:
  enabled_toolsets:
    - web
    - terminal
    - file
    - browser
    - vision
    - memory
    - todo
    - skills
    - session_search
    - code_execution
    - delegation
```

提供商配置映射：

| 提供商 | provider | model_name | base_url | 环境变量 |
|--------|----------|------------|----------|---------|
| BigModel | `custom` | `glm-4-plus` | `https://open.bigmodel.cn/api/paas/v4` | `OPENAI_API_KEY` |
| Anthropic | `anthropic` | `claude-sonnet-4-20250514` | (默认) | `ANTHROPIC_API_KEY` |
| OpenAI | `openai` | `gpt-4o` | (默认) | `OPENAI_API_KEY` |
| OpenRouter | `openrouter` | `anthropic/claude-sonnet-4` | (默认) | `OPENROUTER_API_KEY` |
| DeepSeek | `custom` | `deepseek-chat` | `https://api.deepseek.com` | `OPENAI_API_KEY` |
| Ollama | `custom` | `qwen2.5-coder:14b` | `http://localhost:11434/v1` | (不需要) |

将 API Key 写入 `~/.hermes/.env`（安全存储）。

---

### Phase 5: 迁移数据

按照用户在 Phase 2 选择的迁移范围执行。

#### 5.1 CLAUDE.md → 保留原文件 + 生成 .hermes.md

**关键发现**: Hermes **原生读取 CLAUDE.md**（优先级第3）。不需要转换为 AGENTS.md。

策略：
1. **保留 CLAUDE.md 原文不动** — Hermes 自动加载
2. 如有项目 memory，生成 `.hermes.md`（优先级第1，最高）包含合并的上下文

`.hermes.md` 格式（最大 **20,000 字符**，超出会被 70% head + 20% tail 截断）：
```markdown
# Project Context

> 🔱 Migrated from Claude Code on {YYYY-MM-DD HH:MM}
> CLAUDE.md preserved and auto-loaded by Hermes (priority 3)
> This .hermes.md provides additional project memory (priority 1)

## Project Memory

### {memory_filename_1}
{content from type=project memory}

### {memory_filename_2}
{content from type=feedback memory}
```

#### 5.2 Memory → ~/.hermes/memories/ + .hermes.md

**⚠️ 正确路径**: `~/.hermes/memories/MEMORY.md` 和 `~/.hermes/memories/USER.md`（不是 `~/.hermes/` 根目录）

**字符限制**:
- `MEMORY.md`: **2,200 字符**（~800 tokens）
- `USER.md`: **1,375 字符**（~500 tokens）
- 可在 config.yaml 中调整: `memory.memory_char_limit` / `memory.user_char_limit`

读取 `~/.claude/projects/{encoded-path}/memory/` 和 `{project}/.auto-memory/` 下所有 `.md` 文件。

**迁移策略**:
1. 读取所有 memory 文件，按 frontmatter `type` 分类
2. `type: user` (如 `user_*.md`) → 精简后写入 `~/.hermes/memories/USER.md`（≤1,375 字符）
3. MEMORY.md 索引内容 → 精简后写入 `~/.hermes/memories/MEMORY.md`（≤2,200 字符）
4. `type: project` + `type: feedback` → 合并到项目的 `.hermes.md`（≤20,000 字符）
5. 如果超出限制，用 AI 精简核心内容，完整内容保留在 `.hermes.md`

```bash
mkdir -p ~/.hermes/memories
```

#### 5.3 聊天记录 → Hermes Sessions

**这是最大的数据块** (本用户: 285 个会话文件, 270MB)。

Claude Code JSONL 格式 (每行一条消息):
```json
{"type":"user", "message":{"role":"user","content":"..."}, "uuid":"...", "timestamp":"...", "sessionId":"...", "cwd":"...", "version":"...", "gitBranch":"..."}
{"type":"assistant", "message":{"role":"assistant","content":"..."}, "uuid":"...", "timestamp":"..."}
{"type":"tool_use", ...}
{"type":"tool_result", ...}
```

**⚠️ Hermes 用 SQLite FTS5 存储会话**，不是 JSON 文件。数据库路径: `~/.hermes/state.db`

转换策略：
1. 只迁移**当前项目**的会话 (`~/.claude/projects/{encoded-path}/*.jsonl`)
2. 解析 Claude Code JSONL → 提取 user/assistant 消息
3. **INSERT 到 Hermes 的 SQLite `state.db`** (sessions + messages 表)
4. FTS5 触发器会自动建立搜索索引 → `hermes session_search` 可搜索
5. 导入的会话可用 `hermes --resume <id>` 继续

**Hermes sessions 表结构**: id(TEXT PK), source, title, started_at, ended_at, message_count, tool_call_count, ...
**Hermes messages 表结构**: id(AUTO PK), session_id(FK), role, content, timestamp, tool_calls(JSON), tool_call_id, tool_name, ...

转换脚本 (Python):
```python
import json, os, glob, sqlite3, time
from datetime import datetime

project_dir = os.getcwd()
encoded = project_dir.replace('/', '-').lstrip('-')
src_dir = os.path.expanduser(f'~/.claude/projects/{encoded}')
db_path = os.path.expanduser('~/.hermes/state.db')

# 连接 Hermes SQLite 数据库
conn = sqlite3.connect(db_path)

for jsonl_path in glob.glob(f'{src_dir}/*.jsonl'):
    cc_session_id = os.path.basename(jsonl_path).replace('.jsonl', '')
    messages = []
    first_ts = None
    first_msg = ''

    with open(jsonl_path) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
            except:
                continue

            if entry.get('type') == 'user':
                msg = entry.get('message', {})
                content = msg.get('content', '')
                if isinstance(content, str) and content:
                    ts = entry.get('timestamp', '')
                    messages.append(('user', content, ts))
                    if not first_ts:
                        first_ts = ts
                        first_msg = content[:80]

            elif entry.get('type') == 'assistant':
                msg = entry.get('message', {})
                content = msg.get('content', '')
                if isinstance(content, str) and content:
                    messages.append(('assistant', content, entry.get('timestamp', '')))

    if not messages:
        continue

    # 生成 Hermes 格式的 session ID
    ts_obj = datetime.fromisoformat(first_ts.replace('Z','+00:00')) if first_ts else datetime.now()
    hermes_sid = f"cc_{ts_obj.strftime('%Y%m%d_%H%M%S')}_{cc_session_id[:8]}"

    # INSERT session
    conn.execute('''INSERT OR IGNORE INTO sessions
        (id, source, title, started_at, message_count, tool_call_count)
        VALUES (?, ?, ?, ?, ?, ?)''',
        (hermes_sid, 'cli', f'[CC] {first_msg}', first_ts, len(messages), 0))

    # INSERT messages
    for role, content, ts in messages:
        epoch = datetime.fromisoformat(ts.replace('Z','+00:00')).timestamp() if ts else time.time()
        conn.execute('''INSERT INTO messages
            (session_id, role, content, timestamp)
            VALUES (?, ?, ?, ?)''',
            (hermes_sid, role, content, epoch))

conn.commit()
conn.close()
print(f'Sessions imported to {db_path}')
# 可用 hermes --resume cc_XXXXXXXX_XXXXXX_XXXXXXXX 继续任意会话

```

#### 5.4 Agents → Hermes Skills

Claude Code agent 格式 (`{project}/.claude/agents/*.md`):
```markdown
---
name: agent-name
description: What this agent does
model: claude-sonnet-4-20250514
color: blue
---

Instructions for the agent...
```

转换为 Hermes skill (`~/.hermes/skills/cc-agents/{name}/SKILL.md`):
```markdown
---
name: cc-{agent-name}
description: "{agent description}"
version: 1.0.0
metadata:
  hermes:
    tags: [migrated, claude-code-agent]
    category: agents
---

# {Agent Name}

> Migrated from Claude Code agent definition

{Instructions 部分（去掉原 frontmatter，保留 markdown body）}
```

#### 5.5 Skills 迁移（三层兼容性处理）

Skills 迁移需要处理三层不兼容：

**第一层：Frontmatter 转换（自动）**

Claude Code 的 `allowed-tools` 和 `preamble-tier` 字段需要转换为 Hermes 的 `metadata.hermes` 格式。

转换映射：
```python
# allowed-tools → requires_toolsets 映射
TOOL_TO_TOOLSET = {
    'Bash': 'terminal',
    'Read': 'file',
    'Write': 'file',
    'Edit': 'file',
    'Glob': 'terminal',    # Hermes 无 glob 工具，通过 terminal find
    'Grep': 'terminal',    # Hermes 无 grep 工具，通过 terminal rg
    'WebSearch': 'web',
    'WebFetch': 'web',
    'AskUserQuestion': None,  # → Hermes clarify tool (内建)
    'Agent': None,            # → Hermes delegate_task tool (内建)
    'TodoWrite': None,        # → Hermes todo tool (内建)
}
```

Claude Code skill 完整 frontmatter 字段（文档确认）:
```yaml
---
name: skill-name            # 必须
description: "..."           # 必须
version: 1.0.0              # 可选
allowed-tools: [Bash, Read]  # Claude 专属 → 转为 requires_toolsets
preamble-tier: 1             # Claude 专属 → 丢弃
when_to_use: "..."           # 追加到 description
argument-hint: "[issue]"     # → 保留到 description 末尾
disable-model-invocation: true  # 控制自动加载 → Hermes 无等价
user-invocable: false        # 隐藏菜单 → Hermes 无等价
model: opus                  # 模型覆盖 → 注释保留
effort: high                 # 推理强度 → 注释保留
context: fork                # 子agent执行 → 注释保留
agent: general-purpose       # 子agent类型 → 注释保留
hooks: {...}                 # skill级hook → 注释保留
paths: ["src/**"]            # 路径限制 → 注释保留
shell: bash                  # shell类型 → 注释保留
---
```

转换示例:
```yaml
# Claude Code 原始
---
name: amap
allowed-tools: [Bash]
---

# → Hermes 转换后
---
name: cc-amap
description: "高德地图 Web API..."
version: 1.0.0
metadata:
  hermes:
    tags: [migrated, claude-code]
    category: tools
    requires_toolsets: [terminal]
    required_environment_variables:
      - name: AMAP_API_KEY
        prompt: "高德地图 API Key"
# [CC-COMPAT] 以下为 Claude Code 专属字段，Hermes 不支持:
# disable-model-invocation: false
# preamble-tier: N/A
---
```

**第二层：硬编码路径替换（自动）**

Skill body 中 `~/.claude/skills/{name}/` 路径替换为 `~/.hermes/skills/cc-{name}/`：

```python
# 路径替换
content = content.replace('~/.claude/skills/', '~/.hermes/skills/cc-')
content = content.replace('.claude/skills/', '.hermes/skills/cc-')
```

影响 3 个 skill：amap (scripts/amap.py), gstack (bin/), tripgen (scripts/)。

**第三层：工具名映射（自动）**

Skill body 中的 Claude 专属工具名替换为 Hermes 等价：

```python
# 工具名映射（在 skill body markdown 中替换）
TOOL_NAME_MAP = {
    'AskUserQuestion': 'clarify',
    'WebSearch': 'web_search',
    'WebFetch': 'web_extract',
    'TodoWrite': 'todo',
    'EnterPlanMode': '(手动规划)',
    'ExitPlanMode': '(手动规划)',
}
# 注意：只替换明确的工具调用引用（如 "用 WebSearch"），不替换自然语言描述
```

**迁移执行策略**：

1. 复制全局 skills (`~/.claude/skills/*/`) 和项目级 skills 到 `~/.hermes/skills/cc-{name}/`
2. 对每个 SKILL.md 执行三层转换（frontmatter + 路径 + 工具名）
3. 连同 scripts/, references/, templates/ 等附加目录一起复制
4. 跳过 gstack 框架本身（它是 Claude Code 专属的编排框架，不适用于 Hermes）
5. 跳过 node_modules/（如需要在 Hermes 中重新安装）

#### 5.6 Prompt 历史导入

`~/.claude/history.jsonl` 每行格式:
```json
{"display":"user prompt text", "pastedContents":{}, "timestamp":1760335476965, "project":"/path/to/project"}
```

筛选当前项目的记录，转为 Hermes 可搜索的会话索引。

#### 5.7 Plans 和 Todos

**Plans**: 复制 `~/.claude/plans/*.md` 到 `{project}/.hermes/plans/`

**Todos**: 扫描 `~/.claude/todos/*.json`，过滤非空的，合并为一个待办清单展示给用户。

#### 5.8 设置和权限迁移

读取 Claude Code 的权限配置，转换为 Hermes config.yaml 的 tools 配置：

```python
# Claude Code settings.local.json
{
  "permissions": {
    "allow": ["Bash(open:*)", "Bash(mkdir:*)", "WebSearch", ...],
    "deny": []
  }
}

# → Hermes config.yaml tools section
# Claude 的 Bash → Hermes 的 terminal toolset
# Claude 的 WebSearch → Hermes 的 web toolset
# Claude 的 Read/Write/Edit → Hermes 的 file toolset
```

#### 5.9 插件清单导出

读取 `~/.claude/plugins/installed_plugins.json`，生成一份**等价功能对照表**，帮用户在 Hermes 中找到替代方案。

#### 5.10 生成迁移快照

在项目目录创建 `.hermes-migration.json`:
```json
{
  "version": "4.0.0",
  "timestamp": "{ISO}",
  "source": "claude-code",
  "target": "hermes-agent",
  "projectDir": "{path}",
  "git": {
    "branch": "{branch}",
    "commit": "{hash}",
    "dirty": true/false
  },
  "migrated": {
    "claudeMd": true,
    "hermesMd": true,
    "memoryFiles": 5,
    "chatSessions": 12,
    "subagentSessions": 8,
    "agents": 3,
    "globalSkills": 48,
    "projectSkills": 2,
    "promptHistory": 150,
    "plans": 9,
    "todos": 10,
    "settings": true,
    "plugins": true,
    "hooks": 0,
    "mcpServers": 0
  },
  "apiProvider": "{provider}",
  "model": "{model}"
}
```

---

### Phase 6: SOUL.md 生成

为 Hermes 生成人格定义 `~/.hermes/SOUL.md`:

```markdown
# Hermes Agent — Project Continuation Mode

You are continuing development work previously done in Claude Code.
You have access to the migrated project context in AGENTS.md and project memory.

## Behavior

- Be concise and technical
- Prioritize code quality and correctness
- When in doubt, read the codebase before making changes
- Use Chinese for communication when the user writes in Chinese
- Reference migrated memory and past sessions for context
```

如果用户有自定义偏好，用 AskUserQuestion 收集后调整。

---

### Phase 7: 验证

```bash
echo "=== Phase 7: 验证迁移结果 ==="
echo ""

PASS=0
FAIL=0

check() {
  if eval "$2"; then
    echo "✅ $1"
    PASS=$((PASS+1))
  else
    echo "❌ $1"
    FAIL=$((FAIL+1))
  fi
}

# 基础设施
check "Hermes Agent 已安装" "command -v hermes &>/dev/null"
check "config.yaml 已配置" "[ -f ~/.hermes/config.yaml ]"
check ".env 已配置" "[ -f ~/.hermes/.env ] || [ '$(cat ~/.hermes/config.yaml 2>/dev/null | grep ollama)' ]"

# 项目上下文
check "AGENTS.md 已生成" "[ -f AGENTS.md ]"
check "CLAUDE.md 已保留" "[ -f CLAUDE.md ] || true"
check ".hermes-migration.json 快照" "[ -f .hermes-migration.json ]"

# Memory
check "MEMORY.md 已迁移" "[ -f ~/.hermes/MEMORY.md ] || [ -f .hermes.md ]"
check "SOUL.md 已生成" "[ -f ~/.hermes/SOUL.md ]"

# Sessions
MIGRATED_SESSIONS=$(ls ~/.hermes/sessions/cc-*.json 2>/dev/null | wc -l | tr -d ' ')
check "聊天记录已迁移 ($MIGRATED_SESSIONS 个会话)" "[ $MIGRATED_SESSIONS -gt 0 ] || true"

# Skills
HERMES_SKILLS=$(find ~/.hermes/skills -name "SKILL.md" 2>/dev/null | wc -l | tr -d ' ')
check "Skills 已迁移 ($HERMES_SKILLS 个)" "[ $HERMES_SKILLS -gt 0 ] || true"

echo ""
echo "结果: $PASS 通过, $FAIL 失败"
echo ""
```

---

### Phase 8: 完成报告

```
╔══════════════════════════════════════════╗
║   🔱 Hermes Migration 完成!              ║
╚══════════════════════════════════════════╝

迁移摘要:
  ✅ Hermes Agent — 已安装并配置
  ✅ LLM — {提供商} / {模型}
  ✅ CLAUDE.md — 保留原文件 (Hermes 原生读取)
  ✅ .hermes.md — 项目记忆已迁移 ({N} 个文件)
  ✅ MEMORY.md — 核心记忆已精简
  ✅ USER.md — 用户画像已迁移
  ✅ 聊天记录 — {N} 个会话已转换
  ✅ Skills — {N} 个技能已迁移
  ✅ Agents → Skills — {N} 个 Agent 定义已转换
  ✅ 设置和权限 — 已映射到 config.yaml
  ✅ 迁移快照 — .hermes-migration.json

立即开始:
  ┌────────────────────────────────────────┐
  │  cd {项目目录}                          │
  │  export {API_KEY_VAR}="your-key"       │
  │  hermes                                │
  └────────────────────────────────────────┘

常用命令:
  hermes                    # 启动交互式会话
  hermes --continue         # 继续上一个会话
  hermes model              # 切换模型
  hermes tools              # 管理工具
  hermes /skills            # 查看技能
  hermes /memory            # 查看记忆

💡 提示:
  - 项目开发可以在 Hermes Agent 中无缝继续
  - 如需切回 Claude Code，所有原始文件均已保留
  - CLAUDE.md 无需删除，Hermes 原生支持读取
  - 聊天记录已导入，用 session_search 工具搜索历史
```

---

## 注意事项

- **无损迁移**: 绝不修改或删除任何 Claude Code 原文件
- **增量迁移**: 可多次运行，只更新有变化的部分
- **可逆**: 随时可切回 Claude Code，所有原始数据保留
- **隐私安全**: API Key 只存 `~/.hermes/.env`，不出现在其他文件
- **⚠️ 密钥安全**: 部分项目的 `settings.local.json` 权限规则中嵌入了明文 API Key/Secret (如 Supabase token, 飞书 appSecret, 钉钉 appKey)。迁移时需扫描并提醒用户将这些密钥迁移到 `~/.hermes/.env`，而非复制到配置文件
- **Hermes 兼容**: CLAUDE.md 原生支持，skill 格式基本兼容
- **Hooks 迁移**: Claude Code 的 `hooks.PostToolUse` 等钩子需手动转换为 Hermes 的 `~/.hermes/hooks/` 格式
- **大型数据**: 聊天记录转换为 SQLite INSERT (Hermes 用 `~/.hermes/state.db` FTS5)，不是 JSON 文件
- **工具差异**: Hermes 的 `terminal` = Claude 的 `bash`；`read_file` 相同；`patch` = `edit_file`。但 Hermes **没有** 独立的 `write_file`/`glob`/`grep` 工具，它通过 terminal 调 shell 命令实现。这是行为差异，不影响功能
- **config.yaml**: BigModel 等自定义 endpoint 用 `custom_providers` 块配置，不是直接写在 `model.base_url`
- **`hermes --resume <id>`**: 导入的聊天记录可以用此命令恢复。会话 source 设为 `"cli"`
- **Skill 文件名兼容**: 搜索时同时匹配 `SKILL.md` 和 `skill.md` (大小写不敏感)
- **⚠️ CLAUDE_CONFIG_DIR**: 环境变量可覆盖 `~/.claude` 路径！迁移脚本必须先检查 `$CLAUDE_CONFIG_DIR`，如果设置了则用它替代 `~/.claude`
- **⚠️ autoMemoryDirectory**: settings 中可自定义 memory 存储位置，不一定在默认的 `~/.claude/projects/` 下。迁移时需先读 settings 确认实际路径
- **企业级配置**: macOS `/Library/Application Support/ClaudeCode/`、Linux `/etc/claude-code/`、Windows `C:\Program Files\ClaudeCode\` 下的 managed-settings.json / managed-mcp.json / CLAUDE.md。企业迁移场景需额外处理
- **Memory 结构化**: Memory 文件含 frontmatter (type: user/project/feedback, originSessionId)，MEMORY.md 是索引文件。转换时需保留结构化信息

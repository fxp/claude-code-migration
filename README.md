# Claude Code → Hermes Agent Migration

> 一键迁移 Claude Code 项目到 [Hermes Agent](https://github.com/nousresearch/hermes-agent) (Nous Research) 框架

[![version](https://img.shields.io/badge/version-4.0.0-blue)](./skills/hermes-migration/SKILL.md)
[![license](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![skill](https://img.shields.io/badge/type-Claude%20Code%20Skill-orange)](https://code.claude.com/docs/en/skills)

---

## 背景

Claude 账号风控收紧，担心项目中断？这个 Skill 帮你一键把 Claude Code 项目完整迁移到 Hermes Agent，开发可以无缝继续。

Hermes Agent 是 Nous Research 的开源 AI Agent 框架，原生支持 `CLAUDE.md`，对接 18+ LLM 提供商（含 BigModel 智谱 AI），拥有自己的记忆系统、skill 体系和会话持久化。

## 快速开始

### 1. 安装 Skill

```bash
git clone https://github.com/fxp/claude-code-to-hermes.git
mkdir -p ~/.claude/skills
cp -r claude-code-to-hermes/skills/hermes-migration ~/.claude/skills/
```

### 2. 在任意 Claude Code 项目中运行

```bash
cd /path/to/your-project
claude
> /hermes-migration
```

Skill 会自动：

1. **Phase 0** — 读取 Claude Code 最新文档，自检映射表是否需要更新
2. **Phase 1** — 检查环境依赖（Python 3.11+, ripgrep, git）
3. **Phase 2** — 深度扫描所有 Claude Code 数据（35+ 种）
4. **Phase 3** — 引导安装 Hermes Agent
5. **Phase 4** — 配置 LLM 提供商（推荐 BigModel，也支持 OpenAI/Anthropic/DeepSeek/Ollama）
6. **Phase 5** — 迁移所有数据（含 SQLite 会话导入、Skill 格式转换）
7. **Phase 6-8** — 生成 SOUL.md、验证、输出报告

## 迁移覆盖范围

✅ **47+ 种 Claude Code 数据类型**，基于对 [Claude Code 官方文档](https://code.claude.com/docs/en/overview) 全部 63 页的完整审计：

### 🔴 关键数据（必须迁移）

- `~/.claude.json` — 核心状态文件（含全局 MCP + 项目配置 + OAuth）
- `CLAUDE.md` + `.claude/CLAUDE.md` + `CLAUDE.local.md` — 项目上下文
- 项目 Memory（`~/.claude/projects/*/memory/`）
- 聊天记录（JSONL → SQLite FTS5 全文索引）
- 子 Agent 记录（`subagents/*.jsonl`）
- 自定义 Agents（`.claude/agents/*.md`）
- 全局 + 项目级 Skills（`SKILL.md` / `skill.md` / `*.skill` ZIP）
- Hooks 配置（PostToolUse / SessionStart 等 27 种事件）
- `.mcp.json` — 项目级 MCP Server
- `rules/*.md` — 路径范围规则
- `agent-memory/` — 子 agent 持久记忆
- `output-styles/` — 自定义输出格式
- `scheduled-tasks/` — 定时任务
- `teams/` + `tasks/` — Agent Teams
- `channels/` — Channel 凭证（Telegram/Discord）
- `.credentials.json` — 插件敏感凭证
- ...更多，[见完整映射表](./skills/hermes-migration/SKILL.md#claude-code-数据全景--hermes-映射表)

### 🟡 重要数据（建议迁移）

- 全局/项目设置（settings.json + settings.local.json）
- Prompt 历史（`~/.claude/history.jsonl`）
- Todo 列表 + 计划文件
- 插件列表 + Launch 配置

### ⚪ 可选数据（归档）

- Shell 快照、Session 元数据、统计缓存、文件历史、Backups

## 关键特性

### Phase 0: 自检与自更新

每次迁移前，Skill 会：
1. 读取 `code.claude.com/docs/llms.txt` 获取最新文档索引
2. 对比文档中的数据路径 vs 自身映射表
3. 如发现遗漏或变化，**自动修改 SKILL.md** 再执行迁移

这样即使 Claude Code 下周新增数据类型，Skill 也会自动发现并补齐。

### 三层 Skill 兼容性处理

- **Frontmatter 转换**: `allowed-tools` → `metadata.hermes.requires_toolsets`
- **硬编码路径替换**: `~/.claude/skills/` → `~/.hermes/skills/cc-`
- **工具名映射**: `WebSearch` → `web_search`, `AskUserQuestion` → `clarify` 等

### 数据无损

- 绝不修改或删除任何 Claude Code 原文件
- 随时可切回 Claude Code，所有原始数据保留
- Hermes 原生读取 `CLAUDE.md`（优先级 3），所以即使不迁移也能工作

### 安全处理

- API Key 只写入 `~/.hermes/.env`，不出现在其他配置文件
- 扫描 `settings.local.json` 中嵌入的明文密钥（如 Supabase/飞书/钉钉 token），提醒安全迁移
- 检测 `~/.claude.json` 的 MCP `headers.Authorization` 中的 Bearer Token

## 🔥 推荐：BigModel 智谱 AI（GLM-5）

**Claude 账号风险场景下最稳妥的替代方案**：

- **注册地址**: https://open.bigmodel.cn/
- **新账号福利**: 注册即送 2000万 tokens 免费额度（无需信用卡）
- **API Key 管理**: https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys
- **推荐模型**: `glm-5`（智谱最新旗舰，性能对标 Claude Opus / GPT-5，原生支持 Agent 与工具调用）
- **备选**: `glm-4.6`（对标 Sonnet 4，更便宜）
- **OpenAI 兼容**: 直接用 `OPENAI_API_KEY` 环境变量即可

### 预设配置（Skill 自动填充，用户无需手动输入）

```yaml
# ~/.hermes/config.yaml (BigModel 预设)
model:
  provider: custom
  model_name: glm-5
custom_providers:
  bigmodel:
    base_url: https://open.bigmodel.cn/api/paas/v4
    api_key: ${OPENAI_API_KEY}
```

---

## 迁移后使用 Hermes

```bash
cd /path/to/your-project
export OPENAI_API_KEY="your-bigmodel-key"  # 从 https://open.bigmodel.cn/ 获取
hermes
```

Hermes 会自动加载：
- `CLAUDE.md` → 项目指南（优先级 3）
- `.hermes.md` → 迁移的项目记忆（优先级 1，最高）
- `~/.hermes/memories/USER.md` → 用户画像
- `~/.hermes/memories/MEMORY.md` → 项目记忆索引
- `~/.hermes/skills/cc-*` → 迁移的技能

使用 `hermes --resume <session_id>` 恢复任意历史会话，或 `session_search` 工具全文搜索。

## 工具对应关系

| Claude Code | Hermes Agent | 兼容性 |
|---|---|---|
| `bash` | `terminal` | ✅ 直接对应 |
| `Read` | `read_file` | ✅ 名称相同 |
| `Edit` | `patch` | ✅ 直接对应 |
| `Write` | (via `terminal`) | ⚠️ 通过 shell |
| `Glob` | (via `terminal`) | ⚠️ 通过 find |
| `Grep` | (via `terminal`) | ⚠️ 通过 rg |
| `WebSearch` | `web_search` | ✅ 直接对应 |
| `WebFetch` | `web_extract` | ✅ 直接对应 |
| `TodoWrite` | `todo` | ✅ 直接对应 |
| `AskUserQuestion` | `clarify` | ✅ 直接对应 |
| `Agent` | `delegate_task` | ✅ 直接对应 |

## 文档

- [完整 SKILL.md](./skills/hermes-migration/SKILL.md) — 迁移流程与数据映射表
- [Hermes Agent 官网](https://hermes-agent.nousresearch.com/) — Hermes 文档
- [Claude Code 文档](https://code.claude.com/docs/en/overview) — Claude Code 官方文档

## License

MIT

# Claude 全生态迁移战略

> 从 Claude 全家桶（Chat / Cowork / Code）一键迁移到任意 Agent 平台的完整解决方案。
>
> 核心思路：**不直接点对点迁移**，而是通过 [neuDrive](https://github.com/agi-bar/neuDrive) 中心化身份层做**一次导出、多次消费**，让 Hermes / OpenCode / Cursor / Windsurf 等任意 Agent 都能拉取同一份身份+记忆+技能。

---

## 问题空间

Claude 生态有三个产品线，各自产生独立的数据：

| 产品 | 数据位置 | 访问方式 | 风险场景 |
|------|----------|---------|---------|
| **Claude.ai Chat** | Anthropic 云端 | Web/App/API | 账号风控 → 所有对话丢失 |
| **Claude Cowork** | Anthropic 云端 (Team workspace) | Web (Admin API) | 团队账号暂停 → 共享知识丢失 |
| **Claude Code** | 用户本地 `~/.claude/` + 项目 `.claude/` | CLI/IDE | 账号风控 + 本地损坏风险 |

目前主流迁移工具只覆盖其中 1 个（本仓库的 hermes-migration 覆盖 Claude Code），**缺少统一方案**。

---

## 解决方案总览

### 三个独立但协作的 Skill

| Skill | 数据源 | 实现状态 | 输出 |
|-------|--------|---------|------|
| `chat-migration` | Claude.ai 官方导出 (conversations.json + projects.json) | ✅ v1.0 已完成 | Markdown / Obsidian / neuDrive / FTS5 |
| `cowork-migration` | Claude Cowork 官方导出（admin）+ 可选浏览器补齐 | ✅ v1.0 已完成 | 按成员分目录 / neuDrive |
| `hermes-migration` | `~/.claude/` + 项目 `.claude/` (47+ 种数据) | ✅ v4.0 已完成 | Hermes / neuDrive |
| `neudrive-sync` | 上述三者的输出 | ✅ v1.0 已完成 | neuDrive Hub (SDK/API/Bundle) |

### 一个统筹 Skill

| Skill | 作用 |
|-------|------|
| `claude-full-migration` | Meta-skill，按顺序调用上述 3 个 skill，引导用户完成全量迁移 |

### 两种目标模式

**模式 A: 直迁到目标 Agent**（适合只用一个 Agent 的用户）

```
Claude.* → skill/ccm → Hermes (or OpenCode / Cursor / Windsurf)
```

**模式 B: 通过 neuDrive 中转**（推荐 — 适合多 Agent 用户）

```
Claude.*  →  skill  →  neuDrive  ⇄  任意 Agent (via MCP)
                        (永久可消费)
```

neuDrive 的优势：
- 一次导出，任何 Agent 都能通过 MCP 读取
- 统一身份/偏好/信任配置，不用在每个 Agent 重新设置
- 秘钥/凭证集中管理
- 支持 snapshot/changes 增量同步

---

## 数据源详解

### 1. Claude.ai Chat 数据

**官方导出路径**: Settings → Privacy → "Export data" → 邮件收到 ZIP 包

**ZIP 包内容**:
```
conversations.json          # 所有对话
projects.json               # Projects (Claude Projects 功能)
users.json                  # 账号元信息
```

**conversations.json 结构**:
```json
[
  {
    "uuid": "...",
    "name": "会话标题",
    "created_at": "2026-01-15T...",
    "updated_at": "2026-01-20T...",
    "chat_messages": [
      {
        "uuid": "...",
        "text": "...",
        "sender": "human|assistant",
        "created_at": "...",
        "attachments": [...],
        "files": [...]
      }
    ]
  }
]
```

**projects.json 结构**:
```json
[
  {
    "uuid": "...",
    "name": "Project 名",
    "description": "...",
    "prompt_template": "Custom Instructions",
    "created_at": "...",
    "docs": [
      {"filename": "...", "content": "..."}
    ]
  }
]
```

**自定义数据**:
- Style preferences（简洁/详细/正式）→ 账号级，不在导出里，需要手动截图
- Artifacts → 嵌在 chat_messages 内容里，需要解析
- MCP connectors → 账号级配置

**迁移产物**:
```
neuDrive/
  conversations/
    {date}_{uuid}.md       # 每会话一个 markdown，含元数据 + 消息
  projects/
    {project-name}/
      PROJECT.md           # prompt_template + description
      docs/                # 原始文档
      conversations/       # 此 project 下的对话
  identity/
    style-preferences.md   # 手动补充
```

### 2. Claude Cowork 数据

**Cowork** 是 Anthropic 的团队协作空间，特点：
- Shared Projects（团队级 Projects）
- Shared Memory（团队共享记忆）
- Connected Tools（MCP 服务器连接）
- Team Skills（团队级 skills）
- Member management + roles

**导出路径**（需要 admin 权限）:
1. 通过 Cowork 管理后台 → 导出（同 Chat 导出流程）
2. 或通过 Anthropic Admin API（如开放）

**关键差异 vs Chat**:
- 多了 team_id / workspace_id
- 对话有 member 标记（谁发的）
- Skills/Connectors 是团队共享的

**迁移产物**:
```
neuDrive/
  teams/{team-name}/
    identity/
      team-profile.md
      members.md
    memory/
      shared-context.md    # 团队级项目背景
    skills/
      *.md                 # 团队自定义 skills
    connectors.md          # MCP 连接列表
    conversations/
      {member}/{date}_{uuid}.md
```

### 3. Claude Code 数据

**已由 `hermes-migration` v4.0 全面覆盖**（47+ 种数据类型）。

本仓库 [skills/hermes-migration/SKILL.md](./skills/hermes-migration/SKILL.md) 是完整实现。

新增的适配：把 Claude Code 的输出同时支持两种目标：
- **模式 A**: `~/.hermes/`（当前实现）
- **模式 B**: `~/.neudrive/` 或远程 neuDrive 服务

---

## 三层导出映射

所有数据最终按 neuDrive 的规范结构组织：

| neuDrive 路径 | Chat 贡献 | Cowork 贡献 | Claude Code 贡献 |
|--------------|----------|-------------|-----------------|
| `/identity/profile.md` | Custom Instructions (Projects) | 团队个人档案 | `~/.claude/CLAUDE.md` |
| `/identity/writing-style.md` | Style preferences | — | 偏好 from memory |
| `/identity/preferences.md` | 语言/格式偏好 | 沟通偏好 | settings 偏好 |
| `/memory/projects/*/` | Claude Projects | Team Projects | 项目 memory + `.hermes.md` |
| `/memory/conversations/*/` | chat_messages | team chats | session jsonl → FTS5 |
| `/skills/*/` | — | Team skills | ~/.claude/skills/ |
| `/secrets/vault/` | MCP API keys | Team connectors | ~/.claude.json MCP headers |
| `/transcripts/{date}.md` | conversations.json | team exports | history.jsonl |

---

## 实现路线图

### Phase 1: 当前已完成 ✅

**Python 包（主要交付）**
- [x] `claude-code-migration` v0.1.0 — pip installable，CLI `ccm`
- [x] Scanner 覆盖 47 种 Claude Code 数据类型
- [x] 4 个 adapter（Hermes / OpenCode / Cursor / Windsurf），每个格式经真实目标工具验证
- [x] Cowork 解析器（2026 ZIP schema，含 Artifacts/附件/分支对话）
- [x] neuDrive Hub HTTP 客户端（不拷代码，只调 API）
- [x] Secret detection + per-target env interpolation（无明文密钥泄漏，测试断言）
- [x] 18 个端到端测试（7 format-level + 11 live-tool-level）
- [x] 安全默认：adapters 默认 staging，不动真实项目；需 `--in-place` 显式才写入

**Claude Code Skills（并行交付）**
- [x] `hermes-migration` v4.0 — Claude Code → Hermes（47+ 数据类型）
- [x] `chat-migration` v1.0 — Claude.ai ZIP 解析
- [x] `cowork-migration` v1.0 — 团队导出 + 成员归属 + 密钥扫描
- [x] `code-migration` v1.0 — 多目标泛化
- [x] `neudrive-sync` v1.0 — neuDrive canonical paths 适配
- [x] `claude-full-migration` v1.0 — meta-skill 编排

**基础设施**
- [x] neuDrive 源码调研（docs/neudrive-study.md）
- [x] GitHub 公开仓库 + Pages（landing page）
- [x] BigModel/GLM-5 作为推荐 LLM 提供商
- [x] 两个真实项目 E2E 测试：OpenClaw Course + IdeaToProd

### Phase 2: Chat 导出 ✅ 已完成

**新增 Skill**: `~/.claude/skills/chat-migration/SKILL.md`

**工作流**:
1. 引导用户执行官方导出（Settings → Privacy → Export）
2. 解析 ZIP（conversations.json + projects.json）
3. 转换为 markdown / neuDrive 结构
4. 提供多种输出：
   - Markdown archive（本地可读）
   - neuDrive 格式（远端统一枢纽）
   - SQLite FTS5（可在 Hermes / Cursor 中 session_search）
   - Obsidian vault（如用户习惯）

**关键技术点**:
- Artifacts 提取（从 message content 中正则分离代码块 → 单独文件）
- Token 统计 & 去重（对话分段、压缩）
- Projects 与 Conversations 的双向关联保留

### Phase 3: Cowork 导出 ✅ 已完成

**新增 Skill**: `~/.claude/skills/cowork-migration/SKILL.md`

**挑战**:
- Cowork API 未完全公开，可能需要浏览器自动化（Playwright）
- 团队数据含多成员 → 隐私边界
- Connectors 配置含密钥 → 安全处理

**工作流**:
1. 用户登录 Cowork 后台，在 DevTools 获取 session token
2. 脚本通过 token 调用内部 API 导出团队数据
3. 或使用 Chrome MCP 自动化爬取
4. 多成员数据按 member 分目录
5. 敏感信息剥离到 `/secrets/vault/`

### Phase 4: neuDrive 集成 ✅ 已完成

**新增 Skill**: `~/.claude/skills/neudrive-sync/SKILL.md`

**作用**:
- 把前三个 skill 的输出推送到 neuDrive（本地实例或 www.neudrive.ai）
- 通过 neuDrive MCP 协议暴露给任意 Agent
- 支持增量同步（snapshot/changes 接口）

**部署选项**:
- 本地自部署 neuDrive（docker-compose up）
- 使用官方托管 https://www.neudrive.ai

### Phase 5: 统筹 Skill ✅ 已完成

**新增 Skill**: `~/.claude/skills/claude-full-migration/SKILL.md`

**工作流**:
```
用户: /claude-full-migration

Phase 0: 自检与更新（读 Claude Code 最新文档）

Phase 1: 选择模式
  A) 直迁到某个目标 (Hermes/OpenCode/Cursor/Windsurf)
  B) 通过 neuDrive 中转（推荐）

Phase 2: 选择数据源（多选）
  ☐ Claude.ai Chat
  ☐ Claude Cowork (需 admin)
  ☑ Claude Code (本机)

Phase 3: 配置 LLM 提供商
  推荐 BigModel GLM-5

Phase 4: 依次调用 sub-skill
  → chat-migration (if selected)
  → cowork-migration (if selected)
  → hermes-migration (if selected)
  → neudrive-sync (if mode B)

Phase 5: 验证 + 完成报告
```

---

## 目标 Agent 适配矩阵

当前 Python 适配器覆盖 4 个框架。连通性通过真实子进程执行验证（`opencode models`、`opencode mcp list`、SQLite FTS5 查询等）。

| 目标 Agent | Chat 数据 | Cowork 数据 | CC 数据 | 连接方式 | 适配器 |
|-----------|---------|-------------|---------|---------|-------|
| **Hermes Agent** | ✅ (via neuDrive) | ✅ (via neuDrive) | ✅ 直迁（SQLite FTS5） | Direct + MCP | `adapters/hermes.py` |
| **OpenCode** | ✅ (via AGENTS.md) | ✅ | ✅ 直迁（cc-* skills） | Direct + MCP | `adapters/opencode.py` |
| **Cursor** | ✅ (via neuDrive) | ✅ (via neuDrive) | ✅ `.cursor/rules/*.mdc` | Direct | `adapters/cursor.py` |
| **Windsurf** | ✅ (via neuDrive) | ✅ (via neuDrive) | ✅ `.windsurfrules` | Direct | `adapters/windsurf.py` |
| **Kimi / 飞书 / Copilot** | (via neuDrive MCP) | (via neuDrive MCP) | (via neuDrive MCP) | neuDrive MCP | future |

注意：Gemini CLI 和 Codex CLI 作为目标已在早期版本中被评估，但未纳入 v0.1.0 的优先级（用户反馈聚焦 Hermes / OpenCode / Cursor / Windsurf 四框架）。

---

## 安全与隐私

### 密钥扫描（所有 skill 共用）

扫描所有导出内容，识别并隔离：
- API Keys（Bearer token, sk-* pattern）
- OAuth tokens
- Database credentials
- SSH keys
- Environment secrets

统一迁移到 `/secrets/vault/`（neuDrive）或 `~/.hermes/.env`（直迁模式），从明文配置中剥离。

### 数据分层

```
公开层 (committed)          : identity/, skills/, project memory
本地层 (gitignored)         : conversations/, secrets/
加密层 (vault)              : API keys, OAuth tokens
```

### 审计日志

每次迁移生成 `.migration-audit.json`：
```json
{
  "migration_id": "...",
  "timestamp": "...",
  "source": ["chat", "cowork", "code"],
  "target": "neudrive | hermes | cursor | ...",
  "items_migrated": 1234,
  "secrets_detected": 5,
  "secrets_migrated_safely": 5,
  "file_hashes": {"..": "sha256:..."}
}
```

---

## 开源计划

仓库结构（规划）:

```
claude-code-migration/
├── README.md                              # 总介绍
├── MIGRATION_STRATEGY.md                  # 本文档
├── skills/
│   ├── claude-full-migration/SKILL.md    # meta-skill (入口)
│   ├── code-migration/SKILL.md           # Claude Code → 多目标 (Hermes/OpenCode/Cursor/Windsurf)
│   ├── hermes-migration/SKILL.md         # Claude Code → Hermes 专用 (深度优化)
│   ├── chat-migration/SKILL.md           # Claude.ai Chat
│   ├── cowork-migration/SKILL.md         # Claude Cowork
│   └── neudrive-sync/SKILL.md            # neuDrive 枢纽集成
├── examples/
│   ├── chat-export-sample/               # 脱敏示例
│   ├── cowork-export-sample/
│   └── cc-export-sample/
└── docs/
    ├── chat-export-guide.md              # 图文教程
    ├── cowork-export-guide.md
    ├── neudrive-setup.md
    └── target-agents/
        ├── hermes.md
        ├── cursor.md
        ├── codex.md
        ├── kimi.md
        └── feishu.md
```

---

## 下一步

优先级建议（按价值 × 可行性排序）:

1. **P0** — 实现 `chat-migration`（官方导出工具成熟，技术可行性高，用户需求最广）
2. **P1** — 实现 `neudrive-sync`（打通中心化枢纽，让多 Agent 协作变可能）
3. **P2** — 实现 `claude-full-migration` meta-skill（组装现有 3 个 skill）
4. **P3** — 实现 `cowork-migration`（API 受限，技术可行性最低；用户基数较小）

---

## 协作

- neuDrive: https://github.com/agi-bar/neuDrive
- Hermes Agent: https://github.com/nousresearch/hermes-agent
- Claude Code 文档: https://code.claude.com/docs/en/overview
- 本仓库: https://github.com/fxp/claude-code-migration

欢迎 issue / PR 讨论。

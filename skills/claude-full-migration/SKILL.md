---
name: claude-full-migration
version: 1.0.0
description: |
  Claude 全生态一键迁移入口 — 编排 chat-migration / cowork-migration / hermes-migration / neudrive-sync
  四个子 skill 完成从 Claude.ai Chat + Cowork + Claude Code 到任意目标 Agent 的完整迁移。
  当用户说"全量迁移"、"完整迁移"、"claude full migration"、"一键搬家"、"全家桶迁移"、"full migration"时触发。
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
  - Skill
---

# Claude Full Migration Skill v1.0

**Meta-skill**：把下面 4 个独立 skill 按正确顺序编排成一次完整的迁移，避免用户手工组合。

```
/claude-full-migration
   ├── /chat-migration       (可选)
   ├── /cowork-migration     (可选)
   ├── /hermes-migration     (可选)
   └── /neudrive-sync        (可选，作为汇总)
```

---

## 设计原则

1. **编排，不重复** — 本 skill 不重新实现解析/转换，全部 delegate 给 4 个子 skill
2. **按需跳过** — 用户只有 Chat 导出没有 Cowork/Code，就跳过后两者
3. **幂等** — 任意 phase 可重试；已完成的部分不会重复处理
4. **透明** — 每一步前都告诉用户"即将调用 /xxx-migration"，被拒绝可撤销
5. **审计** — 汇总所有子 skill 的输出到单一 `.full-migration-audit.json`

---

## 执行流程

### Phase 0: 元自检（比任何单个 skill 更彻底）

依次自检**所有 4 个子 skill**的前置数据：

1. `code.claude.com/docs/llms.txt` — Claude Code 文档（for hermes-migration）
2. `support.claude.com/en/articles/9450526-how-can-i-export-my-claude-data` — 官方导出流程（for chat-migration）
3. `github.com/agi-bar/neuDrive/blob/main/docs/sync.md` — neuDrive 同步协议（for neudrive-sync）
4. `github.com/osteele/claude-chat-viewer/blob/main/src/schemas/chat.ts` — Claude.ai 导出 schema（for chat-migration）

对每份文档抽取关键数据结构，对比本 skill 的子 skill 映射表。若任一子 skill 的映射过期：
- 先 delegate 给对应子 skill 的 Phase 0 让它自修复
- 再进入 Phase 1

---

### Phase 1: 统一环境检查

合并 4 个子 skill 的依赖到一张表：

```bash
echo "=== 全量迁移环境检查 ==="

# 通用依赖
for cmd in python3 unzip curl git; do
  command -v $cmd &>/dev/null && echo "✅ $cmd" || echo "❌ $cmd 缺失"
done

PY_VER=$(python3 --version 2>&1 | awk '{print $2}')
PY_MINOR=$(echo $PY_VER | cut -d. -f2)
[ "$PY_MINOR" -ge 11 ] && echo "✅ Python $PY_VER (>= 3.11)" || echo "⚠️ Python $PY_VER (hermes-migration 需要 >= 3.11)"

# 子 skill 是否就位
for skill in hermes-migration chat-migration cowork-migration neudrive-sync; do
  [ -f ~/.claude/skills/$skill/SKILL.md ] && echo "✅ /$skill 就位" || echo "❌ /$skill 未安装"
done

# 目标侧依赖
command -v hermes &>/dev/null && echo "✅ Hermes Agent" || echo "⚪ Hermes Agent 未装 (hermes-migration 会引导)"
command -v neu &>/dev/null && echo "✅ neu CLI" || echo "⚪ neu CLI 未装 (neudrive-sync 会引导)"
python3 -c "import neudrive" 2>/dev/null && echo "✅ neudrive Python SDK" || echo "⚪ neudrive SDK 未装"
```

---

### Phase 2: 数据源盘点

自动发现用户手上**有什么数据**：

```python
import os, glob

discovery = {
    'claude_code': False,    # ~/.claude/ 存在且有内容
    'chat_zip': None,        # 用户声明的导出 ZIP 路径
    'cowork_zip': None,      # 团队导出 ZIP
    'previous_migration': [] # 之前有没有跑过子 skill 的输出
}

# Claude Code
cc_home = os.path.expanduser('~/.claude')
if os.path.isdir(cc_home):
    projects = glob.glob(f"{cc_home}/projects/*")
    discovery['claude_code'] = {
        'home': cc_home,
        'projects': len(projects),
        'size': sum(os.path.getsize(f) for f in glob.glob(f"{cc_home}/**", recursive=True) if os.path.isfile(f)) // (1024*1024)
    }

# 历史迁移产物
for pattern in ['/tmp/chat-migration-*', '/tmp/cowork-migration-*', '/tmp/hermes-migration-*']:
    hits = glob.glob(pattern)
    if hits:
        discovery['previous_migration'].extend(hits)

print(f"发现:")
print(f"  Claude Code: {discovery['claude_code']}")
print(f"  之前迁移输出: {len(discovery['previous_migration'])} 个")
```

再用 AskUserQuestion 补充确认：
- Q1: 你是否已经从 claude.ai 导出了 ZIP？（是 + 路径 / 否，跳过 / 否，现在去导）
- Q2: 你是否是某个 Team workspace 的 admin？（是 + 想导出 / 否）
- Q3: 上次部分迁移过吗？（发现 `/tmp/*-migration-*` 时问是否续用）

---

### Phase 3: 目标选择

用 AskUserQuestion (multi-select) 让用户选目标：

| 目标 | 说明 | 需要的子 skill |
|------|------|--------------|
| **Hermes Agent** 直迁 | 本机跑的 Hermes，最省心 | hermes-migration |
| **本地 Markdown 归档** | 纯文本备份，Obsidian/文件系统可读 | chat-migration, cowork-migration |
| **neuDrive Hub** | 多 Agent 共享枢纽（推荐） | neudrive-sync + 任意源 |
| **SQLite FTS5** | 给 Hermes `--resume` 用 | chat-migration 或 hermes-migration |
| **Cursor / Codex / Kimi** | 通过 neuDrive MCP 间接接入 | neudrive-sync |

用户选完之后，本 skill 根据"源 × 目标"矩阵自动规划**执行清单**。

---

### Phase 4: 执行计划预览

向用户展示即将执行的 skill 调用顺序：

```
📋 执行计划预览

步骤 1: /chat-migration
  输入: ~/Downloads/claude-export-2026-04.zip
  输出: /tmp/chat-migration-<ts>/output/
  包含: Markdown, SQLite FTS5

步骤 2: /hermes-migration
  扫描: ~/.claude/  (60+ 种数据)
  输出: ~/.hermes/ (你选的 Hermes 直迁目标)

步骤 3: /neudrive-sync
  实例: https://www.neudrive.ai
  源: 上面两步的输出 + ~/.claude/ 直接扫
  模式: Hybrid (让 neuDrive 自扫 + 补差集)

步骤 4: 生成汇总审计 .full-migration-audit.json

预计耗时: 10-30 分钟（取决于聊天记录大小）
是否确认执行？(Y/N/修改)
```

如果用户选 N → 展示如何手工调单个 skill。
如果用户选"修改" → 回到 Phase 3 重选。

---

### Phase 5: 依次执行子 skill

调用 Skill tool 依次运行（每步失败可重试或跳过）：

**5.1 Chat** (if selected)
```
Skill: chat-migration
args: zip_path={path}, output_mode={markdown,fts5,...}
```
捕获输出路径 → 传给后续 skill。

**5.2 Cowork** (if selected)
```
Skill: cowork-migration
args: admin={bool}, zip_path={path}
```
特殊：如果用户不是 admin，skill 内部会降级为"个人导出"。

**5.3 Claude Code** (if selected)
```
Skill: hermes-migration
args: target={hermes_local|bundle_only}
```
注：如果用户选 neuDrive 而非直迁 Hermes，这里可以让 hermes-migration 跑到 Phase 5（数据转换）就停，不必真装 Hermes。

**5.4 neuDrive Sync** (if selected)
```
Skill: neudrive-sync
args:
  sources: [output_of_5.1, output_of_5.2, output_of_5.3]
  mode: hybrid
  instance: {hosted|local|custom}
```
neudrive-sync 接受"上面任意组合的输出"作为 sources，统一转 canonical paths。

**每步之间**：
- 失败不中断整个流程，记录到 audit 继续下一步
- 用户可 Ctrl+C 中断，再次运行本 skill 时从断点续跑

---

### Phase 6: 汇总审计

生成 `~/.claude-full-migration/audit-<timestamp>.json`:

```json
{
  "migration_id": "fm-2026-04-17-a3b2c1",
  "started_at": "2026-04-17T15:00:00Z",
  "completed_at": "2026-04-17T15:23:11Z",
  "duration_seconds": 1391,
  "sources_processed": {
    "chat": {
      "zip": "~/Downloads/claude-export.zip",
      "conversations": 142,
      "projects": 6,
      "artifacts": 38,
      "attachments_downloaded": 12,
      "attachments_expired": 1,
      "output_dir": "/tmp/chat-migration-.../output"
    },
    "cowork": null,
    "claude_code": {
      "data_types_migrated": 47,
      "sessions": 285,
      "subagent_sessions": 242,
      "skills": 48,
      "memory_files": 10
    }
  },
  "targets_populated": {
    "hermes_local": {
      "path": "~/.hermes/",
      "state_db_size_mb": 254
    },
    "neudrive": {
      "instance": "https://www.neudrive.ai",
      "profile_entries": 3,
      "projects": 12,
      "conversations": 427,
      "skills": 48,
      "vault_secrets": 7,
      "bundle_size_mb": 189
    }
  },
  "secrets_handling": {
    "detected_total": 9,
    "migrated_to_vault": 7,
    "user_skipped": 2
  },
  "sub_skill_audits": [
    "/tmp/chat-migration-.../.audit.json",
    "/tmp/hermes-migration-.../.migration-snapshot.json",
    "/tmp/neudrive-sync-.../.audit.json"
  ],
  "warnings": [
    "1 attachment URL expired, see failed_downloads.txt",
    "2 secrets left in plain text (user declined vault migration)"
  ],
  "next_steps": [
    "Access neuDrive: https://www.neudrive.ai",
    "Configure Cursor MCP: ~/.cursor/mcp.json",
    "Configure Codex MCP: ~/.codex/config.toml"
  ]
}
```

---

### Phase 7: 完成报告 + 多 Agent 接入指南

```
╔════════════════════════════════════════════════╗
║   🔱 Claude Full Migration 完成!              ║
╚════════════════════════════════════════════════╝

总览:
  ⏱️  耗时: {duration} 分钟
  📊 处理: {N_convs} 对话 + {N_projs} 项目 + {N_skills} Skills + {N_mem} 记忆
  🔐 安全: {N_secrets} 个密钥迁移到 Vault
  ⚠️  警告: {N_warnings} 项（见 audit.json）

现在你可以在任意 Agent 中接入同一份数据:

┌─── Hermes Agent ────────────────────────┐
│  cd /path/to/project                    │
│  export OPENAI_API_KEY=your-bigmodel-key│
│  hermes                                 │
│  > hermes --resume <session_id>         │
└─────────────────────────────────────────┘

┌─── Cursor ──────────────────────────────┐
│  # 编辑 ~/.cursor/mcp.json              │
│  {                                      │
│    "mcpServers": {                      │
│      "neudrive": {                      │
│        "type": "http",                  │
│        "url": "https://www.neudrive.ai/mcp",
│        "headers": {                     │
│          "Authorization": "Bearer ndt_xxx"
│        }                                │
│      }                                  │
│    }                                    │
│  }                                      │
└─────────────────────────────────────────┘

┌─── Codex CLI ───────────────────────────┐
│  neu connect codex                      │
└─────────────────────────────────────────┘

┌─── Kimi / 飞书 / Gemini ────────────────┐
│  参考 neuDrive 平台矩阵:                 │
│  https://github.com/agi-bar/neuDrive    │
└─────────────────────────────────────────┘

审计报告: ~/.claude-full-migration/audit-{id}.json
```

---

## 场景示例

### 场景 1: 账号可能被封，先做完整备份

```
User: 我担心账号被封，先把所有数据导出备份一份
Skill:
  Phase 2 发现: Claude Code 有 + ZIP 没有
  Phase 3 建议: "先去 claude.ai 导出 ZIP，完成后再跑本命令"
  引导用户执行官方 export 流程
  等用户拿到 ZIP 后回来
  执行: chat-migration → hermes-migration (stage only) → neudrive-sync
  输出: 本地 markdown 归档 + neuDrive 上云备份
```

### 场景 2: 只想换到 Hermes 继续开发，不要云端

```
User: 只迁 Claude Code 本地数据，用 Hermes 继续开发
Skill:
  Phase 3 用户只勾选"Hermes 直迁"
  Phase 4 预览: 只有一步 /hermes-migration
  Phase 5 完整调用（含 Hermes 安装 + 配置 + 数据迁移）
  跳过 chat/cowork/neudrive
```

### 场景 3: 团队 admin，全员迁到 neuDrive

```
User: 我是团队 admin，想把整个 team workspace 搬到 neuDrive
Skill:
  Phase 2 Q2 用户答"是 admin"
  Phase 3 用户选 "Cowork" + "neuDrive"
  Phase 5:
    1. cowork-migration (admin mode) → 按成员分目录
    2. neudrive-sync (每成员一个 scope token) → 推送
  Phase 7 生成每个成员的 MCP 配置片段，admin 分发给大家
```

---

## 注意事项

- **子 skill 版本兼容**: 本 meta-skill 依赖 `hermes-migration v4.0+`, `chat-migration v1.0+`, `cowork-migration v1.0+`, `neudrive-sync v1.0+`。启动时 Phase 1 会验证版本号
- **失败恢复**: 任一子 skill 失败，meta-skill 继续后面的 step；完成后汇总报告里标注失败项
- **断点续跑**: `~/.claude-full-migration/state.json` 保存进度；再次调用时询问"是否从断点继续"
- **数据隔离**: 各子 skill 的中间产物放在独立 `/tmp/<skill>-<timestamp>/`，互不干扰
- **Dry-run**: `/claude-full-migration --dry-run` 只到 Phase 4 展示计划，不实际执行
- **Token 安全**: neuDrive token / API key 绝不进入 audit.json，只记录 scope 名称
- **时区**: 所有 timestamp 用 UTC ISO 8601

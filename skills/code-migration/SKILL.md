---
name: code-migration
version: 1.0.0
description: |
  把 Claude Code 项目数据迁移到任意 Agent 平台 —— Hermes / Cursor / Codex / Windsurf / Gemini CLI / Copilot。
  从 hermes-migration 演化而来，扫描逻辑完全相同（60+ 种数据类型），但输出适配器可切换。
  当用户说"迁移到cursor"、"迁移到codex"、"code migration"、"multi-target migration"、"换到其他Agent"时触发。
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

# Code Migration Skill v1.0

**多目标**迁移 — 把 Claude Code 本地数据（`~/.claude/` + 项目 `.claude/`）适配到任意 Agent 平台。

本 skill 是 `hermes-migration` v4.0 的泛化版本。扫描阶段完全沿用（60+ 种数据类型），输出阶段替换为目标侧的**适配器**：

```
        Claude Code Scanner (60+ types)
                  │
                  ▼
       ┌──────────┼──────────┬──────────┬───────────┬─────────┐
       ▼          ▼          ▼          ▼           ▼         ▼
    Hermes    Cursor      Codex    Windsurf    Gemini     Copilot
  (原生+SQLite) (.mdc)    (TOML)   (JSON)      (GEMINI.md) (.github/)
```

---

## 支持的目标矩阵

| 目标 | 项目上下文 | MCP 配置 | 记忆/规则 | Skills | 会话 | 推荐模型 |
|-----|-----------|---------|----------|--------|-----|---------|
| **Hermes Agent** | CLAUDE.md (原生) + .hermes.md | `config.yaml` custom_providers | `~/.hermes/memories/` | `~/.hermes/skills/cc-*` | SQLite FTS5 `state.db` | glm-5 |
| **Cursor** | AGENTS.md + `.cursor/rules/*.mdc` | `~/.cursor/mcp.json` / `.cursor/mcp.json` | User Rules (Settings) + project rules | 无（折叠进 rules） | 无（归档为 markdown） | claude-sonnet / glm-5 |
| **Codex CLI** | AGENTS.md + `~/.codex/AGENTS.md` | `~/.codex/config.toml [mcp_servers.*]` | AGENTS.md sections | 无（折叠） | SQLite 内部 | gpt-5.4 / kimi-k2.5 |
| **Windsurf** | `.windsurfrules` + `.windsurf/rules/*.md` | `~/.codeium/windsurf/mcp_config.json` | `~/.codeium/windsurf/memories/global_rules.md` | 无 | 无 | claude-sonnet |
| **Gemini CLI** | `GEMINI.md` / `AGENT.md` + `~/.gemini/GEMINI.md` | `~/.gemini/settings.json mcpServers` | GEMINI.md 层级 | 无 | general.checkpointing | gemini-2.5-pro |
| **GitHub Copilot** | `.github/copilot-instructions.md` + `.github/instructions/*.md` | `.vscode/mcp.json servers` | instructions + prompts | `.github/prompts/*.prompt.md` | 无 | (VS Code 自选) |
| **Universal (AGENTS.md)** | `AGENTS.md` 作为通用 fallback | (各目标原生) | - | - | - | - |

**Kimi K2 / Moonshot**: 不是独立目标，而是**任何 OpenAI-compatible 目标**（Hermes/Codex）的一个 provider 选项。

---

## 关键观察：AGENTS.md 是通用语

`AGENTS.md` 被 Cursor / Codex / Gemini（以 `AGENT.md` 别名）等**多个目标原生识别**。写一份好的 AGENTS.md 就能覆盖 4 个目标，无需格式转换。

本 skill 的默认策略是 **"AGENTS.md 优先 + 目标专属增强"**：
1. 总是生成一份高质量 AGENTS.md（从 CLAUDE.md + memory 合并）
2. 再针对每个目标做专属格式转换（MCP 配置、memory 路径等）

---

## 执行流程

### Phase 0: 自检与自更新

每次迁移前必须执行：

1. **拉 Claude Code 最新文档** — 确认源端 60+ 种数据类型是否有变化
   - `WebFetch https://code.claude.com/docs/llms.txt`
   - 对比本 skill 的扫描清单，有变化则用 Edit 修改

2. **拉所有目标端的最新文档** — 确认目标配置格式是否有变化
   - Hermes: `https://github.com/nousresearch/hermes-agent/blob/main/docs/` (sync/skills/memory)
   - Cursor: `https://cursor.com/docs/context/rules` + `cursor.com/docs/context/mcp`
   - Codex: `https://developers.openai.com/codex/config-reference`
   - Windsurf: `https://docs.windsurf.com/windsurf/cascade/mcp`
   - Gemini: `https://geminicli.com/docs/reference/configuration/`
   - Copilot: `https://docs.github.com/en/copilot/customizing-copilot/`

3. **对比本 skill 的适配器映射表**，有变化则修改

---

### Phase 1: 环境检查（通用）

```bash
echo "=== 通用环境检查 ==="
for cmd in python3 unzip git; do
  command -v $cmd &>/dev/null && echo "✅ $cmd" || echo "❌ $cmd 缺失"
done

# CLAUDE_CONFIG_DIR 支持
CLAUDE_HOME="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
[ -d "$CLAUDE_HOME" ] && echo "✅ $CLAUDE_HOME" || echo "❌ 找不到 Claude 数据目录"
```

---

### Phase 2: 扫描 Claude Code 数据（60+ 种）

**完全沿用** `hermes-migration` v4.0 的扫描逻辑。此处不重复，直接 delegate：

```
Skill: hermes-migration
args: mode=scan-only  # 只扫描不迁移，输出 /tmp/cc-scan-<ts>/scan.json
```

scan.json 结构：
```json
{
  "timestamp": "2026-04-17T...",
  "claude_home": "~/.claude",
  "claude_json": { "mcpServers": {...}, "projects": {...}, "oauthAccount": {...} },
  "project_dir": "/path/to/project",
  "claude_md": "content...",
  "home_claude_md": "content...",
  "memory": [{file, content, type, ...}],
  "sessions": [{uuid, path, message_count, ...}],
  "agents": [{name, description, model, instructions, ...}],
  "skills": {
    "global": [{name, path, body, frontmatter, extras}],
    "project": [...]
  },
  "settings": { "local": {...}, "project": {...}, "hooks": {...} },
  "mcp_json": {...},
  "rules": [...],
  "output_styles": [...],
  "loop_md": "...",
  "secrets_detected": [...],
  "plans": [...],
  "todos": [...],
  "scheduled_tasks": [...],
  "agent_memory": [...],
  "channels": [...],
  "credentials": [...]
}
```

---

### Phase 3: 目标选择

用 AskUserQuestion (multi-select):

- **Hermes Agent** — 如有本机 Hermes，最完整的迁移
- **Cursor** — 前端开发首选
- **Codex CLI** — OpenAI 体系
- **Windsurf** — Codeium 用户
- **Gemini CLI** — Google 生态
- **GitHub Copilot** — VS Code + 企业订阅
- **AGENTS.md only** — 生成通用文件，手动放到任意支持的 Agent

允许多选。后面按每个目标执行对应 Phase 5.X。

还需要一个**项目上下文选择**：
- A. 当前项目（`pwd`）— 默认
- B. 指定路径
- C. 所有 `~/.claude/projects/` 下的项目（批量）

---

### Phase 4: 通用预处理

无论选哪个目标，以下产物**都会生成**：

#### 4.1 AGENTS.md（通用上下文）

```python
# 输入：scan.json 的 claude_md + memory + rules
# 输出：{project}/AGENTS.md

parts = []
if scan['claude_md']:
    parts.append(scan['claude_md'])

# 合并 project-level memory (type=project + feedback)
for m in scan['memory']:
    if m.get('type') in ('project', 'feedback'):
        parts.append(f"## {m['file']}\n\n{m['content']}")

# rules
for r in scan['rules']:
    parts.append(f"## Rule: {r['name']}\n\n{r['content']}")

agents_md = '\n\n---\n\n'.join(parts)
# 头部加迁移标注
header = f"""# Project Agent Instructions

> 🔱 Migrated from Claude Code on {ts}
> Generated by code-migration skill for multi-agent compatibility
> Read by: Claude Code, Hermes (priority 3), Cursor, Codex, Gemini CLI
"""
write(f"{project}/AGENTS.md", header + '\n\n' + agents_md)
```

#### 4.2 密钥提取

扫描 `scan['secrets_detected']` + `claude_json.mcpServers.headers.Authorization` + `settings.local.json.permissions.allow` 中嵌入的密钥，去重后输出：

```
{project}/.migration/secrets.json    # 仅含 scope 名称和 SHA256，不含明文
{project}/.migration/secrets.env     # 明文环境变量格式，0600 权限，gitignored
```

每个目标的 Phase 5 会根据自己的密钥注入方式使用 `secrets.env`。

#### 4.3 迁移审计

创建 `{project}/.migration/audit.json`，记录每个目标的迁移状态。

---

### Phase 5: 按目标执行适配

#### 5.A Hermes Agent（原 hermes-migration 的 Phase 4-7）

完全调用 `hermes-migration` v4.0 的 Phase 4-7（安装 + LLM 配置 + 数据迁移 + 验证）：

```
Skill: hermes-migration
args: mode=convert-only, scan=<scan.json path>, skip-install=false
```

产物：`~/.hermes/` 完整填充 + `~/.hermes/state.db` SQLite FTS5 + `{project}/.hermes.md`

#### 5.B Cursor

**5.B.1 规则文件生成**

```python
# 把 scan 的各类数据转为 .cursor/rules/*.mdc
import os
rules_dir = f"{project}/.cursor/rules"
os.makedirs(rules_dir, exist_ok=True)

# 主规则（从 CLAUDE.md + project memory 合并）
main_rule = f"""---
description: Project main guidelines (migrated from Claude Code)
alwaysApply: true
---

{scan['claude_md']}
"""
write(f"{rules_dir}/cc-main.mdc", main_rule)

# user profile → alwaysApply=true
if scan['home_claude_md']:
    user_rule = f"""---
description: User preferences
alwaysApply: true
---

{scan['home_claude_md']}
"""
    write(f"{rules_dir}/cc-user.mdc", user_rule)

# Memory feedback → auto-attached by globs
for m in scan['memory']:
    if m.get('type') == 'feedback':
        globs = m.get('paths', '**/*')
        content = f"""---
description: {m.get('name', m['file'])}
globs: "{globs}"
---

{m['content']}
"""
        write(f"{rules_dir}/cc-{m['file'].replace('.md','')}.mdc", content)

# Claude Code agents → manual-invoke rules
agents_dir = f"{rules_dir}/agents"
os.makedirs(agents_dir, exist_ok=True)
for a in scan['agents']:
    agent_rule = f"""---
description: {a['description']}
---

# {a['name']}

Model: {a.get('model', 'default')}

{a['instructions']}
"""
    write(f"{agents_dir}/cc-{a['name']}.mdc", agent_rule)
```

**5.B.2 MCP 配置合并**

```python
# 目标: ~/.cursor/mcp.json (global) 或 {project}/.cursor/mcp.json
import json

# 读取已有配置（避免覆盖用户现有 MCP）
cursor_mcp_path = os.path.expanduser('~/.cursor/mcp.json')
existing = {}
if os.path.exists(cursor_mcp_path):
    existing = json.load(open(cursor_mcp_path))
servers = existing.get('mcpServers', {})

# 合并 Claude Code 的 mcpServers
for name, cfg in scan['claude_json'].get('mcpServers', {}).items():
    # 转换 Claude 的 "type":"http"/"stdio" 到 Cursor 格式
    cursor_cfg = {}
    if cfg.get('type') == 'http' or cfg.get('url'):
        cursor_cfg['url'] = cfg['url']
        if cfg.get('headers'):
            # 替换明文 token 为 env 引用
            headers = {}
            for k, v in cfg['headers'].items():
                if 'auth' in k.lower() or 'bearer' in str(v).lower():
                    headers[k] = f"Bearer ${{env:CC_MCP_{name.upper()}_TOKEN}}"
                else:
                    headers[k] = v
            cursor_cfg['headers'] = headers
    else:
        # stdio
        cursor_cfg['type'] = 'stdio'
        cursor_cfg['command'] = cfg.get('command')
        cursor_cfg['args'] = cfg.get('args', [])
        if cfg.get('env'):
            cursor_cfg['env'] = {k: f"${{env:{k}}}" for k in cfg['env'].keys()}
    
    servers[f"cc-{name}"] = cursor_cfg

# 合并项目级 .mcp.json
if scan.get('mcp_json'):
    for name, cfg in scan['mcp_json'].get('mcpServers', {}).items():
        servers[f"cc-proj-{name}"] = cfg

existing['mcpServers'] = servers
write(cursor_mcp_path, json.dumps(existing, indent=2))
```

**5.B.3 User Rules（无法自动化）**

Cursor 的 User Rules 存在 Settings 数据库，没有文件路径。生成一个 `.migration/cursor-user-rules.md` 文件，引导用户：

```
⚠️ Cursor User Rules 需要手动粘贴：
1. 打开 Cursor Settings → Rules → User Rules
2. 复制 .migration/cursor-user-rules.md 的内容粘贴进去
```

**5.B.4 会话归档**

Cursor 没有会话恢复机制。把 Claude Code 的 JSONL 转为 markdown 归档到 `{project}/.migration/conversations/`（非 Cursor 识别的位置，仅供用户查阅）。

#### 5.C Codex CLI

**5.C.1 AGENTS.md（Codex 原生）**

已在 Phase 4.1 生成。Codex 自动读 `AGENTS.md` + `~/.codex/AGENTS.md`。

如果要让 Codex 也读 CLAUDE.md（便于 round-trip），往 `~/.codex/config.toml` 加：
```toml
project_doc_fallback_filenames = ["CLAUDE.md", "AGENTS.md"]
```

**5.C.2 config.toml 生成**

⚠️ **TOML 陷阱**: 所有顶层 key（`model`, `model_provider`, `project_doc_fallback_filenames`）**必须**出现在**第一个 `[section]` 之前**。一旦 `[model_providers.xxx]` 或 `[mcp_servers.xxx]` 打开，后面所有 key-value 都会被归到那个 section。

推荐直接拼字符串而非用 `tomli_w`，更容易控制顺序：

```python
# 正确的输出顺序
toml_lines = [
    '# ─── Top-level keys (必须在 [section] 之前) ───',
    f'model = "{chosen_model}"',               # glm-5 / gpt-5.4 / kimi-k2.5
    f'model_provider = "{chosen_provider}"',   # bigmodel / openai / moonshot
    'project_doc_fallback_filenames = ["CLAUDE.md", "AGENTS.md"]',  # 让 Codex 也读 CLAUDE.md
    '',
    '# ─── Provider 表 ───',
]

if chosen_provider == 'bigmodel':
    toml_lines += [
        '[model_providers.bigmodel]',
        'base_url = "https://open.bigmodel.cn/api/paas/v4"',
        'env_key = "OPENAI_API_KEY"',
        'requires_openai_auth = false',
        '',
    ]
elif chosen_provider == 'moonshot':
    toml_lines += [
        '[model_providers.moonshot]',
        'base_url = "https://api.moonshot.ai/v1"',
        'env_key = "MOONSHOT_API_KEY"',
        'requires_openai_auth = false',
        '',
    ]

toml_lines.append('# ─── MCP servers ───')
for name, claude_mcp in scan['claude_json'].get('mcpServers', {}).items():
    sid = f"cc-{name}".replace('-', '_')
    toml_lines.append(f'[mcp_servers.{sid}]')
    if claude_mcp.get('url'):
        toml_lines.append(f'url = "{claude_mcp["url"]}"')
    else:
        cmd = claude_mcp.get('command', 'npx')
        args = claude_mcp.get('args', [])
        toml_lines.append(f'command = "{cmd}"')
        toml_lines.append(f'args = {json.dumps(args)}')
    toml_lines.append('enabled = true')
    toml_lines.append('')

open(os.path.expanduser('~/.codex/config.toml'), 'w').write('\n'.join(toml_lines))
```

**验证**（测试时必做）:
```python
import tomllib
d = tomllib.load(open(os.path.expanduser('~/.codex/config.toml'), 'rb'))
assert 'project_doc_fallback_filenames' in d  # 顶层，不在 section 内
assert d['model'] == chosen_model
```

**5.C.3 ~/.codex/AGENTS.md**

Codex 在从项目目录向上走时也会拼接 `~/.codex/AGENTS.md`。把用户级 CLAUDE.md 写进去：

```python
if scan['home_claude_md']:
    write(os.path.expanduser('~/.codex/AGENTS.md'), scan['home_claude_md'])
```

#### 5.D Windsurf

```python
# .windsurfrules（项目级）
write(f"{project}/.windsurfrules", combined_project_rules)

# .windsurf/rules/*.md
os.makedirs(f"{project}/.windsurf/rules", exist_ok=True)
for m in scan['memory']:
    if m.get('type') in ('project', 'feedback'):
        write(f"{project}/.windsurf/rules/cc-{m['file']}", m['content'])

# ~/.codeium/windsurf/memories/global_rules.md
if scan['home_claude_md']:
    path = os.path.expanduser('~/.codeium/windsurf/memories/global_rules.md')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write(path, scan['home_claude_md'])

# ~/.codeium/windsurf/mcp_config.json（合并）
mcp_path = os.path.expanduser('~/.codeium/windsurf/mcp_config.json')
# Windsurf 用 serverUrl 而非 url
# 否则 schema 同 Cursor
```

#### 5.E Gemini CLI

```python
# GEMINI.md（项目）已在 Phase 4.1 生成（复用 AGENTS.md）
import shutil
shutil.copy(f"{project}/AGENTS.md", f"{project}/GEMINI.md")

# ~/.gemini/GEMINI.md（全局）
if scan['home_claude_md']:
    path = os.path.expanduser('~/.gemini/GEMINI.md')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write(path, scan['home_claude_md'])

# ~/.gemini/settings.json
settings_path = os.path.expanduser('~/.gemini/settings.json')
if os.path.exists(settings_path):
    settings = json.load(open(settings_path))
else:
    settings = {}
servers = settings.setdefault('mcpServers', {})
for name, cfg in scan['claude_json'].get('mcpServers', {}).items():
    gcfg = {}
    if cfg.get('url'):
        gcfg['httpUrl'] = cfg['url']  # Gemini 用 httpUrl
        gcfg['headers'] = cfg.get('headers', {})
    else:
        gcfg['command'] = cfg.get('command')
        gcfg['args'] = cfg.get('args', [])
        gcfg['env'] = cfg.get('env', {})
    gcfg['timeout'] = 30000
    gcfg['trust'] = False
    servers[f"cc-{name}"] = gcfg
write(settings_path, json.dumps(settings, indent=2))
```

#### 5.F GitHub Copilot

```python
# .github/copilot-instructions.md
os.makedirs(f"{project}/.github", exist_ok=True)
write(f"{project}/.github/copilot-instructions.md", main_context_md)

# .github/instructions/NAME.instructions.md (path-scoped)
os.makedirs(f"{project}/.github/instructions", exist_ok=True)
for r in scan['rules']:
    glob_pattern = r.get('paths', '**/*')
    content = f"""---
applyTo: "{glob_pattern}"
---

{r['content']}
"""
    write(f"{project}/.github/instructions/cc-{r['name']}.instructions.md", content)

# .github/prompts/*.prompt.md (从 Claude agents 转)
os.makedirs(f"{project}/.github/prompts", exist_ok=True)
for a in scan['agents']:
    prompt = f"""# {a['name']}

{a['description']}

---

{a['instructions']}
"""
    write(f"{project}/.github/prompts/cc-{a['name']}.prompt.md", prompt)

# .vscode/mcp.json (注意: 用 servers 不是 mcpServers)
os.makedirs(f"{project}/.vscode", exist_ok=True)
servers = {}
inputs = []
for name, cfg in scan['claude_json'].get('mcpServers', {}).items():
    copilot_cfg = {}
    if cfg.get('url'):
        copilot_cfg['type'] = 'http'
        copilot_cfg['url'] = cfg['url']
        # Bearer token 走 inputs 提示
        if cfg.get('headers', {}).get('Authorization'):
            input_id = f"cc_{name}_token"
            inputs.append({
                "id": input_id,
                "type": "promptString",
                "password": True,
                "description": f"Token for {name}",
            })
            copilot_cfg['headers'] = {'Authorization': f'Bearer ${{input:{input_id}}}'}
    else:
        copilot_cfg['type'] = 'stdio'
        copilot_cfg['command'] = cfg.get('command')
        copilot_cfg['args'] = cfg.get('args', [])
    servers[f"cc-{name}"] = copilot_cfg
vscode_mcp = {'inputs': inputs, 'servers': servers}
write(f"{project}/.vscode/mcp.json", json.dumps(vscode_mcp, indent=2))
```

---

### Phase 6: 密钥处理

每个目标的 MCP 配置都**不含明文密钥**。密钥走每个目标各自的机制：

| 目标 | 密钥机制 |
|-----|---------|
| Hermes | `~/.hermes/.env` + Vault |
| Cursor | `${env:VAR}` 插值 → 用户 shell env |
| Codex | `env_key` + shell env |
| Windsurf | `${env:VAR}` 插值 |
| Gemini CLI | `env` 块 + shell env |
| Copilot | `inputs` + `${input:id}` |

生成一个 `.migration/SECRETS_SETUP.md` 教用户如何设置环境变量。

---

### Phase 7: 验证

**7.A Hermes**: `hermes stats` + FTS5 搜索
**7.B Cursor**: 检查 `.cursor/rules/` 文件计数 + `~/.cursor/mcp.json` 语法
**7.C Codex**: `codex config check` + `codex mcp list`
**7.D Windsurf**: 检查 `.windsurfrules` + `mcp_config.json` 语法
**7.E Gemini CLI**: 读 `GEMINI.md` 大小 + `gemini mcp list`
**7.F Copilot**: 检查 `.github/` + `.vscode/mcp.json`

---

### Phase 8: 完成报告

```
╔════════════════════════════════════════════════╗
║   🔀 Multi-Target Code Migration 完成!        ║
╚════════════════════════════════════════════════╝

项目: {project}
迁移到 {N} 个目标:

✅ Hermes Agent
    状态: 已配置 + 安装
    会话数据库: ~/.hermes/state.db ({size} MB)
    启动: cd {project} && hermes

✅ Cursor
    项目规则: {project}/.cursor/rules/ ({N} 个)
    全局 MCP: ~/.cursor/mcp.json ({N} 个 cc-* 服务器)
    ⚠️ User Rules 需手动粘贴: 见 .migration/cursor-user-rules.md

✅ Codex CLI
    AGENTS.md: {project}/AGENTS.md + ~/.codex/AGENTS.md
    config.toml: ~/.codex/config.toml ({N} 个 mcp_servers)
    启动: cd {project} && codex

✅ Gemini CLI
    GEMINI.md: {project}/GEMINI.md + ~/.gemini/GEMINI.md
    settings.json: ~/.gemini/settings.json
    启动: cd {project} && gemini

统一上下文 (AGENTS.md): {project}/AGENTS.md
  ↑ Cursor / Codex / Gemini 都会读这份

密钥设置: 见 .migration/SECRETS_SETUP.md
  需要 export 的环境变量: {N} 个

审计: .migration/audit.json
```

---

## 注意事项

- **幂等**: 重复运行不会覆盖用户已有的 rules / MCP 配置；用 `cc-` 前缀隔离
- **无损**: 永远不修改 `~/.claude/` 原始数据；目标侧用 merge-not-overwrite 策略
- **Round-trip 友好**: 每个目标都额外写一份 AGENTS.md（通用），便于跨目标切换
- **无法自动化的部分**: Cursor User Rules、VS Code Settings — skill 生成待粘贴的文件 + 图文步骤
- **密钥安全**: 所有 MCP 配置中的明文 token 自动替换为环境变量引用
- **字符限制**:
  - Cursor MDC: 无硬限制，但建议单文件 ≤ 500 行
  - Codex AGENTS.md: `project_doc_max_bytes` 默认 32KB
  - Gemini GEMINI.md: 由 `context.discoveryMaxDirs` 控制
- **版本兼容**:
  - Cursor 2.1+（Memories 已废弃，改用 Rules）
  - Codex CLI 任意版本（TOML schema 稳定）
  - Hermes Agent 任意版本
- **Kimi / Moonshot**: 选 Codex 或 Hermes 目标，provider 选 `moonshot`

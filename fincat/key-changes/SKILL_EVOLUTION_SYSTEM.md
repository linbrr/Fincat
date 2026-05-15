# Skill 进化系统完整文档（2026-04-24）

> 生成日期：2026-04-24
> 关联文件：skill_evolver.py, skill_manage.py, skill_lifecycle_manager.py, skills.py, loop.py

---

## 目录

1. [整体架构](#1-整体架构)
2. [SkillEvolver 详解](#2-skillevolver-详解)
3. [SkillManageTool 详解](#3-skillmanagetool-详解)
4. [Frontmatter 格式](#4-frontmatter-格式)
5. [SkillValidator 校验](#5-skillvalidator-校验)
6. [生命周期管理](#6-生命周期管理)
7. [内置 Cron Job](#7-内置-cron-job)
8. [与 Hermes skill_manager 对比](#8-与-hermes-skill_manager-对比)

---

## 1. 整体架构

### 1.1 核心组件关系

```
AgentLoop._process_message()
    │
    ├── 任务完成 → _schedule_background(SkillEvolver.on_task_completed(task_ctx))
    │                   │
    │                   ├── Phase 1: _llm_judge_worthiness() — LLM 判断是否值得保存
    │                   │              ├── 传入：user_message, tools_used, assistant_response
    │                   │              ├── 已有 skills 列表（代码预查 + LLM 决策）
    │                   │              ├── 相似度预查（lifecycle_manager._compute_similarity）
    │                   │              └── 输出：{worthy, action, name, content, ...}
    │                   │
    │                   └── Phase 2: skill_manage.execute(**manage_params)
    │                               ├── action=create → 写入 workspace/skills/{name}/SKILL.md
    │                               ├── action=edit → 完整重写 SKILL.md
    │                               ├── action=patch → 局部替换 old_string → new_string
    │                               └── action=delete → 删除整个 skill 目录
    │
    └── SkillEvolver 启动时
            └── _ensure_lifecycle_cron_job() — 创建每6小时一次的 lifecycle 检查 job
```

### 1.2 触发点（loop.py）

```python
# loop.py:880
if self._skill_evolver and (tools_used or final_content):
    task_ctx = TaskContext(
        user_message=msg.content,
        assistant_response=final_content[:500] if final_content else "",
        tools_used=tools_used,
        iterations=1,
        session_key=msg.session_key,
    )
    self._schedule_background(self._skill_evolver.on_task_completed(task_ctx))
```

**触发条件**：有工具调用 `OR` 有回复内容（纯对话任务也会触发）。

---

## 2. SkillEvolver 详解

### 2.1 类定义

```python
class SkillEvolver:
    def __init__(
        self,
        workspace: Path,
        provider: Any,
        model: str,
        skill_manage_tool: Any = None,      # SkillManageTool 实例
        lifecycle_manager: Any = None,       # SkillLifecycleManager 实例（可选）
        cron_service: Any = None,            # CronService 实例（可选）
    )
```

### 2.2 Phase 1: LLM 判断（`_llm_judge_worthiness`）

**输入**：TaskContext（user_message, tools_used, assistant_response）

**Prompt 构造**：
1. 已有 skills 摘要（`_get_existing_skills_summary`）— 列出所有 skill 的 name + description
2. 相似度预查结果（如果有）— 代码层用 Jaccard 相似度计算（阈值 0.4）
3. 判断标准（满足任一即值得保存）：
   - 复杂任务：使用了 3 个以上不同工具
   - 修复错误：处理了棘手错误/非预期输入/边缘情况
   - 非平凡工作流：发现了一套有复用价值的工具组合流程
4. 判断逻辑：已有类似 skill → 优先 patch；无类似 skill → create

**LLM 输出格式**：
```json
{
  "worthy": true 或 false,
  "reason": "简要说明（1-2句话）",
  "action": "create" 或 "patch" 或 "delete",
  "name": "skill 名称",
  "content": "完整 SKILL.md 内容（action=create 时必需，必须以 --- 开头）",
  "old_string": "要替换的原文（action=patch 时必需）",
  "new_string": "替换后的内容（action=patch 时必需）",
  "patch_reason": "missing_steps / wrong_commands / pitfalls"
}
```

### 2.3 Phase 2: 执行保存

根据 LLM 返回的 action，构造 `skill_manage` 参数并执行：
- `action=create` → 传入 `content`
- `action=patch` → 传入 `old_string`, `new_string`, `patch_reason`
- `action=delete` → 无额外参数

### 2.4 生命周期查询方法（供外部/agent 调用）

| 方法 | 功能 |
|------|------|
| `get_stale_warnings()` | 返回 30 天未用 skill 的警告列表 |
| `get_merge_suggestions()` | 返回相似度 ≥ 0.5 的 skill 合并建议 |
| `list_skills()` | 返回所有 skill 名称列表 |
| `_ensure_lifecycle_cron_job()` | 幂等创建每6小时一次的检查 cron job |

---

## 3. SkillManageTool 详解

### 3.1 四个 Action

| Action | 用途 | 必需参数 |
|--------|------|----------|
| `create` | 第一次解决这类问题，创建新 skill | `content`（完整 SKILL.md） |
| `edit` | 完整重写已有 skill 的 SKILL.md | `content` |
| `patch` | 局部替换（修复 bug、补充步骤、添加警告） | `old_string`, `new_string`, `patch_reason` |
| `delete` | 删除整个 skill 目录 | 无 |

### 3.2 核心机制

**`_ensure_frontmatter`**：自动补全 frontmatter 缺失字段
- 无 `---` 开头 → 自动包一层 `--- name: xxx description: "xxx skill" ---`
- `---` 开头但未闭合 → 补全 `---`
- 缺 `description` → 自动插入

**`_validate_frontmatter_yaml`**：用 `yaml.safe_load` 解析 frontmatter
- 检查 `name` 字段存在
- 检查 `description` 字段存在
- 检查 body 非空

**`_atomic_write_text`**：原子写入防崩溃
```python
fd, temp = tempfile.mkstemp(dir=parent, prefix=f".{name}.tmp.")
os.write(fd, content)
os.fdopen(fd).close()
os.replace(temp, file_path)  # 原子替换，崩溃不损坏原文件
```

**失败回滚**：`edit` 和 `patch` 操作前备份原内容，失败时恢复。

### 3.3 SkillManager 类架构

```
SkillManageTool (Tool 接口)
    └── SkillManager (纯执行层)
            ├── create(name, content, reason)
            ├── edit(name, content, reason)
            ├── patch(name, old_string, new_string, patch_reason)
            ├── delete(name)
            ├── _ensure_frontmatter(content, name)
            ├── _validate_frontmatter_yaml(content)
            ├── _append_improvement_reason(content, reason, old_string)
            └── _record_metric(event_type, skill_name, metadata)
```

---

## 4. Frontmatter 格式

### 4.1 标准格式

```yaml
---
name: stock-analysis
description: "个股综合技术分析：实时行情、K线数据、技术指标（MA/MACD/RSI/KDJ/布林带）、板块对比。当用户说'分析股票'、'帮我看看'时触发。"
---
```

### 4.2 字段说明

| 字段 | 必需 | 说明 |
|------|------|------|
| `name` | ✅ | skill 名称，英文小写+连字符 |
| `description` | ✅ | 触发条件 + 功能描述，20-500 字符 |
| `stale` | ❌ | 生命周期标记，stale: true 时表示长期未用 |
| `improved_at` | ❌ | patch 时自动追加 |
| `improvement_reason` | ❌ | patch 原因记录 |
| `version` | ❌ | 版本号（已废弃，不再强制校验） |
| `created_at` | ❌ | 创建日期（已废弃，不再强制校验） |

### 4.3 SKILL.md Body 结构

```markdown
# Skill 标题

简短描述。

## 工作流
1. **步骤1**：`tool_name(param="value")` 说明
2. **步骤2**：`tool_name(param="value")` 说明

## 输出格式
---
模板内容，使用 {占位符} 标注变量
---

## 写作/执行要求（可选）
- 简洁，总字数不超过 X 字
- 有观点，不只是罗列数字
```

---

## 5. SkillValidator 校验

### 5.1 四步校验（不含 metadata）

| 步骤 | 内容 | 通过条件 |
|------|------|----------|
| Step 1 | Frontmatter 格式 | 以 `---` 开头和结尾 |
| Step 2 | tools 列表工具名 | 列表中的工具名在 ToolRegistry 中 |
| Step 3 | description 长度 | 20-500 字符 |
| Step 4 | examples 存在性 | `## 示例` 或 `## Example` 存在（宽松） |

### 5.2 预处理（脱壳）

校验前自动剔除：
- `<think>...</think>` LLM 思考内容
- Markdown 代码块包裹（```markdown ... ```）

---

## 6. 生命周期管理

### 6.1 SkillLifecycleManager

| 方法 | 功能 |
|------|------|
| `check_staleness()` | 找出 30 天未用的 skill |
| `mark_stale(name)` | 在 frontmatter 写入 `stale: true` |
| `unmark_stale(name)` | 移除 `stale: true` |
| `find_merge_candidates()` | 找出相似度 ≥ 0.5 的 skill 对 |
| `get_lifecycle_report()` | 完整报告（stale 列表 + merge 候选 + invalid） |

### 6.2 策略

| 操作 | 触发条件 | 行为 |
|------|----------|------|
| 标记 stale | 30 天未用 | 自动写入 `stale: true` |
| 取消 stale | 被再次使用 | 自动移除 `stale: true` |
| 提示用户 | 180 天未用 + stale | 通过 cron job 提示用户"是否删除" |
| 合并建议 | 两个 skill 相似度 ≥ 0.5 | 通过 cron job 提示 LLM |

### 6.3 原则

- **删除必须用户确认**，不自动删除
- Stale 标记不影响 skill 存在，只在 system prompt 中展示提醒
- SkillManager 的 create/edit/patch 不触碰 `stale` 字段（由 lifecycle manager 独立管理）

---

## 7. 内置 Cron Job

### 7.1 自动创建

AgentLoop 初始化时，`SkillEvolver._ensure_lifecycle_cron_job()` 幂等检查并创建 cron job：
- 名称：`skill-lifecycle-check`
- 频率：每 6 小时一次
- 消息：触发 agent 调用 `get_stale_warnings()` + `get_merge_suggestions()`

### 7.2 触发效果

Cron 触发时，agent 收到消息，执行两个方法后以简洁列表形式汇报给用户：
```
⚠️ Skill 'xxx' 已 45 天未使用（历史调用 2 次），是否考虑删除？
💡 可考虑合并：'stock-analysis' 和 'stock-technical-analysis'（相似度 82%，共享工具: stock_quote, stock_kline）
```

---

## 8. 与 Hermes skill_manager 对比

| 特性 | fincat | Hermes |
|------|---------|--------|
| 判断模式 | SkillEvolver 固定 prompt | tool description 引导 LLM 自主判断 |
| 触发时机 | 任务完成后后台判断 | LLM 自行决定何时保存 |
| actions | create / edit / patch / delete | create / edit / patch / delete / write_file / remove_file |
| frontmatter 校验 | `yaml.safe_load` + 正则补全 | `yaml.safe_load` + 严格校验 |
| 写入安全 | `_atomic_write_text` + 回滚 | `_atomic_write_text` + security scan |
| 相似度检测 | 代码预查（lifecycle_manager）+ LLM 决策 | 无（靠 LLM 自己判断） |
| 生命周期 | 内置 cron job + stale 标记 | 无内置（靠 LLM 自觉） |
| 失败回滚 | edit/patch 有回滚 | edit/patch 有回滚 |
| fuzzy patch | 无 | `fuzzy_find_and_replace` |

---

## 附录：关键文件路径

| 文件 | 关键位置 |
|------|----------|
| `fincat/agent/skill_evolver.py` | `on_task_completed()` line 78, `_llm_judge_worthiness()` line 157 |
| `fincat/agent/tools/skill_manage.py` | `SkillManager.create/edit/patch/delete()`, `_ensure_frontmatter()`, `_atomic_write_text()` |
| `fincat/agent/skills.py` | `SkillValidator.validate()` line 397, `SkillsLoader` line 20 |
| `fincat/agent/skill_lifecycle_manager.py` | `SkillLifecycleManager` line 33, `check_staleness()` line 68 |
| `fincat/agent/loop.py` | `on_task_completed` 触发 line 880 |

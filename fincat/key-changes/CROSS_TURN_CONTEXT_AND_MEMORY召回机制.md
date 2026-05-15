# 跨轮上下文召回机制 — Cross-Turn Memory Prefetch（2026-04-25）

> 日期：2026-04-25
> 关联文件：loop.py, skill_manage.py, memory_manager.py, memory_provider.py, skill_memory_provider.py

---

## 一句话概括

实现了 Hermes 风格的 MemoryManager + MemoryProvider 架构，让 agent 在 skill 被创建/更新后，下一轮对话能通过 `prefetch_all()` 主动感知到"上轮创建了 xxx skill"，打通 skill 写入 → 召回 → 感知的完整链路。

---

## 详细修改

### 1. 新增 `fincat/agent/memory_provider.py`

**角色**：定义 MemoryProvider ABC 接口，参考 hermes-agent 的设计。

```python
class MemoryProvider(ABC):
    name: str                               # provider 名称，用于 on_memory_write 跳过 builtin
    @abstractmethod
    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """返回与 query 相关的上下文字符串。每轮对话前调用一次。"""
        ...

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """skill/memory 被创建/更新/删除时调用，通知外部 provider 同步。"""
        pass  # 默认空实现
```

---

### 2. 新增 `fincat/agent/memory_manager.py`

**角色**：管理多个 MemoryProvider，封装 `prefetch_all()` 和 `on_memory_write()` 的分发逻辑。

**核心方法**：

```python
class MemoryManager:
    def prefetch_all(self, query: str, *, session_id: str = "") -> str:
        """收集所有 providers 的召回上下文，合并返回。
        失败不影响其他 provider。"""
        parts = []
        for provider in self._providers:
            result = provider.prefetch(query, session_id=session_id)
            if result:
                parts.append(result)
        return "\n\n".join(parts)

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """广播给所有外部 providers（跳过 name=='builtin' 的）。"""
        for provider in self._providers:
            if provider.name == "builtin":
                continue
            provider.on_memory_write(action, target, content)
```

---

### 3. 新增 `fincat/agent/skill_memory_provider.py`

**角色**：实现 SkillMemoryProvider，负责记录 skill 写事件，并在下一轮召回相关 skill。

**功能**：

- **`on_memory_write(action, target, content)`**：当 skill 被 create/edit/patch/delete 时调用。
  - 从 `workspace/skills/{name}/SKILL.md` 读取 description
  - 保存到内存缓存 `_recent[skill_name]`
  - 可选持久化到 SQLite ledger 表 `skill_memory_ledger`

- **`prefetch(query)`**：查询与当前用户消息相关的最近创建的 skill。
  - 匹配逻辑：skill name 分词（如 `stock-analysis` → `stock`, `analysis`）与 query 分词做交集
  - 返回格式：`# Recently Created/Updated Skills\n- Skill 'stock-analysis' (create): ...`

- **SQLite ledger 表**（可选，支持跨进程持久化）：
```sql
CREATE TABLE skill_memory_ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT    NOT NULL,
    action     TEXT    NOT NULL,
    content    TEXT,
    created_at TEXT    NOT NULL
);
CREATE INDEX idx_skill_ledger_name    ON skill_memory_ledger(skill_name);
CREATE INDEX idx_skill_ledger_created ON skill_memory_ledger(created_at);
```

---

### 4. 修改 `fincat/agent/tools/skill_manage.py`

**修改点 1**：`SkillManager.__init__` 新增 `memory_manager` 参数。

```python
def __init__(self, workspace: Path, validator: Any = None, tracker: Any = None,
             memory_manager: Any = None):
    self._memory_manager = memory_manager
```

**修改点 2**：`SkillManageTool.__init__` 传递 `memory_manager` 给 `SkillManager`。

```python
def __init__(self, workspace: Path, validator: Any = None, tracker: Any = None,
             memory_manager: Any = None):
    self._manager = SkillManager(workspace, validator=validator, tracker=tracker,
                                 memory_manager=memory_manager)
```

**修改点 3**：新增 `_notify_memory_write()` 方法。

```python
def _notify_memory_write(self, action: str, skill_name: str) -> None:
    """通知外部 memory provider（如果有）：skill 被创建/更新/删除。"""
    if not self._memory_manager:
        return
    self._memory_manager.on_memory_write(action=action, target="skill", content=skill_name)
```

**修改点 4**：在 `create / edit / patch / delete` 成功执行后调用 `_notify_memory_write()`。

```python
# create() 成功后：
self._notify_memory_write("create", name)

# edit() 成功后：
self._notify_memory_write("edit", name)

# patch() 成功后：
self._notify_memory_write("patch", name)

# delete() 成功后：
self._notify_memory_write("delete", name)
```

**修改点 5**（Bug 修复）：删除了 `patch()` 里重复一行的 `return False`。

---

### 5. 修改 `fincat/agent/loop.py`

**修改点 1**：`__init__` 中初始化 `MemoryManager` + `SkillMemoryProvider`，仅当 `enable_sqlite=True` 时启用。

```python
# Initialize memory manager + skill memory provider (for cross-turn prefetch)
from fincat.agent.memory_manager import MemoryManager
from fincat.agent.skill_memory_provider import SkillMemoryProvider
self._memory_manager: MemoryManager | None = None
if enable_sqlite:
    skill_mem_provider = SkillMemoryProvider(
        workspace=workspace,
        db_path=db_path,
    )
    self._memory_manager = MemoryManager([skill_mem_provider])
```

**修改点 2**：`__init__` 中将 `_memory_manager` 注入到 `SkillManageTool`。

```python
self._skill_manage = SkillManageTool(
    workspace=workspace,
    validator=validator,
    tracker=self._usage_tracker,
    memory_manager=self._memory_manager,  # 新增
)
```

**修改点 3**：`_process_message()` 在 `_run_agent_loop` 调用前执行 `prefetch_all()`，将召回结果注入到 user message。

```python
# Cross-turn memory prefetch: collect relevant context from memory providers
# before the agent loop starts (参考 hermes run_agent.py:9014)
prefetch_context = ""
if self._memory_manager and isinstance(msg.content, str):
    prefetch_context = self._memory_manager.prefetch_all(msg.content, session_id=key) or ""

# Inject prefetch context into the user message
if prefetch_context and initial_messages:
    last = initial_messages[-1]
    if last.get("role") == "user":
        last = copy.copy(last)
        original_content = last.get("content", "")
        if isinstance(original_content, str):
            last["content"] = (
                f"{original_content}\n\n"
                f"---\n# Relevant Context from Previous Turns\n\n"
                f"{prefetch_context}"
            )
        initial_messages[-1] = last
```

---

## 完整数据流

```
用户说 "帮我分析英伟达"
    ↓
loop._process_message()
    ↓
memory_manager.prefetch_all("帮我分析英伟达")
    ↓
SkillMemoryProvider.prefetch() → 匹配 skill name tokens
    ↓
返回 "Skill 'stock-analysis' (create)"（如果有上一轮创建的）
    ↓
注入 user message → "# Relevant Context from Previous Turns\nSkill 'stock-analysis' (create)"
    ↓
Agent 在 system prompt 中感知到 "上轮创建了 stock-analysis"
    ↓
Agent 可以主动调用/使用这个 skill
```

```
Agent 说 "分析英伟达走势" → skill_manage.create("nvidia-analysis", ...)
    ↓
skill_manage.create() 成功后调用 _notify_memory_write("create", "nvidia-analysis")
    ↓
memory_manager.on_memory_write("create", "skill", "nvidia-analysis")
    ↓
SkillMemoryProvider.on_memory_write() 记录到 _recent 内存 + SQLite ledger
    ↓
下一轮用户问相关问题 → prefetch_all() 召回 → Agent 感知到刚创建的 skill
```

---

## 关键文件路径

| 文件 | 改动类型 | 关键位置 |
|------|----------|----------|
| `fincat/agent/memory_provider.py` | 新增 | `MemoryProvider` ABC |
| `fincat/agent/memory_manager.py` | 新增 | `MemoryManager.prefetch_all/on_memory_write` |
| `fincat/agent/skill_memory_provider.py` | 新增 | `SkillMemoryProvider.prefetch/on_memory_write` |
| `fincat/agent/tools/skill_manage.py` | 修改 | `SkillManager.__init__`, `_notify_memory_write`, create/edit/patch/delete |
| `fincat/agent/loop.py` | 修改 | `__init__` line 291-300, `_process_message` prefetch 注入 |

---

## 参考设计（Hermes）

| Hermes 位置 | 对应 fincat 实现 |
|-------------|------------------|
| `run_agent.py:9014` — `prefetch_all()` 调用 | `loop.py:825-850` — prefetch + 注入 |
| `run_agent.py:7743` — `on_memory_write()` 调用 | `skill_manage.py` — `_notify_memory_write()` |
| `memory_manager.py:178` — `prefetch_all` | `memory_manager.py` — `prefetch_all()` |
| `memory_manager.py:315` — `on_memory_write` | `memory_manager.py` — `on_memory_write()` |
| `memory_provider.py:223` — `on_memory_write` 接口 | `memory_provider.py` — `on_memory_write()` |

---

## 注意事项

- **`enable_sqlite` 环境变量**：SkillMemoryProvider 的 SQLite ledger 仅在 `NANOBOT_ENABLE_SQLITE_MEMORY=1` 时启用。内存缓存始终有效。
- **不影响现有逻辑**：没有 `_memory_manager` 时，所有操作降级为 no-op，不影响 skill 创建/管理功能。
- **SkillEvolver 评估结果**：SkillEvolver 的 LLM 判断是独立的，"不值得保存" 说明当前任务的 `tools_used=[]` 且内容仅为信息检索，不满足三个保存条件之一（复杂任务/修复错误/非平凡工作流）。

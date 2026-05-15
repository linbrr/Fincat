# 死代码清理计划

**日期**: 2026-05-12
**状态**: 待实施

## 背景
项目经过多次架构迭代（memory restructure、prediction engine 统一等），积累了大量不再使用的文件、函数和 import。需要清理以降低维护负担。

---

## 一、删除整个文件（6 个死模块）

| 文件 | 原因 |
|---|---|
| `fincat/agent/proactive_skill.py` | 已被 PredictionEngine + TopicCache 替代 |
| `fincat/agent/evolution.py` | 从未完成（有 TODO stub），零引用 |
| `fincat/agent/orchestrator.py` | 零引用，从未集成 |
| `fincat/agent/signal_store.py` | 已被 TopicCache + PredictionEngine 替代（loop.py:404 注释确认） |
| `fincat/agent/skill_packager.py` | 零引用，连测试都没有 |
| `fincat/agent/memory_prefilter.py` | 仅测试引用，生产代码零使用 |

配套测试文件也一并删除：
- `tests/agent/test_signal_store.py`
- `tests/agent/test_proactive_skill.py`
- `tests/agent/test_memory_prefilter.py`

## 二、清理死函数（按文件）

### `fincat/agent/memory.py` — 6 个死方法
- `get_memory_context()` (L334)
- `get_entity_summary()` (L371)
- `all_categories()` (L285)
- `write_memory()` (L229)
- `write_soul()` (L321)
- `write_user()` (L329)

### `fincat/agent/memory_monitor.py` — 5 个
- `check_periodic()` (L83)
- `query_category()` (L420)
- `get_decayed_items()` (L424)
- `last_scan` property (L486)
- `item_count` property (L490)

### `fincat/agent/memory_store_v2.py` — 3 个
- `get_unembedded_items()` (L485)
- `get_all_active_items()` (L491)
- `search_similar_batch()` (L259)

### `fincat/agent/memory_sqlite.py` — 16 个

**反思记忆整套功能（reflection_vault + sqlite-vec）**：从未接入 Agent 循环，唯一调用方 `evolution.py` 已是死代码。
- `add_reflection()` (L728)
- `search_reflections()` (L775) — 仅 `evolution.py` 调用
- `_search_reflections_vector()` (L795) — sqlite-vec 向量搜索，零生产调用
- `_search_reflections_text()` (L837) — 文本回退搜索，零生产调用
- `_get_all_reflections()` (L878)
- `VEC_SCHEMA_SQL` 常量 (L125) — reflection_vault_vec 建表语句
- `reflection_vault` 表创建 (L113) 和 `reflection_vault_vec` 虚拟表创建 (L126) 可从 `_init_db()` 中移除

**其他死方法**：
- `sync_from_markdown()`, `rebuild_index()`, `sync_entries_from_markdown()`
- `search_transactions()`, `search_knowledge()`, `search_memory_entries()`
- `get_profile()`, `add_knowledge()`
- `delete_task()`, `_extract_md_tags()`

**可选依赖清理**：`sqlite-vec` (`import sqlite_vec`) 仅用于 reflection_vault_vec，移除反思功能后可从可选依赖中删除。

### `fincat/agent/context.py` — 3 个
- `_is_template_content()` (L177)
- `add_tool_result()` (L246)
- `add_assistant_message()` (L254)

### `fincat/agent/memory_retrieval_filter.py` — 2 个
- `invalidate_session()` (L163)
- `clear_cache()` (L167)

### `fincat/agent/prediction_engine.py` — 2 个
- `_next_expiry()` (L123)
- `_save_rules()` (L106)

### `fincat/agent/pattern_miner.py` — 1 个
- `_extract_topic()` (L240)

## 三、清理未使用 import（22 处）

分布在 evolution.py、loop.py、memory_manager.py、memory_provider.py、memory_sqlite.py、pattern_miner.py、pattern_snapshot.py、prediction_engine.py、skill_evolver.py、skill_lifecycle_manager.py、skill_memory_provider.py、topic_dispatcher.py、topic_store.py 等文件中。随文件删除或逐个清理。

## 四、不删除的（说明）

- **knowledge/ 整个目录**：已从 git 移除但本地保留，属于独立子项目，不在本次清理范围
- **skill-creator/scripts/**：独立 CLI 工具，有测试覆盖，保留
- **fincat/da/ 空目录**：删除
- **根目录杂项文件**（sample_chart.html/png、LOGO.png、fincat.png、static/）：非代码，单独处理

---

## 验证方式
- 删除后运行 `python -c "import fincat.agent.loop"` 确认无 ImportError
- 运行现有测试套件确认无回归

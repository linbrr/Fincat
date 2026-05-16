Compare conversation history against current memory category files. Also scan memory files for stale content — even if not mentioned in history.

If a Pre-filter Queue section is present, validate each item:
- CONFIRM: item is accurate and worth keeping as-is
- RECLASSIFY: item is valuable but in the wrong category — output as [FILE] with correct category
- DISCARD: item is noise, not worth remembering — output as [FILE-REMOVE]

## Output Format (strict — each line must start with one of these prefixes)

[FILE] category: one atomic fact
[FILE-REMOVE] reason for removal
[SKILL] kebab-case-name: one-line description
[SKIP]

Memory categories (对应 memory_type，统一映射到 CategoryManager):
- preference → 用户偏好、风格
- knowledge → 产品知识、市场机制
- profile → 用户画像、资产
- compliance → 合规规则
- behavior → 行为洞察（行为模式、节奏）
- insight → 行为洞察（决策逻辑）
- event → 行为洞察（系统事件、提醒、监控任务）
- goal → custom/对话案例（用户目标、计划、创意）
- case → custom/对话案例（对话案例）

## Examples

[FILE] preference: risk appetite is low, prefers R2 and below products
[FILE] knowledge: R2 means medium risk, suitable for steady investors
[FILE] case: user asked about product safety → agent answered with risk explanation
[FILE] profile: risk score 85.5, life stage: early investor, 50k savings, no debt
[FILE] insight: user chose conservative allocation (1万应急+3万稳健+1万定投) because capital is small, prioritizes capital preservation
[FILE] insight: user panicked when market dropped 3%, sold position at loss — next time proactively reassure when volatility < 5%
[FILE-REMOVE] outdated market snapshot from 14 days ago
[SKILL] explain-risk-rating: explain risk levels to conservative investors
[SKIP]

## Behavior Pattern Extraction

In addition to the above categories, extract user behavior patterns from conversation history:

[BEHAVIOR] active hours: user typically active weekday evenings 20:00-23:00 | stability: stable
[BEHAVIOR] weekly pattern: checks gold price every Friday afternoon | cycle: weekly | time: Friday 15:00
[BEHAVIOR] behavior chain: views gold → reads market news → checks portfolio | frequency: 8 times

## Rules

- Every [FILE] must include exactly one category prefix and one atomic fact
- Atomic facts: "prefers R2 products" not "discussed investment preferences"
- Corrections: [FILE] preference: location corrected to Shanghai, not Osaka
- Do NOT write analysis paragraphs — only tagged lines
- Do NOT use markdown headings, bullet points, or numbered lists — only tagged lines

## Staleness

Flag for [FILE-REMOVE]:
- Time-sensitive data older than 14 days: market snapshots, one-time events
- Completed one-time tasks: resolved complaints, finished inquiries
- Superseded approaches

## Skill Discovery

Flag [SKILL] when ALL are true:
- A repeatable workflow appeared 2+ times
- It involves clear, specific steps
- It's substantial enough to warrant its own instruction set
- Don't worry about duplicates — Phase 2 checks existing skills

Do not add: current weather, transient status, temporary errors, conversational filler.

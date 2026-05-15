Extract key information from this conversation into the following structured format.
Only include sections that have content — skip empty sections entirely.

## 目标
What the user is trying to accomplish (short-term and long-term goals mentioned).

## 约束
Constraints, rules, limits, or boundaries the user has set (stop-loss, budget, preferences, compliance rules).

## 已完成
Tasks completed, questions answered, decisions finalized in this conversation.

## 进行中
Ongoing work, pending tasks, unresolved questions, items awaiting user action.

## 关键决策
Important choices made, trade-offs discussed, conclusions reached.

## 下一步
Next steps, planned actions, upcoming deadlines, follow-up items.

## 用户信息
Personal info, preferences, habits, opinions, corrections the user made.

---

Rules:
- Priority: user corrections and preferences > constraints > decisions > completed tasks
- Skip: code patterns derivable from source, git history, anything already in existing memory
- Each item: one concise line, no preamble
- If nothing noteworthy, output: (nothing)
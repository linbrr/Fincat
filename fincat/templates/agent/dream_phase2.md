Update memory files based on the analysis below.
- [FILE] entries: add the described content to the appropriate category file
- [FILE-REMOVE] entries: delete the corresponding content from memory files
- [SKILL] entries: create a new skill under skills/<name>/SKILL.md using write_file

## Category file paths (relative to workspace root)
- memory/user_preferences.md — risk tolerance, industry preferences, communication style, watchlist
- memory/product_knowledge.md — product details, financial terms, market mechanics
- memory/conversation_cases.md — service cases, complaint handling, FAQ patterns, slot-filling logic
- memory/compliance_rules.md — regulatory rules, forbidden phrases, risk warnings
- memory/user_profile.md — risk score, asset snapshot, life stage, tax status, financial situation
- memory/behavioral_insights.md — decision logic, market reactions, lessons learned (why user made certain choices)
- SOUL.md — bot behavior, tone
- USER.md — user identity

Do NOT guess paths.

## Editing rules
- Edit directly — file contents provided below, no read_file needed
- Use exact text as old_text, include surrounding blank lines for unique match
- Batch changes to the same file into one edit_file call
- For deletions: section header + all bullets as old_text, new_text empty
- Surgical edits only — never rewrite entire files
- If nothing to update, stop without calling tools

## Skill creation rules (for [SKILL] entries)
- Use write_file to create skills/<name>/SKILL.md
- Before writing, read_file `{{ skill_creator_path }}` for format reference (frontmatter structure, naming conventions, quality standards)
- **Dedup check**: read existing skills listed below to verify the new skill is not functionally redundant. Skip creation if an existing skill already covers the same workflow.
- Include YAML frontmatter with name and description fields
- Keep SKILL.md under 2000 words — concise and actionable
- Include: when to use, steps, output format, at least one example
- Do NOT overwrite existing skills — skip if the skill directory already exists
- Reference specific tools the agent has access to (read_file, write_file, exec, web_search, etc.)
- Skills are instruction sets, not code — do not include implementation code

## Quality
- Every line must carry standalone value
- Concise bullets under clear headers
- When reducing (not deleting): keep essential facts, drop verbose details
- If uncertain whether to delete, keep but add "(verify currency)"

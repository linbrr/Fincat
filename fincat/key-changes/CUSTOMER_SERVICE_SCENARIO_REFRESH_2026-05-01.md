# 金融智能客服场景重构 — 2026-05-01

## 背景

当前 agent 定位为"个人量化投资助手/操盘手"，但实际需求是"**金融行业智能客服**"。

定位差异导致多处模板和配置需要调整：
- **自进化** → 客服场景的 skill 进化逻辑保留，但进化方向围绕"问答/知识库"
- **个性化** → 客服场景的个性化记忆（用户偏好、常见问题）
- **轻量化** → 保持现有优势

---

## 一、System Prompt 模板修改

### 1.1 SOUL.md — 人格重写

**文件**: `fincat/templates/SOUL.md`

**变更前**: 定位为"个人操盘手"，强调风险控制、止损、仓位管理

**变更后**:
```markdown
# Soul

I am a professional financial customer service assistant.
I am patient, knowledgeable, and always prioritize compliance and user trust.

Core principles:
- Provide accurate, compliant financial information
- Never make promises about investment returns
- Protect user privacy and data security
- Escalate complex issues to human support when uncertain
- Keep responses clear, concise, and actionable

I say what I know, acknowledge what I don't, and never guess.
```

---

### 1.2 AGENTS.md — 架构简化

**文件**: `fincat/templates/AGENTS.md`

**变更前**: 多Agent架构（Master/Data/Market/Quant）针对操盘手场景

**变更后**: 单客服架构，增加 Escalation Rules

---

### 1.3 identity.md — 身份重写

**文件**: `fincat/templates/agent/identity.md`

**变更前**: 强调交易策略、技术指标（MACD、RSI、K线等）

**变更后**: 强调客服功能：
- Account Support: 余额查询、交易历史、密码重置
- Product Information: 产品详情、费率结构、条款
- Complaint Handling: 倾听、共情、适当升级
- Compliance Guidance: 政策解释、风险披露、用户权益
- Knowledge Q&A: 金融知识普及教育

---

## 二、合规/风控配置调整

### 2.1 risk_scorer.py — 权重调整

**文件**: `fincat/agent/defense/risk_scorer.py`

**变更**:
1. 情绪风险权重: `INTENT_WEIGHT=0.4, EMOTION_WEIGHT=0.3, ACTION_WEIGHT=0.3`
   → `INTENT_WEIGHT=0.3, EMOTION_WEIGHT=0.4, ACTION_WEIGHT=0.3`
   **原因**: 客服场景中负面情绪更常见，情绪判断优先级更高

2. 增加敏感动作关键词:
   - 退费: 0.7
   - 退款: 0.65
   - 撤销: 0.6

**说明**: `compliance_guard.py` 已支持 bank/securities/fund 三个行业规则，直接复用

---

## 三、Skill 进化参数调整

### 3.1 schema.py — 默认值修改

**文件**: `fincat/config/schema.py`

| 配置项 | 原值 | 新值 | 原因 |
|--------|------|------|------|
| `skill_staleness_days` | 30 | 14 | 客服知识更新更频繁 |
| `skill_min_quality_threshold` | 0.2 | 0.3 | 客服回答准确性要求更高 |

### 3.2 config.json — 同步更新

**文件**: `c:\Users\lyw\.fincat\config.json`

| 配置项 | 原值 | 新值 |
|--------|------|------|
| `skillStalenessDays` | 30 | 14 |
| `skillMinQualityThreshold` | 0.2 | 0.3 |

---

## 四、保留未改的配置

| 配置项 | 值 | 原因 |
|--------|------|------|
| `dream.intervalH` | 2 | 轻量化设计符合定位 |
| `autoSkillGeneration` | true | 自进化逻辑可复用为"客服知识库自进化" |
| `model` | MiniMax-M2.7 | 客服场景足够 |
| `temperature` | 0.1 | 保持稳定输出 |

---

## 五、验证清单

- [ ] 启动 agent，发送测试消息验证人格是否正确切换为客服
- [ ] 测试合规词触发（"保本""稳赚"）是否正确拦截/改写
- [ ] 测试情绪风险评分是否正常工作
- [ ] 测试 skill 进化是否正常工作
- [ ] 验证多渠道（telegram/dingtalk）消息格式适配

---

## 六、后续建议

1. **合规规则扩展**: 考虑增加"投诉处理"相关敏感词（退费、投诉、举报）
2. **客服知识库**: 建立常见问题 FAQ skills
3. **个性化记忆**: 增加用户偏好、常见问题类型记录
4. ** escalation 流程**: 完善转人工的标准和流程
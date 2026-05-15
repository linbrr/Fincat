# nano-forge: 超轻量级个人 AI Agent 框架

<div align="center">
  <img src="fincat_logo.png" alt="nano-forge" width="400">
  <p>
    <a href="https://pypi.org/project/fincat-ai/"><img src="https://img.shields.io/pypi/v/fincat-ai" alt="PyPI"></a>
    <a href="https://pepy.tech/project/fincat-ai"><img src="https://static.pepy.tech/badge/fincat-ai" alt="Downloads"></a>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <a href="https://github.com/HKUDS/fincat"><img src="https://img.shields.io/badge/github-HKUDS/fincat-blue?style=flat&logo=github&logoColor=white" alt="GitHub"></a>
  </p>
</div>

## 项目简介

**nano-forge** 是一个受 [OpenClaw](https://github.com/openclaw/openclaw) 启发设计的超轻量级个人 AI Agent 框架，核心理念是：**用最少的代码实现最核心的 Agent 功能**。

项目亮点：
- 支持 **12+ 主流聊天平台** 一键接入
- 支持 **20+ LLM Provider**（OpenAI、Claude、DeepSeek、Qwen、Moonshot 等）
- 内置 **14+ 可扩展 Skills**，支持 Agent 自主学习和演化
- MIT 开源协议，代码简洁易懂，适合学术研究和二次开发

---

## 核心技术指标

| 指标 | 数值 | 说明 |
|------|------|------|
| 核心代码行数 | ~10,000 行 | 不含测试、文档、构建脚本 |
| 支持聊天平台 | 12+ | Telegram、Discord、WhatsApp、飞书等 |
| 支持 LLM Provider | 20+ | 覆盖国内外主流模型 |
| 内置 Skills | 14+ | 金融分析、GitHub、天气、日程等 |
| Python 版本 | 3.11+ | 现代 Python 语法 |
| 测试覆盖率 | 持续集成 | pytest + pytest-cov |

---

## 技术栈

### 语言

| 语言 | 用途 |
|------|------|
| **Python 3.11+** | 核心业务逻辑、Agent、工具、渠道集成 |
| **TypeScript/Node.js** | WhatsApp Bridge（桥接层） |

### AI / Agent 框架

| 技术 | 说明 |
|------|------|
| `anthropic` | Claude API 官方 SDK |
| `openai` | OpenAI GPT 系列 API |
| `pydantic` / `pydantic-settings` | 数据模型验证与配置管理 |
| `mcp` | Model Context Protocol，支持第三方工具服务器接入 |

### Web / 网络通信

| 技术 | 说明 |
|------|------|
| `websockets` / `websocket-client` | WebSocket 实时通信 |
| `httpx` | 异步 HTTP 客户端 |
| `python-socketio` | Socket.IO 协议支持 |
| `dingtalk-stream` | 钉钉 Stream 模式 SDK |
| `lark-oapi` | 飞书/ Lark 开放平台 SDK |
| `slack-sdk` | Slack 平台 SDK |
| `qq-botpy` | QQ 机器人 SDK |
| `matrix-nio` | Matrix/Element 协议实现 |

### CLI / 用户交互

| 技术 | 说明 |
|------|------|
| `typer` | 现代 CLI 应用框架（基于 type hints） |
| `rich` | 终端富文本渲染与进度条 |
| `prompt-toolkit` / `questionary` | 交互式命令行组件 |
| `loguru` | 简洁强大的日志库 |

### 数据处理 / 文档解析

| 技术 | 说明 |
|------|------|
| `pandas` | 数据分析 |
| `akshare` | 金融数据（股票、宏观经济） |
| `pypdf` | PDF 解析 |
| `python-docx` | Word 文档解析 |
| `openpyxl` | Excel 文件解析 |
| `python-pptx` | PowerPoint 解析 |
| `readability-lxml` | 网页内容提取 |
| `jinja2` | 模板引擎 |
| `tiktoken` | OpenAI token 计费工具 |

### 系统工具

| 技术 | 说明 |
|------|------|
| `dulwich` | 纯 Python Git 实现 |
| `croniter` | Cron 表达式解析 |
| `filelock` | 文件锁 |
| `chardet` | 字符编码检测 |
| `json-repair` | 损坏 JSON 修复 |

### 构建 / 测试 / 质量

| 技术 | 说明 |
|------|------|
| **hatchling** | 现代 Python 包构建系统 |
| **pytest** / **pytest-asyncio** / **pytest-cov** | 测试框架与覆盖率 |
| **ruff** | 超快速 Python linter（E, F, I, N, W） |
| **uv** |极速 Python 包管理器 |

### 容器化 / DevOps

| 技术 | 说明 |
|------|------|
| **Docker** / **docker-compose** | 容器化部署 |
| **GitHub Actions** | 持续集成/持续部署 |
| **Bubblewrap** | Linux 沙箱隔离（可选） |

---

## 架构设计

```
┌─────────────────────────────────────────────────────────────────┐
│                        fincat 架构                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     │
│  │   Channels   │     │   Providers  │     │    Skills    │     │
│  │   渠道接入层   │     │   模型供给层   │     │   技能系统    │     │
│  ├──────────────┤     ├──────────────┤     ├──────────────┤     │
│  │ Telegram     │     │ OpenAI       │     │ github       │     │
│  │ Discord      │     │ Anthropic    │     │ weather      │     │
│  │ WhatsApp     │     │ DeepSeek     │     │ summarize    │     │
│  │ WeChat       │     │ DashScope    │     │ cron         │     │
│  │ Feishu       │     │ Moonshot     │     │ trading      │     │
│  │ DingTalk     │     │ Zhipu        │     │ stock-analysis│     │
│  │ QQ           │     │ Ollama (本地) │     │ ...         │     │
│  │ Slack        │     │ vLLM (本地)   │     │              │     │
│  │ ...          │     │ ...          │     │              │     │
│  └──────────────┘     └──────────────┘     └──────────────┘     │
│           │                  │                   │               │
│           └──────────────────┼───────────────────┘               │
│                              │                                     │
│  ┌───────────────────────────▼────────────────────────────────┐  │
│  │                      Agent Core (核心 Agent)                │  │
│  ├─────────────────────────────────────────────────────────────┤  │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐       │  │
│  │  │  Loop   │  │ Context │  │ Memory  │  │Evolver  │       │  │
│  │  │ Agent循环 │  │ 上下文   │  │ 记忆系统  │  │ 技能演化  │       │  │
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘       │  │
│  │                                                            │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │                    Built-in Tools                    │  │  │
│  │  │  filesystem │ shell │ web │ cron │ message │ spawn  │  │  │
│  │  │  mcp │ akshare │ skill_manage │ trading │ ...      │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│  ┌───────────────────────────▼────────────────────────────────┐  │
│  │                     Session / Memory                         │  │
│  │  history.jsonl │ SOUL.md │ USER.md │ GitStore (版本化记忆)   │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 核心模块说明

#### 1. Agent Loop (`agent/loop.py`)
LLM 与工具执行的循环大脑。采用 **ReAct 模式**：
1. LLM 生成推理和工具调用
2. 工具执行结果反馈给 LLM
3. 循环直到任务完成或达到最大轮次

#### 2. Context Builder (`agent/context.py`)
动态构建 Prompt，包含：
- 系统提示词
- 可用工具列表
- Skill 摘要
- Memory 上下文
- Session 历史

#### 3. Memory System (`agent/memory.py` / `agent/memory_manager.py`)
**分层记忆架构**：
- **短期记忆**: `session.messages` — 当前对话上下文
- **中期记忆**: `memory/history.jsonl` — 追加式摘要历史
- **长期记忆**: `SOUL.md`, `USER.md`, `memory/MEMORY.md` — Dream 机制管理的持久化知识
- **版本控制**: `GitStore` — 基于 Git 的记忆版本化管理

**Dream 机制**: 定时任务自动整理记忆，将重要信息从对话历史提取到长期记忆。

#### 4. Skill Evolution (`agent/skill_evolver.py`)
Agent 可根据任务模式**自主创建、更新、合并 Skills**：
- **Phase 1**: LLM 判断是否值得保存
- **Phase 2**: 调用 `skill_manage` 工具执行 create/patch/edit/delete
- 支持原子写入和失败回滚

#### 5. Auto-Compact (`agent/autocompact.py`)
用户空闲超过阈值后，自动压缩旧上下文为摘要，减少 token 消耗和延迟。

#### 6. Built-in Tools (`agent/tools/`)
| 工具 | 功能 |
|------|------|
| `filesystem` | 文件读写、glob、grep |
| `shell` | 执行 Shell 命令（可选沙箱） |
| `web` | 搜索和网页内容抓取 |
| `mcp` | 接入 MCP 工具服务器 |
| `cron` | 定时任务调度 |
| `message` | 跨渠道消息推送 |
| `spawn` | 启动子 Agent |
| `akshare` | 股票行情、K线、财务数据 |
| `skill_manage` | 创建/编辑/删除 Skills |
| `notebook` | Jupyter 笔记执行 |

#### 7. Channel Manager (`channels/`)
统一的消息接入层，支持 12+ 平台，每种渠道通过 WebSocket 或长轮询保持连接。

#### 8. Provider Registry (`providers/registry.py`)
LLM Provider 注册中心，新增 Provider 仅需两步配置，无需修改业务代码。

---

## 主要功能特性

### 多平台接入
| 平台 | 连接方式 | 认证方式 |
|------|----------|----------|
| Telegram | Bot API | Token |
| Discord | Bot API | Token + Intent |
| WhatsApp | Baileys (QR) | QR Code |
| 微信 | ilinkai API | QR Code |
| 飞书 | WebSocket | App ID/Secret |
| 钉钉 | Stream Mode | App Key/Secret |
| QQ | WebSocket | App ID/Secret |
| Slack | Socket Mode | Bot Token |
| Matrix | WebSocket | 密码登录 + E2EE |
| Email | IMAP/SMTP | App Password |
| 企业微信 | WebSocket | Bot ID/Secret |
| Mochat | Socket.IO | Token |

### 丰富的 Skills 生态
```
skills/
├── github/          # GitHub 操作
├── weather/         # 天气预报
├── summarize/       # 文本摘要
├── tmux/           # tmux 会话管理
├── memory/         # 记忆管理
├── skill-creator/  # 技能创建助手
├── cron/           # 定时任务
├── earnings-analysis/      # 财报分析
├── initiating-coverage/    # 首次覆盖
├── macro-overview/         # 宏观概览
├── morning-note/          # 晨会纪要
├── sector-analysis/       # 板块分析
├── stock-analysis/        # 股票分析
└── valuation/            # 估值分析
```

### 金融市场数据集成
通过 `akshare` 集成 A 股、期货、宏观经济数据，配合 Skills 实现：
- 股票技术分析
- 财报解读
- 板块轮动分析
- 晨会自动化

---

## 快速开始

### 安装

```bash
# 从 PyPI 安装（稳定版）
pip install fincat-ai

# 或使用 uv（推荐，更快）
uv tool install fincat-ai

# 从源码安装（最新功能）
git clone https://github.com/HKUDS/fincat.git
cd fincat
pip install -e .
```

### 配置

```bash
# 初始化配置和工作目录
fincat onboard
```

编辑 `~/.fincat/config.json`，配置 LLM Provider：

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-5",
      "provider": "openrouter"
    }
  }
}
```

### 运行

```bash
# 命令行对话
uv run fincat agent

# 启动网关（连接聊天平台）
uv run fincat gateway

# 查看状态
uv run fincat status

uv run fincat chat
```



### Docker 部署

```bash
# 首次初始化
docker compose run --rm fincat-cli onboard

# 编辑配置
vim ~/.fincat/config.json

# 启动网关
docker compose up -d fincat-gateway
```

---

## 工程实践亮点

### 1. 极简依赖管理
使用 `hatchling` 作为构建系统，`uv` 作为包管理器，依赖清晰，版本锁定。

### 2. 完善的测试覆盖
- `pytest` + `pytest-asyncio` 支持异步测试
- `pytest-cov` 持续监控覆盖率
- `ruff` 保证代码风格一致

### 3. 安全的沙箱执行
可选的 `bubblewrap` 沙箱将 Shell 执行限制在指定目录内，防止恶意操作。

### 4. 多实例隔离
通过 `--config` 和 `--workspace` 参数，可同时运行多个互不干扰的 fincat 实例。

### 5. 环境变量 secrets
支持 `${VAR_NAME}` 语法从环境变量读取敏感信息，配合 systemd `EnvironmentFile` 实现安全部署。

---

## 项目结构

```
fincat/
├── agent/                 # 核心 Agent 逻辑
│   ├── loop.py           #    LLM ↔ Tools 执行循环
│   ├── context.py        #    Prompt 构建器
│   ├── memory.py         #    持久化记忆
│   ├── skill_evolver.py  #    技能自主演化
│   ├── autocompact.py    #    上下文自动压缩
│   └── tools/            #    内置工具集
├── channels/              # 聊天平台集成 (12+)
├── providers/            # LLM Provider (20+)
├── skills/                # 可扩展 Skills (14+)
├── session/              # 会话管理
├── cron/                 # 定时任务
├── heartbeat/            # 心跳主动唤醒
├── bus/                  # 消息路由
├── config/               # 配置管理
├── api/                  # OpenAI 兼容 API
├── cli/                  # CLI 命令
├── bridge/               # WhatsApp Bridge (TypeScript)
├── tests/                # 测试套件
├── pyproject.toml        # 项目配置
└── Dockerfile            # 容器化
```

---

## 使用示例

### Python SDK

```python
from fincat import Fincat

bot = Fincat.from_config()
result = await bot.run("分析一下腾讯最近的股价走势")
print(result.content)
```

### In-Chat 命令

| 命令 | 说明 |
|------|------|
| `/new` | 开始新对话 |
| `/stop` | 停止当前任务 |
| `/dream` | 手动触发 Dream 记忆整理 |
| `/dream-log` | 查看记忆变更记录 |
| `/dream-restore <sha>` | 恢复到指定版本 |

### OpenAI 兼容 API

```bash
curl http://127.0.0.1:8900/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "分析今日大盘"}],
    "session_id": "my-session"
  }'
```

---

## 开源协议

MIT License - 可自由使用、修改和分发。

---

## 相关链接

- GitHub: https://github.com/HKUDS/fincat
- PyPI: https://pypi.org/project/fincat-ai/
- 文档: https://fincat.wiki/docs/

> nano-forge 仅用于教育、研究和技术交流目的，与加密货币无关，不涉及任何官方代币或硬币。

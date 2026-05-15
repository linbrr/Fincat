<div align="center">
  <img src="fincat_logo.png" alt="nano-forge" width="500">
  <h1>nano-forge: Ultra-Lightweight Personal AI Agent</h1>
  <p>
    <a href="https://pypi.org/project/fincat-ai/"><img src="https://img.shields.io/pypi/v/fincat-ai" alt="PyPI"></a>
    <a href="https://pepy.tech/project/fincat-ai"><img src="https://static.pepy.tech/badge/fincat-ai" alt="Downloads"></a>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <a href="https://fincat.wiki/docs/0.1.5/getting-started/fincat-overview"><img src="https://img.shields.io/badge/Docs-fincat.wiki-blue?style=flat&logo=readthedocs&logoColor=white" alt="Docs"></a>
    <a href="./COMMUNICATION.md"><img src="https://img.shields.io/badge/Feishu-Group-E9DBFC?style=flat&logo=feishu&logoColor=white" alt="Feishu"></a>
    <a href="./COMMUNICATION.md"><img src="https://img.shields.io/badge/WeChat-Group-C5EAB4?style=flat&logo=wechat&logoColor=white" alt="WeChat"></a>
    <a href="https://discord.gg/MnCvHqpUGB"><img src="https://img.shields.io/badge/Discord-Community-5865F2?style=flat&logo=discord&logoColor=white" alt="Discord"></a>
  </p>
</div>

🐈 **nano-forge** is an **ultra-lightweight** personal AI agent inspired by [OpenClaw](https://github.com/openclaw/openclaw).

⚡️ Delivers core agent functionality with **99% fewer lines of code**.

📏 Real-time line count: run `bash core_agent_lines.sh` to verify anytime.

## 更新日志

- **2026-05-03** 🧠 **记忆系统全面升级** — 新增 `memory_item.py`（原子记忆单元 + 衰减/频率/置信度 + JSONL存储）、`memory_monitor.py`（Trigger & Monitor 主动服务引擎，5类触发器）；`MemoryStore` 改造为支持 4 个分类 Markdown 文件（preference/knowledge/case/compliance）；Dream Phase 1 新增结构化输出 + LLM fallback 自动提取 MemoryItem；`ContextBuilder` 改为注入分类摘要而非全量 MEMORY.md。详见 [key-changes/MEMORY_SYSTEM_UPGRADE_MEMU_DESIGN_2026-05-03.md](fincat/key-changes/MEMORY_SYSTEM_UPGRADE_MEMU_DESIGN_2026-05-03.md)。
- **2026-05-01** 🔄 **定位修改：量化交易助手 → 金融智能客服** — 重写 SOUL.md（客服人格 + 合规优先）、AGENTS.md（单客服架构）、identity.md；更新风控评分权重；调整 skill 参数阈值。详见 [key-changes/CUSTOMER_SERVICE_SCENARIO_REFRESH_2026-05-01.md](fincat/key-changes/CUSTOMER_SERVICE_SCENARIO_REFRESH_2026-05-01.md)。
- **2026-05-01** 💾 **COGNIX 混合存储架构设计** — 设计 Markdown（唯一真相源）+ SQLite（索引层）+ JSONL（会话存储）三层架构，金融场景专属 5 类分类（user/market/research/regulation）。详见 [key-changes/COGNIX_MIXED_STORAGE_ARCHITECTURE_UPGRADE_2026-05-01.md](fincat/key-changes/COGNIX_MIXED_STORAGE_ARCHITECTURE_UPGRADE_2026-05-01.md)。
- **2026-04-25** 🔄 **跨轮上下文召回机制** — 新增 MemoryManager + MemoryProvider 架构，skill 被创建/更新后，下一轮对话可通过 `prefetch_all()` 感知"上轮创建了 xxx skill"。参考 [key-changes/CROSS_TURN_CONTEXT_AND_MEMORY召回机制.md](fincat/key-changes/CROSS_TURN_CONTEXT_AND_MEMORY召回机制.md)。
- **2026-04-24** 🧠 **SkillEvolver 全面重构** — 重写判断逻辑，Phase 1 由 LLM 判断是否值得保存，Phase 2 调用 skill_manage 执行 create/patch/edit/delete；新增 edit action、yaml.safe_load frontmatter 校验、原子写入、失败回滚等。详见 [key-changes/SKILL_EVOLUTION_SYSTEM.md](fincat/key-changes/SKILL_EVOLUTION_SYSTEM.md)。
- **2026-04-23** 📋 **Skill 生命周期管理设计文档** — 分析 Hermes 与 fincat 的 skill 管理差异，设计 stale 检测、merge 候选、180天清理等生命周期策略。详见 [key-changes/SKILL_LIFECYCLE_AND_HERMES.md](fincat/key-changes/SKILL_LIFECYCLE_AND_HERMES.md)。

> 🐈 nano-forge is for educational, research, and technical exchange purposes only. It is unrelated to crypto and does not involve any official token or coin.

## Key Features of fincat:

🪶 **Ultra-Lightweight**: A lightweight implementation built for stable, long-running AI agents.

🔬 **Research-Ready**: Clean, readable code that's easy to understand, modify, and extend for research.

⚡️ **Lightning Fast**: Minimal footprint means faster startup, lower resource usage, and quicker iterations.

💎 **Easy-to-Use**: One-click to deploy and you're ready to go.

## 🏗️ Architecture

<p align="center">
  <img src="fincat_arch.png" alt="fincat architecture" width="800">
</p>

## 🤖 Multi-Agent Framework

fincat uses a **Master Agent + Background Subagent** architecture. The main agent handles user conversations in a ReAct loop, and can spawn isolated background subagents for time-consuming tasks.

### Architecture Overview

```
User Message
    │
    ▼
┌─────────────────────────────────────────────────────┐
│              Master Agent (AgentLoop)                │
│                                                      │
│  ContextBuilder → LLM → Tool Execution → Response    │
│                                                      │
│  Tools: read_file, write_file, exec, web_search,     │
│         message, spawn, cron, akshare, rag ...        │
└──────────┬───────────────────────────────────────────┘
           │
           │ spawn tool (async, non-blocking)
           ▼
┌─────────────────────────────────────────────────────┐
│          SubagentManager (Background)                │
│                                                      │
│  ┌───────────────┐  ┌───────────────┐               │
│  │ Subagent [a1] │  │ Subagent [b2] │  ...          │
│  │ (independent  │  │ (independent  │               │
│  │  ReAct loop)  │  │  ReAct loop)  │               │
│  └───────┬───────┘  └───────┬───────┘               │
│          │                  │                        │
│          └──────┬───────────┘                        │
│                 │                                    │
│     bus.publish_inbound()                            │
│     (result injected back to master)                 │
└─────────────────────────────────────────────────────┘
```

### How Subagents Work

**Creation**: The master agent calls the `spawn` tool when it identifies a task that is complex, time-consuming, or independent. `SubagentManager.spawn()` creates an async background task and returns immediately — the master continues chatting with the user without blocking.

**Context**: Subagents receive a minimal system prompt — only the current time, workspace path, and a skill summary. They have **no** conversation history, user profile, memory, or financial context. The first user message is the task description itself.

**Tools**: Subagents have a restricted toolset:

| Available | Not Available |
|-----------|---------------|
| `read_file` / `write_file` / `edit_file` | `spawn` (no nesting) |
| `list_dir` / `glob` / `grep` | `message` (no direct user contact) |
| `exec` (shell commands) | `cron` |
| `web_search` / `web_fetch` | |

**Communication**: Subagents cannot talk to users directly. When a subagent completes (or fails), it publishes the result to the `MessageBus` as a system message:

```
SubagentManager._announce_result()
    → InboundMessage(channel="system", sender_id="subagent")
    → bus.publish_inbound()
    → Master Agent's run() loop consumes it
    → Master summarizes the result for the user
```

The announce template instructs the master: *"Summarize this naturally for the user. Keep it brief (1-2 sentences). Do not mention technical details like 'subagent' or task IDs."*

**Constraints**:
- Max **15 iterations** per subagent (vs. 50 for master)
- `fail_on_tool_error=True` — any tool error terminates the subagent immediately
- Subagents are tracked per session; master can cancel all subagents for a session via `cancel_by_session()`
- Each subagent gets a short UUID (e.g. `7aaac441`) for logging and tracking

### Key Source Files

| File | Role |
|------|------|
| `agent/loop.py` | Master agent loop — message dispatch, tool registration, session management |
| `agent/runner.py` | Shared ReAct execution engine (`AgentRunner.run()`) used by both master and subagents |
| `agent/subagent.py` | `SubagentManager` — spawn, execute, announce, lifecycle management |
| `agent/tools/spawn.py` | `SpawnTool` — the tool the master calls to create a subagent |
| `agent/context.py` | `ContextBuilder` — builds system prompt with memory, skills, user profile |
| `templates/agent/subagent_system.md` | Subagent system prompt template |
| `templates/agent/subagent_announce.md` | Result announcement template |

## Table of Contents

- [News](#-news)
- [Key Features](#key-features-of-fincat)
- [Architecture](#️-architecture)
- [Multi-Agent Framework](#-multi-agent-framework)
- [Features](#-features)
- [Install](#-install)
- [Quick Start](#-quick-start)
- [Chat Apps](#-chat-apps)
- [Agent Social Network](#-agent-social-network)
- [Configuration](#️-configuration)
- [Multiple Instances](#-multiple-instances)
- [Memory](#-memory)
- [CLI Reference](#-cli-reference)
- [In-Chat Commands](#-in-chat-commands)
- [Python SDK](#-python-sdk)
- [OpenAI-Compatible API](#-openai-compatible-api)
- [Docker](#-docker)
- [Linux Service](#-linux-service)
- [Project Structure](#-project-structure)
- [Contribute & Roadmap](#-contribute--roadmap)
- [Star History](#-star-history)

## ✨ Features

<table align="center">
  <tr align="center">
    <th><p align="center">📈 24/7 Real-Time Market Analysis</p></th>
    <th><p align="center">🚀 Full-Stack Software Engineer</p></th>
    <th><p align="center">📅 Smart Daily Routine Manager</p></th>
    <th><p align="center">📚 Personal Knowledge Assistant</p></th>
  </tr>
  <tr>
    <td align="center"><p align="center"><img src="case/search.gif" width="180" height="400"></p></td>
    <td align="center"><p align="center"><img src="case/code.gif" width="180" height="400"></p></td>
    <td align="center"><p align="center"><img src="case/schedule.gif" width="180" height="400"></p></td>
    <td align="center"><p align="center"><img src="case/memory.gif" width="180" height="400"></p></td>
  </tr>
  <tr>
    <td align="center">Discovery • Insights • Trends</td>
    <td align="center">Develop • Deploy • Scale</td>
    <td align="center">Schedule • Automate • Organize</td>
    <td align="center">Learn • Memory • Reasoning</td>
  </tr>
</table>

## 📦 Install

> [!IMPORTANT]
> This README may describe features that are available first in the latest source code.
> If you want the newest features and experiments, install from source.
> If you want the most stable day-to-day experience, install from PyPI or with `uv`.

**Install from source** (latest features, experimental changes may land here first; recommended for development)

```bash
git clone https://github.com/HKUDS/fincat.git
cd fincat
pip install -e .
```

**Install with [uv](https://github.com/astral-sh/uv)** (stable release, fast)

```bash
uv tool install fincat-ai
```

**Install from PyPI** (stable release)

```bash
pip install fincat-ai
```

### Update to latest version

**PyPI / pip**

```bash
pip install -U fincat-ai
fincat --version
```

**uv**

```bash
uv tool upgrade fincat-ai
fincat --version
```

**Using WhatsApp?** Rebuild the local bridge after upgrading:

```bash
rm -rf ~/.fincat/bridge
fincat channels login whatsapp
```

## 🚀 Quick Start

> [!TIP]
> Set your API key in `~/.fincat/config.json`.
> Get API keys: [OpenRouter](https://openrouter.ai/keys) (Global)
>
> For other LLM providers, please see the [Providers](#providers) section.
>
> For web search capability setup, please see [Web Search](#web-search).

**1. Initialize**

```bash
fincat onboard
```

Use `fincat onboard --wizard` if you want the interactive setup wizard.

**2. Configure** (`~/.fincat/config.json`)

Configure these **two parts** in your config (other options have defaults).

*Set your API key* (e.g. OpenRouter, recommended for global users):
```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  }
}
```

*Set your model* (optionally pin a provider — defaults to auto-detection):
```json
{
  "agents": {
    "defaults": {
      "model": "anthropic/claude-opus-4-5",
      "provider": "openrouter"
    }
  }
}
```

**3. Chat**

```bash
fincat agent
```

That's it! You have a working AI agent in 2 minutes.

## 💬 Chat Apps

Connect fincat to your favorite chat platform. Want to build your own? See the [Channel Plugin Guide](./docs/CHANNEL_PLUGIN_GUIDE.md).

| Channel | What you need |
|---------|---------------|
| **Telegram** | Bot token from @BotFather |
| **Discord** | Bot token + Message Content intent |
| **WhatsApp** | QR code scan (`fincat channels login whatsapp`) |
| **WeChat (Weixin)** | QR code scan (`fincat channels login weixin`) |
| **Feishu** | App ID + App Secret |
| **DingTalk** | App Key + App Secret |
| **Slack** | Bot token + App-Level token |
| **Matrix** | Homeserver URL + Access token |
| **Email** | IMAP/SMTP credentials |
| **QQ** | App ID + App Secret |
| **Wecom** | Bot ID + Bot Secret |
| **Mochat** | Claw token (auto-setup available) |

<details>
<summary><b>Telegram</b> (Recommended)</summary>

**1. Create a bot**
- Open Telegram, search `@BotFather`
- Send `/newbot`, follow prompts
- Copy the token

**2. Configure**

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"]
    }
  }
}
```

> You can find your **User ID** in Telegram settings. It is shown as `@yourUserId`.
> Copy this value **without the `@` symbol** and paste it into the config file.


**3. Run**

```bash
fincat gateway
```

</details>

<details>
<summary><b>Mochat (Claw IM)</b></summary>

Uses **Socket.IO WebSocket** by default, with HTTP polling fallback.

**1. Ask fincat to set up Mochat for you**

Simply send this message to fincat (replace `xxx@xxx` with your real email):

```
Read https://raw.githubusercontent.com/HKUDS/MoChat/refs/heads/main/skills/fincat/skill.md and register on MoChat. My Email account is xxx@xxx Bind me as your owner and DM me on MoChat.
```

fincat will automatically register, configure `~/.fincat/config.json`, and connect to Mochat.

**2. Restart gateway**

```bash
fincat gateway
```

That's it — fincat handles the rest!

<br>

<details>
<summary>Manual configuration (advanced)</summary>

If you prefer to configure manually, add the following to `~/.fincat/config.json`:

> Keep `claw_token` private. It should only be sent in `X-Claw-Token` header to your Mochat API endpoint.

```json
{
  "channels": {
    "mochat": {
      "enabled": true,
      "base_url": "https://mochat.io",
      "socket_url": "https://mochat.io",
      "socket_path": "/socket.io",
      "claw_token": "claw_xxx",
      "agent_user_id": "6982abcdef",
      "sessions": ["*"],
      "panels": ["*"],
      "reply_delay_mode": "non-mention",
      "reply_delay_ms": 120000
    }
  }
}
```



</details>

</details>

<details>
<summary><b>Discord</b></summary>

**1. Create a bot**
- Go to https://discord.com/developers/applications
- Create an application → Bot → Add Bot
- Copy the bot token

**2. Enable intents**
- In the Bot settings, enable **MESSAGE CONTENT INTENT**
- (Optional) Enable **SERVER MEMBERS INTENT** if you plan to use allow lists based on member data

**3. Get your User ID**
- Discord Settings → Advanced → enable **Developer Mode**
- Right-click your avatar → **Copy User ID**

**4. Configure**

```json
{
  "channels": {
    "discord": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"],
      "groupPolicy": "mention",
      "streaming": true
    }
  }
}
```

> `groupPolicy` controls how the bot responds in group channels:
> - `"mention"` (default) — Only respond when @mentioned
> - `"open"` — Respond to all messages
> DMs always respond when the sender is in `allowFrom`.
> - If you set group policy to open create new threads as private threads and then @ the bot into it. Otherwise the thread itself and the channel in which you spawned it will spawn a bot session.
> `streaming` defaults to `true`. Disable it only if you explicitly want non-streaming replies.

**5. Invite the bot**
- OAuth2 → URL Generator
- Scopes: `bot`
- Bot Permissions: `Send Messages`, `Read Message History`
- Open the generated invite URL and add the bot to your server

**6. Run**

```bash
fincat gateway
```

</details>

<details>
<summary><b>Matrix (Element)</b></summary>

Install Matrix dependencies first:

```bash
pip install fincat-ai[matrix]
```

**1. Create/choose a Matrix account**

- Create or reuse a Matrix account on your homeserver (for example `matrix.org`).
- Confirm you can log in with Element.

**2. Get credentials**

- You need:
  - `userId` (example: `@fincat:matrix.org`)
  - `password`

(Note: `accessToken` and `deviceId` are still supported for legacy reasons, but
for reliable encryption, password login is recommended instead. If the
`password` is provided, `accessToken` and `deviceId` will be ignored.)

**3. Configure**

```json
{
  "channels": {
    "matrix": {
      "enabled": true,
      "homeserver": "https://matrix.org",
      "userId": "@fincat:matrix.org",
      "password": "mypasswordhere",
      "e2eeEnabled": true,
      "allowFrom": ["@your_user:matrix.org"],
      "groupPolicy": "open",
      "groupAllowFrom": [],
      "allowRoomMentions": false,
      "maxMediaBytes": 20971520
    }
  }
}
```

> Keep a persistent `matrix-store` — encrypted session state is lost if these change across restarts.

| Option | Description |
|--------|-------------|
| `allowFrom` | User IDs allowed to interact. Empty denies all; use `["*"]` to allow everyone. |
| `groupPolicy` | `open` (default), `mention`, or `allowlist`. |
| `groupAllowFrom` | Room allowlist (used when policy is `allowlist`). |
| `allowRoomMentions` | Accept `@room` mentions in mention mode. |
| `e2eeEnabled` | E2EE support (default `true`). Set `false` for plaintext-only. |
| `maxMediaBytes` | Max attachment size (default `20MB`). Set `0` to block all media. |




**4. Run**

```bash
fincat gateway
```

</details>

<details>
<summary><b>WhatsApp</b></summary>

Requires **Node.js ≥18**.

**1. Link device**

```bash
fincat channels login whatsapp
# Scan QR with WhatsApp → Settings → Linked Devices
```

**2. Configure**

```json
{
  "channels": {
    "whatsapp": {
      "enabled": true,
      "allowFrom": ["+1234567890"]
    }
  }
}
```

**3. Run** (two terminals)

```bash
# Terminal 1
fincat channels login whatsapp

# Terminal 2
fincat gateway
```

> WhatsApp bridge updates are not applied automatically for existing installations.
> After upgrading fincat, rebuild the local bridge with:
> `rm -rf ~/.fincat/bridge && fincat channels login whatsapp`

</details>

<details>
<summary><b>Feishu</b></summary>

Uses **WebSocket** long connection — no public IP required.

**1. Create a Feishu bot**
- Visit [Feishu Open Platform](https://open.feishu.cn/app)
- Create a new app → Enable **Bot** capability
- **Permissions**:
  - `im:message` (send messages) and `im:message.p2p_msg:readonly` (receive messages)
  - **Streaming replies** (default in fincat): add **`cardkit:card:write`** (often labeled **Create and update cards** in the Feishu developer console). Required for CardKit entities and streamed assistant text. Older apps may not have it yet — open **Permission management**, enable the scope, then **publish** a new app version if the console requires it.
  - If you **cannot** add `cardkit:card:write`, set `"streaming": false` under `channels.feishu` (see below). The bot still works; replies use normal interactive cards without token-by-token streaming.
- **Events**: Add `im.message.receive_v1` (receive messages)
  - Select **Long Connection** mode (requires running fincat first to establish connection)
- Get **App ID** and **App Secret** from "Credentials & Basic Info"
- Publish the app

**2. Configure**

```json
{
  "channels": {
    "feishu": {
      "enabled": true,
      "appId": "cli_xxx",
      "appSecret": "xxx",
      "encryptKey": "",
      "verificationToken": "",
      "allowFrom": ["ou_YOUR_OPEN_ID"],
      "groupPolicy": "mention",
      "reactEmoji": "OnIt",
      "doneEmoji": "DONE",
      "toolHintPrefix": "🔧",
      "streaming": true,
      "domain": "feishu"
    }
  }
}
```

> `streaming` defaults to `true`. Use `false` if your app does not have **`cardkit:card:write`** (see permissions above).
> `encryptKey` and `verificationToken` are optional for Long Connection mode.
> `allowFrom`: Add your open_id (find it in fincat logs when you message the bot). Use `["*"]` to allow all users.
> `groupPolicy`: `"mention"` (default — respond only when @mentioned), `"open"` (respond to all group messages). Private chats always respond.
> `reactEmoji`: Emoji for "processing" status (default: `OnIt`). See [available emojis](https://open.larkoffice.com/document/server-docs/im-v1/message-reaction/emojis-introduce).
> `doneEmoji`: Optional emoji for "completed" status (e.g., `DONE`, `OK`, `HEART`). When set, bot adds this reaction after removing `reactEmoji`.
> `toolHintPrefix`: Prefix for inline tool hints in streaming cards (default: `🔧`).
> `domain`: `"feishu"` (default) for China (open.feishu.cn), `"lark"` for international Lark (open.larksuite.com).

**3. Run**

```bash
fincat gateway
```

> [!TIP]
> Feishu uses WebSocket to receive messages — no webhook or public IP needed!

</details>

<details>
<summary><b>QQ (QQ单聊)</b></summary>

Uses **botpy SDK** with WebSocket — no public IP required. Currently supports **private messages only**.

**1. Register & create bot**
- Visit [QQ Open Platform](https://q.qq.com) → Register as a developer (personal or enterprise)
- Create a new bot application
- Go to **开发设置 (Developer Settings)** → copy **AppID** and **AppSecret**

**2. Set up sandbox for testing**
- In the bot management console, find **沙箱配置 (Sandbox Config)**
- Under **在消息列表配置**, click **添加成员** and add your own QQ number
- Once added, scan the bot's QR code with mobile QQ → open the bot profile → tap "发消息" to start chatting

**3. Configure**

> - `allowFrom`: Add your openid (find it in fincat logs when you message the bot). Use `["*"]` for public access.
> - `msgFormat`: Optional. Use `"plain"` (default) for maximum compatibility with legacy QQ clients, or `"markdown"` for richer formatting on newer clients.
> - For production: submit a review in the bot console and publish. See [QQ Bot Docs](https://bot.q.qq.com/wiki/) for the full publishing flow.

```json
{
  "channels": {
    "qq": {
      "enabled": true,
      "appId": "YOUR_APP_ID",
      "secret": "YOUR_APP_SECRET",
      "allowFrom": ["YOUR_OPENID"],
      "msgFormat": "plain"
    }
  }
}
```

**4. Run**

```bash
fincat gateway
```

Now send a message to the bot from QQ — it should respond!

</details>

<details>
<summary><b>DingTalk (钉钉)</b></summary>

Uses **Stream Mode** — no public IP required.

**1. Create a DingTalk bot**
- Visit [DingTalk Open Platform](https://open-dev.dingtalk.com/)
- Create a new app -> Add **Robot** capability
- **Configuration**:
  - Toggle **Stream Mode** ON
- **Permissions**: Add necessary permissions for sending messages
- Get **AppKey** (Client ID) and **AppSecret** (Client Secret) from "Credentials"
- Publish the app

**2. Configure**

```json
{
  "channels": {
    "dingtalk": {
      "enabled": true,
      "clientId": "YOUR_APP_KEY",
      "clientSecret": "YOUR_APP_SECRET",
      "allowFrom": ["YOUR_STAFF_ID"]
    }
  }
}
```

> `allowFrom`: Add your staff ID. Use `["*"]` to allow all users.

**3. Run**

```bash
fincat gateway
```

</details>

<details>
<summary><b>Slack</b></summary>

Uses **Socket Mode** — no public URL required.

**1. Create a Slack app**
- Go to [Slack API](https://api.slack.com/apps) → **Create New App** → "From scratch"
- Pick a name and select your workspace

**2. Configure the app**
- **Socket Mode**: Toggle ON → Generate an **App-Level Token** with `connections:write` scope → copy it (`xapp-...`)
- **OAuth & Permissions**: Add bot scopes: `chat:write`, `reactions:write`, `app_mentions:read`
- **Event Subscriptions**: Toggle ON → Subscribe to bot events: `message.im`, `message.channels`, `app_mention` → Save Changes
- **App Home**: Scroll to **Show Tabs** → Enable **Messages Tab** → Check **"Allow users to send Slash commands and messages from the messages tab"**
- **Install App**: Click **Install to Workspace** → Authorize → copy the **Bot Token** (`xoxb-...`)

**3. Configure fincat**

```json
{
  "channels": {
    "slack": {
      "enabled": true,
      "botToken": "xoxb-...",
      "appToken": "xapp-...",
      "allowFrom": ["YOUR_SLACK_USER_ID"],
      "groupPolicy": "mention"
    }
  }
}
```

**4. Run**

```bash
fincat gateway
```

DM the bot directly or @mention it in a channel — it should respond!

> [!TIP]
> - `groupPolicy`: `"mention"` (default — respond only when @mentioned), `"open"` (respond to all channel messages), or `"allowlist"` (restrict to specific channels).
> - DM policy defaults to open. Set `"dm": {"enabled": false}` to disable DMs.

</details>

<details>
<summary><b>Email</b></summary>

Give fincat its own email account. It polls **IMAP** for incoming mail and replies via **SMTP** — like a personal email assistant.

**1. Get credentials (Gmail example)**
- Create a dedicated Gmail account for your bot (e.g. `my-fincat@gmail.com`)
- Enable 2-Step Verification → Create an [App Password](https://myaccount.google.com/apppasswords)
- Use this app password for both IMAP and SMTP

**2. Configure**

> - `consentGranted` must be `true` to allow mailbox access. This is a safety gate — set `false` to fully disable.
> - `allowFrom`: Add your email address. Use `["*"]` to accept emails from anyone.
> - `smtpUseTls` and `smtpUseSsl` default to `true` / `false` respectively, which is correct for Gmail (port 587 + STARTTLS). No need to set them explicitly.
> - Set `"autoReplyEnabled": false` if you only want to read/analyze emails without sending automatic replies.
> - `allowedAttachmentTypes`: Save inbound attachments matching these MIME types — `["*"]` for all, e.g. `["application/pdf", "image/*"]` (default `[]` = disabled).
> - `maxAttachmentSize`: Max size per attachment in bytes (default `2000000` / 2MB).
> - `maxAttachmentsPerEmail`: Max attachments to save per email (default `5`).

```json
{
  "channels": {
    "email": {
      "enabled": true,
      "consentGranted": true,
      "imapHost": "imap.gmail.com",
      "imapPort": 993,
      "imapUsername": "my-fincat@gmail.com",
      "imapPassword": "your-app-password",
      "smtpHost": "smtp.gmail.com",
      "smtpPort": 587,
      "smtpUsername": "my-fincat@gmail.com",
      "smtpPassword": "your-app-password",
      "fromAddress": "my-fincat@gmail.com",
      "allowFrom": ["your-real-email@gmail.com"],
      "allowedAttachmentTypes": ["application/pdf", "image/*"]
    }
  }
}
```


**3. Run**

```bash
fincat gateway
```

</details>

<details>
<summary><b>WeChat (微信 / Weixin)</b></summary>

Uses **HTTP long-poll** with QR-code login via the ilinkai personal WeChat API. No local WeChat desktop client is required.

**1. Install with WeChat support**

```bash
pip install "fincat-ai[weixin]"
```

**2. Configure**

```json
{
  "channels": {
    "weixin": {
      "enabled": true,
      "allowFrom": ["YOUR_WECHAT_USER_ID"]
    }
  }
}
```

> - `allowFrom`: Add the sender ID you see in fincat logs for your WeChat account. Use `["*"]` to allow all users.
> - `token`: Optional. If omitted, log in interactively and fincat will save the token for you.
> - `routeTag`: Optional. When your upstream Weixin deployment requires request routing, fincat will send it as the `SKRouteTag` header.
> - `stateDir`: Optional. Defaults to fincat's runtime directory for Weixin state.
> - `pollTimeout`: Optional long-poll timeout in seconds.

**3. Login**

```bash
fincat channels login weixin
```

Use `--force` to re-authenticate and ignore any saved token:

```bash
fincat channels login weixin --force
```

**4. Run**

```bash
fincat gateway
```

</details>

<details>
<summary><b>Wecom (企业微信)</b></summary>

> Here we use [wecom-aibot-sdk-python](https://github.com/chengyongru/wecom_aibot_sdk) (community Python version of the official [@wecom/aibot-node-sdk](https://www.npmjs.com/package/@wecom/aibot-node-sdk)).
>
> Uses **WebSocket** long connection — no public IP required.

**1. Install the optional dependency**

```bash
pip install fincat-ai[wecom]
```

**2. Create a WeCom AI Bot**

Go to the WeCom admin console → Intelligent Robot → Create Robot → select **API mode** with **long connection**. Copy the Bot ID and Secret.

**3. Configure**

```json
{
  "channels": {
    "wecom": {
      "enabled": true,
      "botId": "your_bot_id",
      "secret": "your_bot_secret",
      "allowFrom": ["your_id"]
    }
  }
}
```

**4. Run**

```bash
fincat gateway
```

</details>

## 🌐 Agent Social Network

🐈 fincat is capable of linking to the agent social network (agent community). **Just send one message and your fincat joins automatically!**

| Platform | How to Join (send this message to your bot) |
|----------|-------------|
| [**Moltbook**](https://www.moltbook.com/) | `Read https://moltbook.com/skill.md and follow the instructions to join Moltbook` |
| [**ClawdChat**](https://clawdchat.ai/) | `Read https://clawdchat.ai/skill.md and follow the instructions to join ClawdChat` |

Simply send the command above to your fincat (via CLI or any chat channel), and it will handle the rest.

## ⚙️ Configuration

Config file: `~/.fincat/config.json`

> [!NOTE]
> If your config file is older than the current schema, you can refresh it without overwriting your existing values:
> run `fincat onboard`, then answer `N` when asked whether to overwrite the config.
> fincat will merge in missing default fields and keep your current settings.

### Environment Variables for Secrets

Instead of storing secrets directly in `config.json`, you can use `${VAR_NAME}` references that are resolved from environment variables at startup:

```json
{
  "channels": {
    "telegram": { "token": "${TELEGRAM_TOKEN}" },
    "email": {
      "imapPassword": "${IMAP_PASSWORD}",
      "smtpPassword": "${SMTP_PASSWORD}"
    }
  },
  "providers": {
    "groq": { "apiKey": "${GROQ_API_KEY}" }
  }
}
```

For **systemd** deployments, use `EnvironmentFile=` in the service unit to load variables from a file that only the deploying user can read:

```ini
# /etc/systemd/system/fincat.service (excerpt)
[Service]
EnvironmentFile=/home/youruser/fincat_secrets.env
User=fincat
ExecStart=...
```

```bash
# /home/youruser/fincat_secrets.env (mode 600, owned by youruser)
TELEGRAM_TOKEN=your-token-here
IMAP_PASSWORD=your-password-here
```

### Providers

> [!TIP]
> - **Voice transcription**: Voice messages (Telegram, WhatsApp) are automatically transcribed using Whisper. By default Groq is used (free tier). Set `"transcriptionProvider": "openai"` under `channels` to use OpenAI Whisper instead — the API key is picked from the matching provider config.
> - **MiniMax Coding Plan**: Exclusive discount links for the fincat community: [Overseas](https://platform.minimax.io/subscribe/coding-plan?code=9txpdXw04g&source=link) · [Mainland China](https://platform.minimaxi.com/subscribe/token-plan?code=GILTJpMTqZ&source=link)
> - **MiniMax (Mainland China)**: If your API key is from MiniMax's mainland China platform (minimaxi.com), set `"apiBase": "https://api.minimaxi.com/v1"` in your minimax provider config.
> - **VolcEngine / BytePlus Coding Plan**: Use dedicated providers `volcengineCodingPlan` or `byteplusCodingPlan` instead of the pay-per-use `volcengine` / `byteplus` providers.
> - **Zhipu Coding Plan**: If you're on Zhipu's coding plan, set `"apiBase": "https://open.bigmodel.cn/api/coding/paas/v4"` in your zhipu provider config.
> - **Alibaba Cloud BaiLian**: If you're using Alibaba Cloud BaiLian's OpenAI-compatible endpoint, set `"apiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1"` in your dashscope provider config.
> - **Step Fun (Mainland China)**: If your API key is from Step Fun's mainland China platform (stepfun.com), set `"apiBase": "https://api.stepfun.com/v1"` in your stepfun provider config.

| Provider | Purpose | Get API Key |
|----------|---------|-------------|
| `custom` | Any OpenAI-compatible endpoint | — |
| `openrouter` | LLM (recommended, access to all models) | [openrouter.ai](https://openrouter.ai) |
| `volcengine` | LLM (VolcEngine, pay-per-use) | [Coding Plan](https://www.volcengine.com/activity/codingplan?utm_campaign=fincat&utm_content=fincat&utm_medium=devrel&utm_source=OWO&utm_term=fincat) · [volcengine.com](https://www.volcengine.com) |
| `byteplus` | LLM (VolcEngine international, pay-per-use) | [Coding Plan](https://www.byteplus.com/en/activity/codingplan?utm_campaign=fincat&utm_content=fincat&utm_medium=devrel&utm_source=OWO&utm_term=fincat) · [byteplus.com](https://www.byteplus.com) |
| `anthropic` | LLM (Claude direct) | [console.anthropic.com](https://console.anthropic.com) |
| `azure_openai` | LLM (Azure OpenAI) | [portal.azure.com](https://portal.azure.com) |
| `openai` | LLM + Voice transcription (Whisper) | [platform.openai.com](https://platform.openai.com) |
| `deepseek` | LLM (DeepSeek direct) | [platform.deepseek.com](https://platform.deepseek.com) |
| `groq` | LLM + Voice transcription (Whisper, default) | [console.groq.com](https://console.groq.com) |
| `minimax` | LLM (MiniMax direct) | [platform.minimaxi.com](https://platform.minimaxi.com) |
| `gemini` | LLM (Gemini direct) | [aistudio.google.com](https://aistudio.google.com) |
| `aihubmix` | LLM (API gateway, access to all models) | [aihubmix.com](https://aihubmix.com) |
| `siliconflow` | LLM (SiliconFlow/硅基流动) | [siliconflow.cn](https://siliconflow.cn) |
| `dashscope` | LLM (Qwen) | [dashscope.console.aliyun.com](https://dashscope.console.aliyun.com) |
| `moonshot` | LLM (Moonshot/Kimi) | [platform.moonshot.cn](https://platform.moonshot.cn) |
| `zhipu` | LLM (Zhipu GLM) | [open.bigmodel.cn](https://open.bigmodel.cn) |
| `mimo` | LLM (MiMo) | [platform.xiaomimimo.com](https://platform.xiaomimimo.com) |
| `ollama` | LLM (local, Ollama) | — |
| `mistral` | LLM | [docs.mistral.ai](https://docs.mistral.ai/) |
| `stepfun` | LLM (Step Fun/阶跃星辰) | [platform.stepfun.com](https://platform.stepfun.com) |
| `ovms` | LLM (local, OpenVINO Model Server) | [docs.openvino.ai](https://docs.openvino.ai/2026/model-server/ovms_docs_llm_quickstart.html) |
| `vllm` | LLM (local, any OpenAI-compatible server) | — |
| `openai_codex` | LLM (Codex, OAuth) | `fincat provider login openai-codex` |
| `github_copilot` | LLM (GitHub Copilot, OAuth) | `fincat provider login github-copilot` |
| `qianfan` | LLM (Baidu Qianfan) | [cloud.baidu.com](https://cloud.baidu.com/doc/qianfan/s/Hmh4suq26) |


<details>
<summary><b>OpenAI Codex (OAuth)</b></summary>

Codex uses OAuth instead of API keys. Requires a ChatGPT Plus or Pro account.
No `providers.openaiCodex` block is needed in `config.json`; `fincat provider login` stores the OAuth session outside config.

**1. Login:**
```bash
fincat provider login openai-codex
```

**2. Set model** (merge into `~/.fincat/config.json`):
```json
{
  "agents": {
    "defaults": {
      "model": "openai-codex/gpt-5.1-codex"
    }
  }
}
```

**3. Chat:**
```bash
fincat agent -m "Hello!"

# Target a specific workspace/config locally
fincat agent -c ~/.fincat-telegram/config.json -m "Hello!"

# One-off workspace override on top of that config
fincat agent -c ~/.fincat-telegram/config.json -w /tmp/fincat-telegram-test -m "Hello!"
```

> Docker users: use `docker run -it` for interactive OAuth login.

</details>


<details>
<summary><b>GitHub Copilot (OAuth)</b></summary>

GitHub Copilot uses OAuth instead of API keys. Requires a [GitHub account with a plan](https://github.com/features/copilot/plans) configured.
No `providers.githubCopilot` block is needed in `config.json`; `fincat provider login` stores the OAuth session outside config.

**1. Login:**
```bash
fincat provider login github-copilot
```

**2. Set model** (merge into `~/.fincat/config.json`):
```json
{
  "agents": {
    "defaults": {
      "model": "github-copilot/gpt-4.1"
    }
  }
}
```

**3. Chat:**
```bash
fincat agent -m "Hello!"

# Target a specific workspace/config locally
fincat agent -c ~/.fincat-telegram/config.json -m "Hello!"

# One-off workspace override on top of that config
fincat agent -c ~/.fincat-telegram/config.json -w /tmp/fincat-telegram-test -m "Hello!"
```

> Docker users: use `docker run -it` for interactive OAuth login.

</details>

<details>
<summary><b>Custom Provider (Any OpenAI-compatible API)</b></summary>

Connects directly to any OpenAI-compatible endpoint — LM Studio, llama.cpp, Together AI, Fireworks, Azure OpenAI, or any self-hosted server. Model name is passed as-is.

```json
{
  "providers": {
    "custom": {
      "apiKey": "your-api-key",
      "apiBase": "https://api.your-provider.com/v1"
    }
  },
  "agents": {
    "defaults": {
      "model": "your-model-name"
    }
  }
}
```

> For local servers that don't require a key, set `apiKey` to any non-empty string (e.g. `"no-key"`).
>
> `custom` is the right choice for providers that expose an OpenAI-compatible **chat completions** API. It does **not** force third-party endpoints onto the OpenAI/Azure **Responses API**.
>
> If your proxy or gateway is specifically Responses-API-compatible, use the `azure_openai` provider shape instead and point `apiBase` at that endpoint:
>
> ```json
> {
>   "providers": {
>     "azure_openai": {
>       "apiKey": "your-api-key",
>       "apiBase": "https://api.your-provider.com",
>       "defaultModel": "your-model-name"
>     }
>   },
>   "agents": {
>     "defaults": {
>       "provider": "azure_openai",
>       "model": "your-model-name"
>     }
>   }
> }
> ```
>
> In short: **chat-completions-compatible endpoint → `custom`**; **Responses-compatible endpoint → `azure_openai`**.

</details>

<details>
<summary><b>Ollama (local)</b></summary>

Run a local model with Ollama, then add to config:

**1. Start Ollama** (example):
```bash
ollama run llama3.2
```

**2. Add to config** (partial — merge into `~/.fincat/config.json`):
```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434"
    }
  },
  "agents": {
    "defaults": {
      "provider": "ollama",
      "model": "llama3.2"
    }
  }
}
```

> `provider: "auto"` also works when `providers.ollama.apiBase` is configured, but setting `"provider": "ollama"` is the clearest option.

</details>

<details>
<summary><b>OpenVINO Model Server (local / OpenAI-compatible)</b></summary>

Run LLMs locally on Intel GPUs using [OpenVINO Model Server](https://docs.openvino.ai/2026/model-server/ovms_docs_llm_quickstart.html). OVMS exposes an OpenAI-compatible API at `/v3`.

> Requires Docker and an Intel GPU with driver access (`/dev/dri`).

**1. Pull the model** (example):

```bash
mkdir -p ov/models && cd ov

docker run -d \
  --rm \
  --user $(id -u):$(id -g) \
  -v $(pwd)/models:/models \
  openvino/model_server:latest-gpu \
  --pull \
  --model_name openai/gpt-oss-20b \
  --model_repository_path /models \
  --source_model OpenVINO/gpt-oss-20b-int4-ov \
  --task text_generation \
  --tool_parser gptoss \
  --reasoning_parser gptoss \
  --enable_prefix_caching true \
  --target_device GPU
```

> This downloads the model weights. Wait for the container to finish before proceeding.

**2. Start the server** (example):

```bash
docker run -d \
  --rm \
  --name ovms \
  --user $(id -u):$(id -g) \
  -p 8000:8000 \
  -v $(pwd)/models:/models \
  --device /dev/dri \
  --group-add=$(stat -c "%g" /dev/dri/render* | head -n 1) \
  openvino/model_server:latest-gpu \
  --rest_port 8000 \
  --model_name openai/gpt-oss-20b \
  --model_repository_path /models \
  --source_model OpenVINO/gpt-oss-20b-int4-ov \
  --task text_generation \
  --tool_parser gptoss \
  --reasoning_parser gptoss \
  --enable_prefix_caching true \
  --target_device GPU
```

**3. Add to config** (partial — merge into `~/.fincat/config.json`):

```json
{
  "providers": {
    "ovms": {
      "apiBase": "http://localhost:8000/v3"
    }
  },
  "agents": {
    "defaults": {
      "provider": "ovms",
      "model": "openai/gpt-oss-20b"
    }
  }
}
```

> OVMS is a local server — no API key required. Supports tool calling (`--tool_parser gptoss`), reasoning (`--reasoning_parser gptoss`), and streaming.
> See the [official OVMS docs](https://docs.openvino.ai/2026/model-server/ovms_docs_llm_quickstart.html) for more details.
</details>

<details>
<summary><b>vLLM (local / OpenAI-compatible)</b></summary>

Run your own model with vLLM or any OpenAI-compatible server, then add to config:

**1. Start the server** (example):
```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000
```

**2. Add to config** (partial — merge into `~/.fincat/config.json`):

*Provider (key can be any non-empty string for local):*
```json
{
  "providers": {
    "vllm": {
      "apiKey": "dummy",
      "apiBase": "http://localhost:8000/v1"
    }
  }
}
```

*Model:*
```json
{
  "agents": {
    "defaults": {
      "model": "meta-llama/Llama-3.1-8B-Instruct"
    }
  }
}
```

</details>

<details>
<summary><b>Adding a New Provider (Developer Guide)</b></summary>

fincat uses a **Provider Registry** (`fincat/providers/registry.py`) as the single source of truth.
Adding a new provider only takes **2 steps** — no if-elif chains to touch.

**Step 1.** Add a `ProviderSpec` entry to `PROVIDERS` in `fincat/providers/registry.py`:

```python
ProviderSpec(
    name="myprovider",                   # config field name
    keywords=("myprovider", "mymodel"),  # model-name keywords for auto-matching
    env_key="MYPROVIDER_API_KEY",        # env var name
    display_name="My Provider",          # shown in `fincat status`
    default_api_base="https://api.myprovider.com/v1",  # OpenAI-compatible endpoint
)
```

**Step 2.** Add a field to `ProvidersConfig` in `fincat/config/schema.py`:

```python
class ProvidersConfig(BaseModel):
    ...
    myprovider: ProviderConfig = ProviderConfig()
```

That's it! Environment variables, model routing, config matching, and `fincat status` display will all work automatically.

**Common `ProviderSpec` options:**

| Field | Description | Example |
|-------|-------------|---------|
| `default_api_base` | OpenAI-compatible base URL | `"https://api.deepseek.com"` |
| `env_extras` | Additional env vars to set | `(("ZHIPUAI_API_KEY", "{api_key}"),)` |
| `model_overrides` | Per-model parameter overrides | `(("kimi-k2.5", {"temperature": 1.0}),)` |
| `is_gateway` | Can route any model (like OpenRouter) | `True` |
| `detect_by_key_prefix` | Detect gateway by API key prefix | `"sk-or-"` |
| `detect_by_base_keyword` | Detect gateway by API base URL | `"openrouter"` |
| `strip_model_prefix` | Strip provider prefix before sending to gateway | `True` (for AiHubMix) |
| `supports_max_completion_tokens` | Use `max_completion_tokens` instead of `max_tokens`; required for providers that reject both being set simultaneously (e.g. VolcEngine) | `True` |

</details>

### Channel Settings

Global settings that apply to all channels. Configure under the `channels` section in `~/.fincat/config.json`:

```json
{
  "channels": {
    "sendProgress": true,
    "sendToolHints": false,
    "sendMaxRetries": 3,
    "transcriptionProvider": "groq",
    "telegram": { ... }
  }
}
```

| Setting | Default | Description |
|---------|---------|-------------|
| `sendProgress` | `true` | Stream agent's text progress to the channel |
| `sendToolHints` | `false` | Stream tool-call hints (e.g. `read_file("…")`) |
| `sendMaxRetries` | `3` | Max delivery attempts per outbound message, including the initial send (0-10 configured, minimum 1 actual attempt) |
| `transcriptionProvider` | `"groq"` | Voice transcription backend: `"groq"` (free tier, default) or `"openai"`. API key is auto-resolved from the matching provider config. |

#### Retry Behavior

Retry is intentionally simple.

When a channel `send()` raises, fincat retries at the channel-manager layer. By default, `channels.sendMaxRetries` is `3`, and that count includes the initial send.

- **Attempt 1**: Send immediately
- **Attempt 2**: Retry after `1s`
- **Attempt 3**: Retry after `2s`
- **Higher retry budgets**: Backoff continues as `1s`, `2s`, `4s`, then stays capped at `4s`
- **Transient failures**: Network hiccups and temporary API limits often recover on the next attempt
- **Permanent failures**: Invalid tokens, revoked access, or banned channels will exhaust the retry budget and fail cleanly

> [!NOTE]
> This design is deliberate: channel implementations should raise on delivery failure, and the channel manager owns the shared retry policy.
>
> Some channels may still apply small API-specific retries internally. For example, Telegram separately retries timeout and flood-control errors before surfacing a final failure to the manager.
>
> If a channel is completely unreachable, fincat cannot notify the user through that same channel. Watch logs for `Failed to send to {channel} after N attempts` to spot persistent delivery failures.

### Web Search

> [!TIP]
> Use `proxy` in `tools.web` to route all web requests (search + fetch) through a proxy:
> ```json
> { "tools": { "web": { "proxy": "http://127.0.0.1:7890" } } }
> ```

fincat supports multiple web search providers. Configure in `~/.fincat/config.json` under `tools.web.search`.

By default, web tools are enabled and web search uses `duckduckgo`, so search works out of the box without an API key.

If you want to disable all built-in web tools entirely, set `tools.web.enable` to `false`. This removes both `web_search` and `web_fetch` from the tool list sent to the LLM.

If you need to allow trusted private ranges such as Tailscale / CGNAT addresses, you can explicitly exempt them from SSRF blocking with `tools.ssrfWhitelist`:

```json
{
  "tools": {
    "ssrfWhitelist": ["100.64.0.0/10"]
  }
}
```

| Provider | Config fields | Env var fallback | Free |
|----------|--------------|------------------|------|
| `brave` | `apiKey` | `BRAVE_API_KEY` | No |
| `tavily` | `apiKey` | `TAVILY_API_KEY` | No |
| `jina` | `apiKey` | `JINA_API_KEY` | Free tier (10M tokens) |
| `kagi` | `apiKey` | `KAGI_API_KEY` | No |
| `searxng` | `baseUrl` | `SEARXNG_BASE_URL` | Yes (self-hosted) |
| `duckduckgo` (default) | — | — | Yes |

**Disable all built-in web tools:**
```json
{
  "tools": {
    "web": {
      "enable": false
    }
  }
}
```

**Brave:**
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "brave",
        "apiKey": "BSA..."
      }
    }
  }
}
```

**Tavily:**
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "tavily",
        "apiKey": "tvly-..."
      }
    }
  }
}
```

**Jina** (free tier with 10M tokens):
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "jina",
        "apiKey": "jina_..."
      }
    }
  }
}
```

**Kagi:**
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "kagi",
        "apiKey": "your-kagi-api-key"
      }
    }
  }
}
```

**SearXNG** (self-hosted, no API key needed):
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "searxng",
        "baseUrl": "https://searx.example"
      }
    }
  }
}
```

**DuckDuckGo** (zero config):
```json
{
  "tools": {
    "web": {
      "search": {
        "provider": "duckduckgo"
      }
    }
  }
}
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enable` | boolean | `true` | Enable or disable all built-in web tools (`web_search` + `web_fetch`) |
| `proxy` | string or null | `null` | Proxy for all web requests, for example `http://127.0.0.1:7890` |

#### `tools.web.search`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `provider` | string | `"duckduckgo"` | Search backend: `brave`, `tavily`, `jina`, `searxng`, `duckduckgo` |
| `apiKey` | string | `""` | API key for Brave or Tavily |
| `baseUrl` | string | `""` | Base URL for SearXNG |
| `maxResults` | integer | `5` | Results per search (1–10) |

### MCP (Model Context Protocol)

> [!TIP]
> The config format is compatible with Claude Desktop / Cursor. You can copy MCP server configs directly from any MCP server's README.

fincat supports [MCP](https://modelcontextprotocol.io/) — connect external tool servers and use them as native agent tools.

Add MCP servers to your `config.json`:

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
      },
      "my-remote-mcp": {
        "url": "https://example.com/mcp/",
        "headers": {
          "Authorization": "Bearer xxxxx"
        }
      }
    }
  }
}
```

Two transport modes are supported:

| Mode | Config | Example |
|------|--------|---------|
| **Stdio** | `command` + `args` | Local process via `npx` / `uvx` |
| **HTTP** | `url` + `headers` (optional) | Remote endpoint (`https://mcp.example.com/sse`) |

Use `toolTimeout` to override the default 30s per-call timeout for slow servers:

```json
{
  "tools": {
    "mcpServers": {
      "my-slow-server": {
        "url": "https://example.com/mcp/",
        "toolTimeout": 120
      }
    }
  }
}
```

Use `enabledTools` to register only a subset of tools from an MCP server:

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
        "enabledTools": ["read_file", "mcp_filesystem_write_file"]
      }
    }
  }
}
```

`enabledTools` accepts either the raw MCP tool name (for example `read_file`) or the wrapped fincat tool name (for example `mcp_filesystem_write_file`).

- Omit `enabledTools`, or set it to `["*"]`, to register all tools.
- Set `enabledTools` to `[]` to register no tools from that server.
- Set `enabledTools` to a non-empty list of names to register only that subset.

MCP tools are automatically discovered and registered on startup. The LLM can use them alongside built-in tools — no extra configuration needed.




### Security

> [!TIP]
> For production deployments, set `"restrictToWorkspace": true` and `"tools.exec.sandbox": "bwrap"` in your config to sandbox the agent.
> In `v0.1.4.post3` and earlier, an empty `allowFrom` allowed all senders. Since `v0.1.4.post4`, empty `allowFrom` denies all access by default. To allow all senders, set `"allowFrom": ["*"]`.

| Option | Default | Description |
|--------|---------|-------------|
| `tools.restrictToWorkspace` | `false` | When `true`, restricts **all** agent tools (shell, file read/write/edit, list) to the workspace directory. Prevents path traversal and out-of-scope access. |
| `tools.exec.sandbox` | `""` | Sandbox backend for shell commands. Set to `"bwrap"` to wrap exec calls in a [bubblewrap](https://github.com/containers/bubblewrap) sandbox — the process can only see the workspace (read-write) and media directory (read-only); config files and API keys are hidden. Automatically enables `restrictToWorkspace` for file tools. **Linux only** — requires `bwrap` installed (`apt install bubblewrap`; pre-installed in the Docker image). Not available on macOS or Windows (bwrap depends on Linux kernel namespaces). |
| `tools.exec.enable` | `true` | When `false`, the shell `exec` tool is not registered at all. Use this to completely disable shell command execution. |
| `tools.exec.pathAppend` | `""` | Extra directories to append to `PATH` when running shell commands (e.g. `/usr/sbin` for `ufw`). |
| `channels.*.allowFrom` | `[]` (deny all) | Whitelist of user IDs. Empty denies all; use `["*"]` to allow everyone. |

**Docker security**: The official Docker image runs as a non-root user (`fincat`, UID 1000) with bubblewrap pre-installed. When using `docker-compose.yml`, the container drops all Linux capabilities except `SYS_ADMIN` (required for bwrap's namespace isolation).


### Auto Compact

When a user is idle for longer than a configured threshold, fincat **proactively** compresses the older part of the session context into a summary while keeping a recent legal suffix of live messages. This reduces token cost and first-token latency when the user returns — instead of re-processing a long stale context with an expired KV cache, the model receives a compact summary, the most recent live context, and fresh input.

```json
{
  "agents": {
    "defaults": {
      "idleCompactAfterMinutes": 15
    }
  }
}
```

| Option | Default | Description |
|--------|---------|-------------|
| `agents.defaults.idleCompactAfterMinutes` | `0` (disabled) | Minutes of idle time before auto-compaction starts. Set to `0` to disable. Recommended: `15` — close to a typical LLM KV cache expiry window, so stale sessions get compacted before the user returns. |

`sessionTtlMinutes` remains accepted as a legacy alias for backward compatibility, but `idleCompactAfterMinutes` is the preferred config key going forward.

How it works:
1. **Idle detection**: On each idle tick (~1 s), checks all sessions for expiration.
2. **Background compaction**: Idle sessions summarize the older live prefix via LLM and keep the most recent legal suffix (currently 8 messages).
3. **Summary injection**: When the user returns, the summary is injected as runtime context (one-shot, not persisted) alongside the retained recent suffix.
4. **Restart-safe resume**: The summary is also mirrored into session metadata so it can still be recovered after a process restart.

> [!TIP]
> Think of auto compact as "summarize older context, keep the freshest live turns." It is not a hard session reset.

### Timezone

Time is context. Context should be precise.

By default, fincat uses `UTC` for runtime time context. If you want the agent to think in your local time, set `agents.defaults.timezone` to a valid [IANA timezone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones):

```json
{
  "agents": {
    "defaults": {
      "timezone": "Asia/Shanghai"
    }
  }
}
```

This affects runtime time strings shown to the model, such as runtime context and heartbeat prompts. It also becomes the default timezone for cron schedules when a cron expression omits `tz`, and for one-shot `at` times when the ISO datetime has no explicit offset.

Common examples: `UTC`, `America/New_York`, `America/Los_Angeles`, `Europe/London`, `Europe/Berlin`, `Asia/Tokyo`, `Asia/Shanghai`, `Asia/Singapore`, `Australia/Sydney`.

> Need another timezone? Browse the full [IANA Time Zone Database](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).

### Unified Session

By default, each channel × chat ID combination gets its own session. If you use fincat across multiple channels (e.g. Telegram + Discord + CLI) and want them to share the same conversation, enable `unifiedSession`:

```json
{
  "agents": {
    "defaults": {
      "unifiedSession": true
    }
  }
}
```

When enabled, all incoming messages — regardless of which channel they arrive on — are routed into a single shared session. Switching from Telegram to Discord (or any other channel) continues the same conversation seamlessly.

| Behavior | `false` (default) | `true` |
|----------|-------------------|--------|
| Session key | `channel:chat_id` | `unified:default` |
| Cross-channel continuity | No | Yes |
| `/new` clears | Current channel session | Shared session |
| `/stop` finds tasks | By channel session | By shared session |
| Existing `session_key_override` (e.g. Telegram thread) | Respected | Still respected — not overwritten |

> This is designed for single-user, multi-device setups. It is **off by default** — existing users see zero behavior change.

### Disabled Skills

fincat ships with built-in skills, and your workspace can also define custom skills under `skills/`. If you want to hide specific skills from the agent, set `agents.defaults.disabledSkills` to a list of skill directory names:

```json
{
  "agents": {
    "defaults": {
      "disabledSkills": ["github", "weather"]
    }
  }
}
```

Disabled skills are excluded from the main agent's skill summary, from always-on skill injection, and from subagent skill summaries. This is useful when some bundled skills are unnecessary for your deployment or should not be exposed to end users.

| Option | Default | Description |
|--------|---------|-------------|
| `agents.defaults.disabledSkills` | `[]` | List of skill directory names to exclude from loading. Applies to both built-in skills and workspace skills. |

## 🧩 Multiple Instances

Run multiple fincat instances simultaneously with separate configs and runtime data. Use `--config` as the main entrypoint. Optionally pass `--workspace` during `onboard` when you want to initialize or update the saved workspace for a specific instance.

### Quick Start

If you want each instance to have its own dedicated workspace from the start, pass both `--config` and `--workspace` during onboarding.

**Initialize instances:**

```bash
# Create separate instance configs and workspaces
fincat onboard --config ~/.fincat-telegram/config.json --workspace ~/.fincat-telegram/workspace
fincat onboard --config ~/.fincat-discord/config.json --workspace ~/.fincat-discord/workspace
fincat onboard --config ~/.fincat-feishu/config.json --workspace ~/.fincat-feishu/workspace
```

**Configure each instance:**

Edit `~/.fincat-telegram/config.json`, `~/.fincat-discord/config.json`, etc. with different channel settings. The workspace you passed during `onboard` is saved into each config as that instance's default workspace.

**Run instances:**

```bash
# Instance A - Telegram bot
fincat gateway --config ~/.fincat-telegram/config.json

# Instance B - Discord bot  
fincat gateway --config ~/.fincat-discord/config.json

# Instance C - Feishu bot with custom port
fincat gateway --config ~/.fincat-feishu/config.json --port 18792
```

### Path Resolution

When using `--config`, fincat derives its runtime data directory from the config file location. The workspace still comes from `agents.defaults.workspace` unless you override it with `--workspace`.

To open a CLI session against one of these instances locally:

```bash
fincat agent -c ~/.fincat-telegram/config.json -m "Hello from Telegram instance"
fincat agent -c ~/.fincat-discord/config.json -m "Hello from Discord instance"

# Optional one-off workspace override
fincat agent -c ~/.fincat-telegram/config.json -w /tmp/fincat-telegram-test
```

> `fincat agent` starts a local CLI agent using the selected workspace/config. It does not attach to or proxy through an already running `fincat gateway` process.

| Component | Resolved From | Example |
|-----------|---------------|---------|
| **Config** | `--config` path | `~/.fincat-A/config.json` |
| **Workspace** | `--workspace` or config | `~/.fincat-A/workspace/` |
| **Cron Jobs** | config directory | `~/.fincat-A/cron/` |
| **Media / runtime state** | config directory | `~/.fincat-A/media/` |

### How It Works

- `--config` selects which config file to load
- By default, the workspace comes from `agents.defaults.workspace` in that config
- If you pass `--workspace`, it overrides the workspace from the config file

### Minimal Setup

1. Copy your base config into a new instance directory.
2. Set a different `agents.defaults.workspace` for that instance.
3. Start the instance with `--config`.

Example config:

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.fincat-telegram/workspace",
      "model": "anthropic/claude-sonnet-4-6"
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_TELEGRAM_BOT_TOKEN"
    }
  },
  "gateway": {
    "host": "127.0.0.1",
    "port": 18790
  }
}
```

Start separate instances:

```bash
fincat gateway --config ~/.fincat-telegram/config.json
fincat gateway --config ~/.fincat-discord/config.json
```

Each gateway instance also exposes a lightweight HTTP health endpoint on
`gateway.host:gateway.port`. By default, the gateway binds to `127.0.0.1`,
so the endpoint stays local unless you explicitly set `gateway.host` to a
public or LAN-facing address.

- `GET /health` returns `{"status":"ok"}`
- Other paths return `404`

Override workspace for one-off runs when needed:

```bash
fincat gateway --config ~/.fincat-telegram/config.json --workspace /tmp/fincat-telegram-test
```

### Common Use Cases

- Run separate bots for Telegram, Discord, Feishu, and other platforms
- Keep testing and production instances isolated
- Use different models or providers for different teams
- Serve multiple tenants with separate configs and runtime data

### Notes

- Each instance must use a different port if they run at the same time
- Use a different workspace per instance if you want isolated memory, sessions, and skills
- `--workspace` overrides the workspace defined in the config file
- Cron jobs and runtime media/state are derived from the config directory

## 🧠 Memory

fincat uses a layered memory system designed to stay light in the moment and durable over
time.

- `memory/history.jsonl` stores append-only summarized history
- `SOUL.md`, `USER.md`, and `memory/MEMORY.md` store long-term knowledge managed by Dream
- `Dream` can also promote repeated workflows into reusable workspace skills under `skills/`
- `Dream` runs on a schedule and can also be triggered manually
- memory changes can be inspected and restored with built-in commands

If you want the full design, see [docs/MEMORY.md](docs/MEMORY.md).

## 💻 CLI Reference

| Command | Description |
|---------|-------------|
| `fincat onboard` | Initialize config & workspace at `~/.fincat/` |
| `fincat onboard --wizard` | Launch the interactive onboarding wizard |
| `fincat onboard -c <config> -w <workspace>` | Initialize or refresh a specific instance config and workspace |
| `fincat agent -m "..."` | Chat with the agent |
| `fincat agent -w <workspace>` | Chat against a specific workspace |
| `fincat agent -w <workspace> -c <config>` | Chat against a specific workspace/config |
| `fincat agent` | Interactive chat mode |
| `fincat agent --no-markdown` | Show plain-text replies |
| `fincat agent --logs` | Show runtime logs during chat |
| `fincat serve` | Start the OpenAI-compatible API |
| `fincat gateway` | Start the gateway |
| `fincat status` | Show status |
| `fincat provider login openai-codex` | OAuth login for providers |
| `fincat channels login <channel>` | Authenticate a channel interactively |
| `fincat channels status` | Show channel status |

Interactive mode exits: `exit`, `quit`, `/exit`, `/quit`, `:q`, or `Ctrl+D`.

## 💬 In-Chat Commands

These commands work inside chat channels and interactive agent sessions:

| Command | Description |
|---------|-------------|
| `/new` | Start a new conversation |
| `/stop` | Stop the current task |
| `/restart` | Restart the bot |
| `/status` | Show bot status |
| `/dream` | Run Dream memory consolidation now |
| `/dream-log` | Show the latest Dream memory change |
| `/dream-log <sha>` | Show a specific Dream memory change |
| `/dream-restore` | List recent Dream memory versions |
| `/dream-restore <sha>` | Restore memory to the state before a specific change |
| `/help` | Show available in-chat commands |

<details>
<summary><b>Heartbeat (Periodic Tasks)</b></summary>

The gateway wakes up every 30 minutes and checks `HEARTBEAT.md` in your workspace (`~/.fincat/workspace/HEARTBEAT.md`). If the file has tasks, the agent executes them and delivers results to your most recently active chat channel.

**Setup:** edit `~/.fincat/workspace/HEARTBEAT.md` (created automatically by `fincat onboard`):

```markdown
## Periodic Tasks

- [ ] Check weather forecast and send a summary
- [ ] Scan inbox for urgent emails
```

The agent can also manage this file itself — ask it to "add a periodic task" and it will update `HEARTBEAT.md` for you.

> **Note:** The gateway must be running (`fincat gateway`) and you must have chatted with the bot at least once so it knows which channel to deliver to.

</details>

## 🐍 Python SDK

Use fincat as a library — no CLI, no gateway, just Python:

```python
from fincat import Fincat

bot = Fincat.from_config()
result = await bot.run("Summarize the README")
print(result.content)
```

Each call carries a `session_key` for conversation isolation — different keys get independent history:

```python
await bot.run("hi", session_key="user-alice")
await bot.run("hi", session_key="task-42")
```

Add lifecycle hooks to observe or customize the agent:

```python
from fincat.agent import AgentHook, AgentHookContext

class AuditHook(AgentHook):
    async def before_execute_tools(self, ctx: AgentHookContext) -> None:
        for tc in ctx.tool_calls:
            print(f"[tool] {tc.name}")

result = await bot.run("Hello", hooks=[AuditHook()])
```

See [docs/PYTHON_SDK.md](docs/PYTHON_SDK.md) for the full SDK reference.

## 🔌 OpenAI-Compatible API

fincat can expose a minimal OpenAI-compatible endpoint for local integrations:

```bash
pip install "fincat-ai[api]"
fincat serve
```

By default, the API binds to `127.0.0.1:8900`. You can change this in `config.json`.

### Behavior

- Session isolation: pass `"session_id"` in the request body to isolate conversations; omit for a shared default session (`api:default`)
- Single-message input: each request must contain exactly one `user` message
- Fixed model: omit `model`, or pass the same model shown by `/v1/models`
- No streaming: `stream=true` is not supported
- **File uploads**: supports images, PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx) via JSON base64 or `multipart/form-data` (max 10MB per file)
- API requests run in the synthetic `api` channel, so the `message` tool does **not** automatically deliver to Telegram/Discord/etc. To proactively send to another chat, call `message` with an explicit `channel` and `chat_id` for an enabled channel.

Example tool call for cross-channel delivery from an API session:

```json
{
  "content": "Build finished successfully.",
  "channel": "telegram",
  "chat_id": "123456789"
}
```

If `channel` points to a channel that is not enabled in your config, fincat will queue the outbound event but no platform delivery will occur.

### Endpoints

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`

### curl

```bash
curl http://127.0.0.1:8900/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "hi"}],
    "session_id": "my-session"
  }'
```

### File Upload (JSON base64)

Send images inline using the OpenAI multimodal content format:

```bash
curl http://127.0.0.1:8900/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": [
      {"type": "text", "text": "Describe this image"},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR..."}}
    ]}]
  }'
```

### File Upload (multipart/form-data)

Upload any supported file type (images, PDF, Word, Excel, PPT) via multipart:

```bash
# Single file
curl http://127.0.0.1:8900/v1/chat/completions \
  -F "message=Summarize this report" \
  -F "files=@report.docx"

# Multiple files with session isolation
curl http://127.0.0.1:8900/v1/chat/completions \
  -F "message=Compare these files" \
  -F "files=@chart.png" \
  -F "files=@data.xlsx" \
  -F "session_id=my-session"
```

Supported file types:
- **Images**: PNG, JPEG, GIF, WebP (sent to AI as base64 for vision analysis)
- **Documents**: PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx) (text extracted and sent to AI)
- **Text**: TXT, Markdown, CSV, JSON, etc. (read directly)

### Python (`requests`)

```python
import requests

resp = requests.post(
    "http://127.0.0.1:8900/v1/chat/completions",
    json={
        "messages": [{"role": "user", "content": "hi"}],
        "session_id": "my-session",  # optional: isolate conversation
    },
    timeout=120,
)
resp.raise_for_status()
print(resp.json()["choices"][0]["message"]["content"])
```

### Python (`openai`)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8900/v1",
    api_key="dummy",
)

resp = client.chat.completions.create(
    model="MiniMax-M2.7",
    messages=[{"role": "user", "content": "hi"}],
    extra_body={"session_id": "my-session"},  # optional: isolate conversation
)
print(resp.choices[0].message.content)
```

## 🐳 Docker

> [!TIP]
> The `-v ~/.fincat:/home/fincat/.fincat` flag mounts your local config directory into the container, so your config and workspace persist across container restarts.
> The container runs as user `fincat` (UID 1000). If you get **Permission denied**, fix ownership on the host first: `sudo chown -R 1000:1000 ~/.fincat`, or pass `--user $(id -u):$(id -g)` to match your host UID. Podman users can use `--userns=keep-id` instead.

### Docker Compose

```bash
docker compose run --rm fincat-cli onboard   # first-time setup
vim ~/.fincat/config.json                     # add API keys
docker compose up -d fincat-gateway           # start gateway
```

```bash
docker compose run --rm fincat-cli agent -m "Hello!"   # run CLI
docker compose logs -f fincat-gateway                   # view logs
docker compose down                                      # stop
```

### Docker

```bash
# Build the image
docker build -t fincat .

# Initialize config (first time only)
docker run -v ~/.fincat:/home/fincat/.fincat --rm fincat onboard

# Edit config on host to add API keys
vim ~/.fincat/config.json

# Run gateway (connects to enabled channels, e.g. Telegram/Discord/Mochat)
docker run -v ~/.fincat:/home/fincat/.fincat -p 18790:18790 fincat gateway

# Or run a single command
docker run -v ~/.fincat:/home/fincat/.fincat --rm fincat agent -m "Hello!"
docker run -v ~/.fincat:/home/fincat/.fincat --rm fincat status
```

## 🐧 Linux Service

Run the gateway as a systemd user service so it starts automatically and restarts on failure.

**1. Find the fincat binary path:**

```bash
which fincat   # e.g. /home/user/.local/bin/fincat
```

**2. Create the service file** at `~/.config/systemd/user/fincat-gateway.service` (replace `ExecStart` path if needed):

```ini
[Unit]
Description=Fincat Gateway
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/fincat gateway
Restart=always
RestartSec=10
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=%h

[Install]
WantedBy=default.target
```

**3. Enable and start:**

```bash
systemctl --user daemon-reload
systemctl --user enable --now fincat-gateway
```

**Common operations:**

```bash
systemctl --user status fincat-gateway        # check status
systemctl --user restart fincat-gateway       # restart after config changes
journalctl --user -u fincat-gateway -f        # follow logs
```

If you edit the `.service` file itself, run `systemctl --user daemon-reload` before restarting.

> **Note:** User services only run while you are logged in. To keep the gateway running after logout, enable lingering:
>
> ```bash
> loginctl enable-linger $USER
> ```

## 📁 Project Structure

```
fincat/
├── agent/          # 🧠 Core agent logic
│   ├── loop.py     #    Agent loop (LLM ↔ tool execution)
│   ├── context.py  #    Prompt builder
│   ├── memory.py   #    Persistent memory
│   ├── skills.py   #    Skills loader
│   ├── subagent.py #    Background task execution
│   └── tools/      #    Built-in tools (incl. spawn)
├── skills/         # 🎯 Bundled skills (github, weather, tmux...)
├── channels/       # 📱 Chat channel integrations (supports plugins)
├── bus/            # 🚌 Message routing
├── cron/           # ⏰ Scheduled tasks
├── heartbeat/      # 💓 Proactive wake-up
├── providers/      # 🤖 LLM providers (OpenRouter, etc.)
├── session/        # 💬 Conversation sessions
├── config/         # ⚙️ Configuration
└── cli/            # 🖥️ Commands
```

## 🤝 Contribute & Roadmap

PRs welcome! The codebase is intentionally small and readable. 🤗

### Branching Strategy

| Branch | Purpose |
|--------|---------|
| `main` | Stable releases — bug fixes and minor improvements |
| `nightly` | Experimental features — new features and breaking changes |

**Unsure which branch to target?** See [CONTRIBUTING.md](./CONTRIBUTING.md) for details.

**Roadmap** — Pick an item and [open a PR](https://github.com/HKUDS/fincat/pulls)!

- [ ] **Multi-modal** — See and hear (images, voice, video)
- [ ] **Long-term memory** — Never forget important context
- [ ] **Better reasoning** — Multi-step planning and reflection
- [ ] **More integrations** — Calendar and more
- [ ] **Self-improvement** — Learn from feedback and mistakes

### Contributors

<a href="https://github.com/HKUDS/fincat/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=HKUDS/fincat&max=100&columns=12&updated=20260210" alt="Contributors" />
</a>


## ⭐ Star History

<div align="center">
  <a href="https://star-history.com/#HKUDS/fincat&Date">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=HKUDS/fincat&type=Date&theme=dark" />
      <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=HKUDS/fincat&type=Date" />
      <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=HKUDS/fincat&type=Date" style="border-radius: 15px; box-shadow: 0 0 30px rgba(0, 217, 255, 0.3);" />
    </picture>
  </a>
</div>

<p align="center">
  <em> Thanks for visiting ✨ fincat!</em><br><br>
  <img src="https://visitor-badge.laobi.icu/badge?page_id=HKUDS.fincat&style=for-the-badge&color=00d4ff" alt="Views">
</p>


<p align="center">
  <sub>fincat is for educational, research, and technical exchange purposes only</sub>
</p>

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

nanobot is an ultra-lightweight personal AI assistant (~4,000 lines of code). It's designed for research and easy modification. The project provides a clean, minimal agent framework with support for multiple LLM providers (OpenRouter, Anthropic, OpenAI, Groq, Gemini, vLLM) and chat channels (Telegram, WhatsApp).

## Common Commands

```bash
# Install for development
pip install -e .

# Run agent interactively
nanobot agent

# Run single message
nanobot agent -m "What is 2+2?"

# Start gateway (Telegram/WhatsApp)
nanobot gateway

# Show status
nanobot status

# Channel management
nanobot channels status
nanobot channels login  # WhatsApp QR code

# Cron jobs
nanobot cron add --name "daily" --message "Good morning!" --cron "0 9 * * *"
nanobot cron list
nanobot cron remove <job_id>
```

## Architecture

The system follows a bus-based architecture with these core components:

```
User → Channel → MessageBus → AgentLoop → Tools/LLM
                            ↓
                    CronService/HeartbeatService
```

### Key Components

**`nanobot/agent/loop.py`** - Core agent loop. Handles LLM calls and tool execution. The `AgentLoop` class processes messages, calls the LLM, executes tools, and manages iteration.

**`nanobot/bus/`** - Message routing. The `MessageBus` handles event publishing/subscribing for inter-component communication. Events include inbound messages, outbound messages, and internal events.

**`nanobot/providers/litellm_provider.py`** - LLM abstraction using [LiteLLM](https://github.com/BerriAI/litellm). Supports OpenRouter, Anthropic, OpenAI, Groq, Gemini, and any OpenAI-compatible endpoint (vLLM, Bedrock).

**`nanobot/agent/tools/`** - Built-in tools. Each tool extends the `Tool` base class and is registered in `AgentLoop._register_default_tools()`. The `spawn.py` tool allows creating subagents via the `spawn` tool.

**`nanobot/channels/`** - Chat integrations. `telegram.py` and `whatsapp.py` handle messaging. The WhatsApp bridge lives in the `bridge/` directory (Node.js).

**`nanobot/cron/`** - Scheduled task execution. Jobs are stored in `~/.nanobot/data/cron/jobs.json` and executed through the agent.

**`nanobot/heartbeat/`** - Periodic wake-up. Checks `HEARTBEAT.md` every 30 minutes and prompts the agent to work on tasks.

**`nanobot/session/`** - Conversation session management.

### Workspace Files

The workspace (`~/.nanobot/workspace/`) contains markdown files that configure the agent:

- **AGENTS.md** - Agent behavior instructions
- **SOUL.md** - Agent personality and identity
- **USER.md** - User information and preferences
- **memory/MEMORY.md** - Long-term memory that persists across sessions

### Skills System

Skills are subagent definitions in `nanobot/skills/`. Each skill has a `SKILL.md` file describing its purpose and can be loaded via the `spawn` tool. See `nanobot/skills/skill-creator/` for how to create new skills.

## Configuration

Config file: `~/.nanobot/config.json`

Key settings:
- `providers.*.apiKey` - LLM provider keys
- `agents.defaults.model` - Default model (e.g., `anthropic/claude-opus-4-5`)
- `channels.*` - Telegram/WhatsApp settings
- `tools.web.search.api_key` - Brave Search API key for web search

## Development Notes

- The project uses **typer** for CLI and **rich** for formatted output
- All async code uses Python's native `asyncio`
- Providers use LiteLLM for standardization across LLM APIs
- Adding a new tool: create a class extending `Tool` in `nanobot/agent/tools/`, implement the interface, and register in `AgentLoop._register_default_tools()`
- Adding a new skill: create a subdirectory in `nanobot/skills/` with `SKILL.md` and any helper files

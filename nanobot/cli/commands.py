"""
nanobot 命令行接口 (CLI)

本模块使用 Typer 框架定义所有 nanobot 的命令行命令。
"""

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from nanobot import __version__, __logo__

# ============================================================================
# CLI 应用初始化
# ============================================================================

# 创建主 CLI 应用实例
# name="nanobot": 命令名
# no_args_is_help=True: 不带参数时显示帮助信息
app = typer.Typer(
    name="nanobot",
    help=f"{__logo__} nanobot - Personal AI Assistant",
    no_args_is_help=True,
)

# 控制台输出对象（用于彩色格式化输出）
console = Console()


# ============================================================================
# 通用回调：版本号
# ============================================================================

def version_callback(value: bool):
    """版本号回调函数，当用户输入 --version 时触发"""
    if value:
        console.print(f"{__logo__} nanobot v{__version__}")
        raise typer.Exit()


# ============================================================================
# 入口回调：主命令
# ============================================================================

@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """
    nanobot - Personal AI Assistant

    这是主入口，所有子命令都会继承这里的选项。
    """
    pass


# ============================================================================
# 第一部分：初始化命令 (Onboard / Setup)
# ============================================================================


@app.command()
def onboard():
    """
    初始化 nanobot 配置和工作区

    运行此命令会：
    1. 创建默认配置文件 ~/.nanobot/config.json
    2. 创建工作目录 ~/.nanobot/workspace/
    3. 创建默认模板文件（AGENTS.md, SOUL.md, USER.md, memory/MEMORY.md）
    """
    from nanobot.config.loader import get_config_path, save_config
    from nanobot.config.schema import Config
    from nanobot.utils.helpers import get_workspace_path

    # 获取配置文件的路径
    config_path = get_config_path()

    # 如果配置文件已存在，询问用户是否覆盖
    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        if not typer.confirm("Overwrite?"):
            raise typer.Exit()

    # 创建默认配置对象并保存
    config = Config()
    save_config(config)
    console.print(f"[green]✓[/green] Created config at {config_path}")

    # 创建工作目录
    workspace = get_workspace_path()
    console.print(f"[green]✓[/green] Created workspace at {workspace}")

    # 创建默认的模板文件
    _create_workspace_templates(workspace)

    # 打印完成信息和下一步指引
    console.print(f"\n{__logo__} nanobot is ready!")
    console.print("\nNext steps:")
    console.print("  1. Add your API key to [cyan]~/.nanobot/config.json[/cyan]")
    console.print("     Get one at: https://openrouter.ai/keys")
    console.print("  2. Chat: [cyan]nanobot agent -m \"Hello!\"[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/nanobot#-chat-apps[/dim]")


def _create_workspace_templates(workspace: Path):
    """
    创建工作区的模板文件

    这些模板文件用于配置 Agent 的行为、个性、用户信息和长期记忆。
    """
    # 定义模板内容
    templates = {
        # AGENTS.md: 定义 Agent 的行为准则和指令
        "AGENTS.md": """# Agent Instructions

You are a helpful AI assistant. Be concise, accurate, and friendly.

## Guidelines

- Always explain what you're doing before taking actions
- Ask for clarification when the request is ambiguous
- Use tools to help accomplish tasks
- Remember important information in your memory files
""",
        # SOUL.md: 定义 Agent 的个性和身份
        "SOUL.md": """# Soul

I am nanobot, a lightweight AI assistant.

## Personality

- Helpful and friendly
- Concise and to the point
- Curious and eager to learn

## Values

- Accuracy over speed
- User privacy and safety
- Transparency in actions
""",
        # USER.md: 用户信息占位符
        "USER.md": """# User

Information about the user goes here.

## Preferences

- Communication style: (casual/formal)
- Timezone: (your timezone)
- Language: (your preferred language)
""",
    }

    # 遍历模板，创建每个文件
    for filename, content in templates.items():
        file_path = workspace / filename
        if not file_path.exists():
            file_path.write_text(content)
            console.print(f"  [dim]Created {filename}[/dim]")

    # 创建 memory 目录和 MEMORY.md（长期记忆文件）
    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)
    memory_file = memory_dir / "MEMORY.md"
    if not memory_file.exists():
        memory_file.write_text("""# Long-term Memory

This file stores important information that should persist across sessions.

## User Information

(Important facts about the user)

## Preferences

(User preferences learned over time)

## Important Notes

(Things to remember)
""")
        console.print("  [dim]Created memory/MEMORY.md[/dim]")


# ============================================================================
# 第二部分：网关服务命令 (Gateway / Server)
# ============================================================================


@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """
    启动 nanobot 网关服务

    这是启动整个系统的核心命令，会：
    1. 加载配置文件
    2. 初始化消息总线 (MessageBus)
    3. 初始化 LLM 提供商
    4. 启动 Agent 循环
    5. 启动渠道管理（支持 Telegram/WhatsApp）
    6. 启动定时任务服务 (Cron)
    7. 启动心跳服务 (Heartbeat)

    使用方式：
        nanobot gateway                    # 使用默认端口 18790
        nanobot gateway --port 8080        # 自定义端口
        nanobot gateway --verbose          # 详细日志输出
    """
    from nanobot.config.loader import load_config, get_data_dir
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.litellm_provider import LiteLLMProvider
    from nanobot.agent.loop import AgentLoop
    from nanobot.channels.manager import ChannelManager
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronJob
    from nanobot.heartbeat.service import HeartbeatService

    # 如果指定了 --verbose，开启 DEBUG 日志级别
    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    console.print(f"{__logo__} Starting nanobot gateway on port {port}...")

    # 1. 加载配置文件
    config = load_config()

    # 2. 创建消息总线（用于组件间通信）
    bus = MessageBus()

    # 3. 创建 LLM 提供商（支持 OpenRouter, Anthropic, OpenAI, Bedrock 等）
    api_key = config.get_api_key()
    api_base = config.get_api_base()
    model = config.agents.defaults.model
    is_bedrock = model.startswith("bedrock/")

    # 检查是否配置了 API key
    if not api_key and not is_bedrock:
        console.print("[red]Error: No API key configured.[/red]")
        console.print("Set one in ~/.nanobot/config.json under providers.openrouter.apiKey")
        raise typer.Exit(1)

    # 创建 LiteLLM 提供商实例
    provider = LiteLLMProvider(
        api_key=api_key,
        api_base=api_base,
        default_model=config.agents.defaults.model
    )

    # 4. 创建 Agent 循环实例（处理消息和工具调用）
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        brave_api_key=config.tools.web.search.api_key or None
    )

    # 5. 创建定时任务服务
    # on_cron_job: 当定时任务触发时执行的回调
    async def on_cron_job(job: CronJob) -> str | None:
        """执行定时任务：通过 Agent 处理消息"""
        response = await agent.process_direct(
            job.payload.message,
            session_key=f"cron:{job.id}"
        )
        # 如果设置了交付选项，将响应发送到渠道
        if job.payload.deliver and job.payload.to:
            from nanobot.bus.events import OutboundMessage
            await bus.publish_outbound(OutboundMessage(
                channel=job.payload.channel or "whatsapp",
                chat_id=job.payload.to,
                content=response or ""
            ))
        return response

    # 创建定时任务服务，从 jobs.json 读取任务
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path, on_job=on_cron_job)

    # 6. 创建心跳服务
    # 每 30 分钟唤醒一次 Agent，让它检查是否有任务要做
    async def on_heartbeat(prompt: str) -> str:
        """执行心跳任务：通过 Agent 处理提示词"""
        return await agent.process_direct(prompt, session_key="heartbeat")

    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        on_heartbeat=on_heartbeat,
        interval_s=30 * 60,  # 30 分钟
        enabled=True
    )

    # 7. 创建渠道管理器（管理 Telegram/WhatsApp 连接）
    channels = ChannelManager(config, bus)

    # 打印状态信息
    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"[green]✓[/green] Heartbeat: every 30m")

    # 8. 启动所有服务
    async def run():
        """异步运行所有服务"""
        try:
            # 启动定时任务服务和心跳服务
            await cron.start()
            await heartbeat.start()
            # 并行运行 Agent 循环和渠道服务
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            # 优雅关闭：Ctrl+C 中断
            console.print("\nShutting down...")
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await channels.stop_all()

    # 运行主程序
    asyncio.run(run())


# ============================================================================
# 第三部分：Agent 对话命令
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
):
    """
    直接与 Agent 对话

    这是最常用的命令，有两种使用方式：

    1. 单次对话模式（推荐用于脚本）：
        nanobot agent -m "你好，请介绍一下自己"

    2. 交互式对话模式（进入聊天循环）：
        nanobot agent
        # 然后输入你的问题

    参数说明：
        -m, --message: 发送给 Agent 的消息
        -s, --session: 会话 ID（默认为 cli:default，用于保持对话历史）

    使用示例：
        nanobot agent -m "今天天气怎么样"
        nanobot agent -m "总结一下我们刚才讨论的内容" -s my_session
    """
    from nanobot.config.loader import load_config
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.litellm_provider import LiteLLMProvider
    from nanobot.agent.loop import AgentLoop

    # 加载配置
    config = load_config()

    # 获取 API key 和 base URL
    api_key = config.get_api_key()
    api_base = config.get_api_base()
    model = config.agents.defaults.model
    is_bedrock = model.startswith("bedrock/")

    # 检查 API key
    if not api_key and not is_bedrock:
        console.print("[red]Error: No API key configured.[/red]")
        raise typer.Exit(1)

    # 创建消息总线和 Provider
    bus = MessageBus()
    provider = LiteLLMProvider(
        api_key=api_key,
        api_base=api_base,
        default_model=config.agents.defaults.model
    )

    # 创建 Agent 实例
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        brave_api_key=config.tools.web.search.api_key or None
    )

    # 判断是单次对话还是交互模式
    if message:
        # 单次消息模式：发送一条消息，输出响应，退出
        async def run_once():
            response = await agent_loop.process_direct(message, session_id)
            console.print(f"\n{__logo__} {response}")

        asyncio.run(run_once())
    else:
        # 交互模式：持续读取用户输入，直到 Ctrl+C 退出
        console.print(f"{__logo__} Interactive mode (Ctrl+C to exit)\n")

        async def run_interactive():
            """交互式对话循环"""
            while True:
                try:
                    # 读取用户输入
                    user_input = console.input("[bold blue]You:[/bold blue] ")
                    if not user_input.strip():
                        continue

                    # 发送给 Agent，获取响应
                    response = await agent_loop.process_direct(user_input, session_id)
                    console.print(f"\n{__logo__} {response}\n")
                except KeyboardInterrupt:
                    console.print("\nGoodbye!")
                    break

        asyncio.run(run_interactive())


# ============================================================================
# 第四部分：渠道管理命令 (Channel Commands)
# ============================================================================

# 创建子命令组：nanobot channels
channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """
    查看各聊天渠道的状态

    显示：
        - WhatsApp 是否启用
        - Telegram 是否启用
        - 各渠道的配置信息

    使用示例：
        nanobot channels status
    """
    from nanobot.config.loader import load_config

    config = load_config()

    # 创建状态表格
    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")      # 渠道名称
    table.add_column("Enabled", style="green")     # 是否启用
    table.add_column("Configuration", style="yellow")  # 配置信息

    # WhatsApp 状态
    wa = config.channels.whatsapp
    table.add_row(
        "WhatsApp",
        "✓" if wa.enabled else "✗",
        wa.bridge_url
    )

    # Telegram 状态
    tg = config.channels.telegram
    tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
    table.add_row(
        "Telegram",
        "✓" if tg.enabled else "✗",
        tg_config
    )

    # 打印表格
    console.print(table)


def _get_bridge_dir() -> Path:
    """
    获取 WhatsApp Bridge 的目录

    Bridge 是一个 Node.js 程序，用于连接 WhatsApp。
    如果用户目录下没有，会从安装目录复制过来。

    返回值：Bridge 目录的 Path 对象
    """
    import shutil
    import subprocess

    # 用户目录下的 Bridge 位置：~/.nanobot/bridge
    user_bridge = Path.home() / ".nanobot" / "bridge"

    # 如果已经编译好了，直接返回
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # 检查是否安装了 npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # 查找 Bridge 源码位置
    # 优先查找安装目录，然后是开发目录
    pkg_bridge = Path(__file__).parent / "bridge"  # nanobot/bridge (pip 安装后)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # 源码根目录/bridge (开发时)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall nanobot")
        raise typer.Exit(1)

    console.print(f"{__logo__} Setting up bridge...")

    # 复制 Bridge 到用户目录
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # 安装依赖并编译
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login():
    """
    登录 WhatsApp（扫描二维码）

    这会启动 WhatsApp Bridge，并显示登录二维码。
    用手机 WhatsApp 扫描二维码即可登录。

    使用示例：
        nanobot channels login

    注意：需要先安装 Node.js >= 18
    """
    import subprocess

    # 获取 Bridge 目录（必要时会自动设置）
    bridge_dir = _get_bridge_dir()

    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    try:
        # 运行 Bridge（这会显示二维码并保持运行）
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


# ============================================================================
# 第五部分：定时任务命令 (Cron Commands)
# ============================================================================

# 创建子命令组：nanobot cron
cron_app = typer.Typer(help="Manage scheduled tasks")
app.add_typer(cron_app, name="cron")


@cron_app.command("list")
def cron_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
):
    """
    列出所有定时任务

    显示：
        - 任务 ID
        - 任务名称
        - 执行计划（每隔多久或 cron 表达式）
        - 状态（启用/禁用）
        - 下次执行时间

    使用示例：
        nanobot cron list              # 只显示启用的任务
        nanobot cron list --all        # 显示所有任务（包括禁用的）

    输出示例：
        ┌─────────────────────────────────────────────────────────────┐
        │ Scheduled Jobs                                             │
        ├──────────┬──────────┬────────────────┬──────────┬──────────┤
        │ ID       │ Name     │ Schedule       │ Status   │ Next Run │
        ├──────────┼──────────┼────────────────┼──────────┼──────────┤
        │ abc123   │ daily    │ every 86400s   │ enabled  │ 08:00    │
        │ def456   │ weekly   │ 0 9 * * 1      │ disabled │ -        │
        └──────────┴──────────┴────────────────┴──────────┴──────────┘
    """
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService

    # 读取任务存储文件
    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    # 获取任务列表
    jobs = service.list_jobs(include_disabled=all)

    if not jobs:
        console.print("No scheduled jobs.")
        return

    # 创建表格
    table = Table(title="Scheduled Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Schedule")   # 执行计划
    table.add_column("Status")     # 状态
    table.add_column("Next Run")   # 下次执行时间

    import time
    for job in jobs:
        # 格式化执行计划
        if job.schedule.kind == "every":
            # 间隔执行（如 "every 86400s" 表示每天）
            sched = f"every {(job.schedule.every_ms or 0) // 1000}s"
        elif job.schedule.kind == "cron":
            # Cron 表达式（如 "0 9 * * *" 表示每天早上 9 点）
            sched = job.schedule.expr or ""
        else:
            sched = "one-time"  # 一次性任务

        # 格式化下次执行时间
        next_run = ""
        if job.state.next_run_at_ms:
            next_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(job.state.next_run_at_ms / 1000))
            next_run = next_time

        # 格式化状态
        status = "[green]enabled[/green]" if job.enabled else "[dim]disabled[/dim]"

        table.add_row(job.id, job.name, sched, status, next_run)

    console.print(table)


@cron_app.command("add")
def cron_add(
    name: str = typer.Option(..., "--name", "-n", help="Job name"),
    message: str = typer.Option(..., "--message", "-m", help="Message for agent"),
    every: int = typer.Option(None, "--every", "-e", help="Run every N seconds"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression (e.g. '0 9 * * *')"),
    at: str = typer.Option(None, "--at", help="Run once at time (ISO format)"),
    deliver: bool = typer.Option(False, "--deliver", "-d", help="Deliver response to channel"),
    to: str = typer.Option(None, "--to", help="Recipient for delivery"),
    channel: str = typer.Option(None, "--channel", help="Channel for delivery (e.g. 'telegram', 'whatsapp')"),
):
    """
    添加定时任务

    定时任务会定期向 Agent 发送消息，Agent 处理后可以返回响应。

    必选参数：
        -n, --name: 任务名称（用于识别）
        -m, --message: 发送给 Agent 的消息

    执行计划（三选一）：
        -e, --every: 每隔 N 秒执行一次
        -c, --cron: Cron 表达式（如 "0 9 * * *" 每天早上 9 点）
        --at: 指定时间执行一次（ISO 格式，如 "2024-01-01 12:00:00"）

    交付选项（可选）：
        -d, --deliver: 是否将 Agent 的响应发送到渠道
        --to: 接收者的 ID（如 Telegram 用户名或 WhatsApp 电话号码）
        --channel: 发送渠道（telegram 或 whatsapp）

    使用示例：
        # 每天早上 9 点发送天气询问
        nanobot cron add --name "daily_weather" --message "今天天气怎么样" --cron "0 9 * * *"

        # 每小时执行一次
        nanobot cron add --name "hourly" --message "检查提醒" --every 3600

        # 定时发送消息到 Telegram
        nanobot cron add --name "morning" --message "早安！" --cron "0 8 * * *" --deliver --to "@username" --channel telegram
    """
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronSchedule

    # 确定执行计划的类型
    if every:
        # 间隔执行：N 秒
        schedule = CronSchedule(kind="every", every_ms=every * 1000)
    elif cron_expr:
        # Cron 表达式
        schedule = CronSchedule(kind="cron", expr=cron_expr)
    elif at:
        # 一次性执行：指定时间
        import datetime
        dt = datetime.datetime.fromisoformat(at)
        schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
    else:
        console.print("[red]Error: Must specify --every, --cron, or --at[/red]")
        raise typer.Exit(1)

    # 创建定时任务服务
    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    # 添加任务
    job = service.add_job(
        name=name,
        schedule=schedule,
        message=message,
        deliver=deliver,
        to=to,
        channel=channel,
    )

    console.print(f"[green]✓[/green] Added job '{job.name}' ({job.id})")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """
    删除定时任务

    使用示例：
        nanobot cron remove abc123

    参数：
        job_id: 要删除的任务 ID（可以从 cron list 命令查看）
    """
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    if service.remove_job(job_id):
        console.print(f"[green]✓[/green] Removed job {job_id}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID"),
    disable: bool = typer.Option(False, "--disable", help="Disable instead of enable"),
):
    """
    启用或禁用定时任务

    使用示例：
        nanobot cron enable abc123          # 启用任务
        nanobot cron enable abc123 --disable  # 禁用任务

    参数：
        job_id: 任务 ID
        --disable: 加上此参数则改为禁用
    """
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    job = service.enable_job(job_id, enabled=not disable)
    if job:
        status = "disabled" if disable else "enabled"
        console.print(f"[green]✓[/green] Job '{job.name}' {status}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
    force: bool = typer.Option(False, "--force", "-f", help="Run even if disabled"),
):
    """
    手动执行定时任务

    这会立即触发任务执行，而不必等待预定时间。

    使用示例：
        nanobot cron run abc123                    # 执行任务
        nanobot cron run abc123 --force            # 即使任务已禁用也执行

    参数：
        job_id: 要执行的任务 ID
        --force: 即使任务被禁用也强制执行
    """
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    async def run():
        return await service.run_job(job_id, force=force)

    if asyncio.run(run()):
        console.print(f"[green]✓[/green] Job executed")
    else:
        console.print(f"[red]Failed to run job {job_id}[/red]")


# ============================================================================
# 第六部分：状态查看命令 (Status Commands)
# ============================================================================


@app.command()
def status():
    """
    查看 nanobot 系统状态

    显示：
        - 配置文件路径及状态
        - 工作区路径及状态
        - 当前使用的模型
        - 各 API key 配置情况

    使用示例：
        nanobot status

    输出示例：
        ╭──────────────────────────────────────╮
        │          nanobot Status              │
        ├──────────────────────────────────────┤
        │ Config: /home/user/.nanobot/config   │
        │ Workspace: /home/user/.nanobot/work  │
        │ Model: anthropic/claude-opus-4-5     │
        │ OpenRouter API: ✓                    │
        │ Anthropic API: not set               │
        │ OpenAI API: not set                  │
        │ Gemini API: not set                  │
        │ vLLM/Local: not set                  │
        ╰──────────────────────────────────────╯
    """
    from nanobot.config.loader import load_config, get_config_path
    from nanobot.utils.helpers import get_workspace_path

    config_path = get_config_path()
    workspace = get_workspace_path()

    console.print(f"{__logo__} nanobot Status\n")

    # 显示配置和工作区路径
    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    # 如果配置存在，显示详细信息
    if config_path.exists():
        config = load_config()
        console.print(f"Model: {config.agents.defaults.model}")

        # 检查各 API key 是否已配置
        has_openrouter = bool(config.providers.openrouter.api_key)
        has_anthropic = bool(config.providers.anthropic.api_key)
        has_openai = bool(config.providers.openai.api_key)
        has_gemini = bool(config.providers.gemini.api_key)
        has_vllm = bool(config.providers.vllm.api_base)

        console.print(f"OpenRouter API: {'[green]✓[/green]' if has_openrouter else '[dim]not set[/dim]'}")
        console.print(f"Anthropic API: {'[green]✓[/green]' if has_anthropic else '[dim]not set[/dim]'}")
        console.print(f"OpenAI API: {'[green]✓[/green]' if has_openai else '[dim]not set[/dim]'}")
        console.print(f"Gemini API: {'[green]✓[/green]' if has_gemini else '[dim]not set[/dim]'}")
        vllm_status = f"[green]✓ {config.providers.vllm.api_base}[/green]" if has_vllm else "[dim]not set[/dim]"
        console.print(f"vLLM/Local: {vllm_status}")


# ============================================================================
# 程序入口
# ============================================================================

if __name__ == "__main__":
    app()

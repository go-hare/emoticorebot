"""CLI commands for emoticorebot."""

import asyncio
import os
import signal
from contextlib import nullcontext
from pathlib import Path
import sys
from uuid import uuid4

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout

from emoticorebot import __version__, __logo__
from emoticorebot.config.schema import Config

app = typer.Typer(
    name="emoticorebot",
    help=f"{__logo__} emoticorebot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    try:
        import termios
        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    history_file = Path.home() / ".emoticorebot" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,   # Enter submits (single line mode)
    )


def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with consistent terminal styling."""
    content = response or ""
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(f"[cyan]{__logo__} emoticorebot[/cyan]")
    console.print(body)
    console.print()


def _interactive_console() -> Console:
    """Build a Rich console bound to the current stdout proxy used by prompt_toolkit."""
    return Console(file=sys.stdout, color_system="auto")


def _print_agent_response_interactive(response: str, render_markdown: bool) -> None:
    """Render assistant output while prompt_toolkit may still own the terminal."""
    content = response or ""
    with patch_stdout():
        interactive_console = _interactive_console()
        body = Markdown(content) if render_markdown else Text(content)
        interactive_console.print()
        interactive_console.print(f"[cyan]{__logo__} emoticorebot[/cyan]")
        interactive_console.print(body)
        interactive_console.print()


def _print_interactive_line(text: str) -> None:
    """Print a single line safely during interactive prompt redraws."""
    with patch_stdout():
        _interactive_console().print(text)


def _write_stream_chunk(*, content: str, render_markdown: bool, stream_started: bool) -> None:
    text = content or ""
    with patch_stdout():
        interactive_console = _interactive_console()
        if not stream_started:
            interactive_console.print()
            interactive_console.print(f"[cyan]{__logo__} emoticorebot[/cyan]")
        del render_markdown
        sys.stdout.write(text)
        sys.stdout.flush()


def _finish_stream_output() -> None:
    with patch_stdout():
        sys.stdout.write("\n\n")
        sys.stdout.flush()


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


def _pick_one_shot_task_id(agent_loop: object, session_id: str, known_task_ids: set[str], fallback_task_id: str | None) -> str | None:
    if fallback_task_id:
        return fallback_task_id
    task_store = getattr(getattr(agent_loop, "kernel", None), "task_store", None)
    if task_store is None:
        return None
    new_tasks = [task for task in task_store.for_session(session_id) if task.task_id not in known_task_ids]
    if not new_tasks:
        return None
    newest = max(new_tasks, key=lambda task: (task.updated_at, task.state_version))
    return newest.task_id


def _is_one_shot_task_settled(agent_loop: object, task_id: str | None) -> bool:
    if not task_id:
        return False
    kernel = getattr(agent_loop, "kernel", None)
    if kernel is None or not hasattr(kernel, "get_task"):
        return False
    task = kernel.get_task(task_id)
    if task is None:
        return False
    from emoticorebot.right.state_machine import TERMINAL_STATES, RightBrainState

    return task.state in TERMINAL_STATES or task.state is RightBrainState.DONE


async def _await_one_shot_task_id(
    agent_loop: object,
    session_id: str,
    known_task_ids: set[str],
    fallback_task_id: str | None,
    *,
    retries: int = 5,
    delay_s: float = 0.1,
) -> str | None:
    task_id = _pick_one_shot_task_id(agent_loop, session_id, known_task_ids, fallback_task_id)
    if task_id or fallback_task_id:
        return task_id
    for _ in range(retries):
        await asyncio.sleep(delay_s)
        task_id = _pick_one_shot_task_id(agent_loop, session_id, known_task_ids, None)
        if task_id:
            return task_id
    return None


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc



def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} emoticorebot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """emoticorebot - Personal AI Assistant."""
    pass


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard():
    """Initialize emoticorebot configuration and workspace."""
    from emoticorebot.config.loader import get_config_path, load_config, save_config
    from emoticorebot.config.schema import Config
    from emoticorebot.utils.helpers import get_workspace_path
    
    config_path = get_config_path()
    
    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print("  [bold]N[/bold] = refresh config, keeping existing values and adding new fields")
        if typer.confirm("Overwrite?"):
            config = Config()
            save_config(config)
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            config = load_config()
            save_config(config)
            console.print(f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)")
    else:
        save_config(Config())
        console.print(f"[green]✓[/green] Created config at {config_path}")
    
    # Create workspace
    workspace = get_workspace_path()
    
    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace}")
    
    # Create default bootstrap files
    _create_workspace_templates(workspace)
    
    console.print(f"\n{__logo__} emoticorebot is ready!")
    console.print("\nNext steps:")
    console.print("  1. Add your API key to [cyan]~/.emoticorebot/config.json[/cyan]")
    console.print("     Get one at: https://openrouter.ai/keys")
    console.print("  2. Chat: [cyan]emoticorebot agent -m \"Hello!\"[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/go-hare/emoticorebot#-chat-apps[/dim]")




def _create_workspace_templates(workspace: Path):
    """Create default workspace template files from bundled templates."""
    from importlib.resources import files as pkg_files

    templates_dir = pkg_files("emoticorebot") / "templates"

    for item in templates_dir.iterdir():
        if not (item.name.endswith(".md") or item.name.endswith(".yaml")):
            continue
        dest = workspace / item.name
        if not dest.exists():
            dest.write_text(item.read_text(encoding="utf-8"), encoding="utf-8")
            console.print(f"  [dim]Created {item.name}[/dim]")

    data_memory_dir = workspace / "data" / "memory"
    data_memory_dir.mkdir(parents=True, exist_ok=True)

    config_dir = workspace / "config"
    config_dir.mkdir(exist_ok=True)

    drive_template = templates_dir / "drive_config.yaml"
    drive_file = config_dir / "drive_config.yaml"
    if not drive_file.exists() and drive_template.exists():
        drive_file.write_text(drive_template.read_text(encoding="utf-8"), encoding="utf-8")
        console.print("  [dim]Created config/drive_config.yaml[/dim]")

    (workspace / "skills").mkdir(exist_ok=True)



# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int | None = typer.Option(None, "--port", "-p", help="Reserved gateway port override"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the emoticorebot gateway."""
    from emoticorebot.config.loader import load_config, get_data_dir
    from emoticorebot.runtime.transport_bus import TransportBus
    from emoticorebot.bootstrap import RuntimeHost
    from emoticorebot.channels.manager import ChannelManager
    from emoticorebot.session.thread_store import ThreadStore
    from emoticorebot.cron.service import CronService
    from emoticorebot.cron.types import CronJob
    
    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    config = load_config()
    resolved_port = port if port is not None else config.gateway.port
    console.print(f"{__logo__} Starting emoticorebot gateway...")
    console.print(
        f"[dim]Port setting {resolved_port} is reserved for future webhook-based channels; "
        "the current gateway does not bind a local listener.[/dim]"
    )
    bus = TransportBus()
    thread_store = ThreadStore(config.workspace_path)
    
    # Create cron service first (callback set after agent creation)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)
    
    # Create agent with cron service
    agent = RuntimeHost(
        bus=bus,
        workspace=config.workspace_path,
        worker_mode=config.agents.defaults.worker_mode,
        brain_mode=config.agents.defaults.brain_mode,
        providers_config=config.providers,
        memory_config=config.memory,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        thread_store=thread_store,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
    )
    
    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        response = await agent.process_direct(
            job.payload.message,
            session_key=f"cron:{job.id}",
            channel=job.payload.channel or "cli",
            chat_id=job.payload.to or "direct",
            deliver=bool(job.payload.deliver and job.payload.channel and job.payload.to),
        )
        return response
    cron.on_job = on_cron_job
    
    # Initialize subconscious daemon and heartbeat service in RuntimeHost
    hb_cfg = config.gateway.heartbeat
    agent.initialize_subconscious(
        enable_reflection=True,  # 启用反思和主动对话
        enable_heartbeat=hb_cfg.enabled,  # 根据配置启用心跳
        heartbeat_interval_s=hb_cfg.interval_s,
    )
    
    # Create channel manager
    channels = ChannelManager(config, bus)
    
    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")
    
    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")
    
    if agent.heartbeat and agent.heartbeat.enabled:
        console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")
    
    if agent.subconscious:
        console.print("[green]✓[/green] Subconscious daemon: reflection & proactive chat enabled")
    
    async def run():
        try:
            await cron.start()
            agent.start_background_services()  # 启动潜意识守护进程和心跳服务
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            await agent.close_mcp()
            agent.stop_background_services()  # 停止潜意识守护进程和心跳服务
            cron.stop()
            agent.stop()
            await channels.stop_all()
    
    asyncio.run(run())




# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show emoticorebot runtime logs during chat"),
):
    """Interact with the agent directly."""
    from emoticorebot.config.loader import load_config, get_data_dir
    from emoticorebot.runtime.transport_bus import TransportBus
    from emoticorebot.bootstrap import RuntimeHost
    from emoticorebot.cron.service import CronService
    from loguru import logger
    
    config = load_config()
    
    bus = TransportBus()
    # Create cron service for tool usage (no callback needed for CLI unless running)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    if logs:
        logger.enable("emoticorebot")
    else:
        logger.disable("emoticorebot")
    
    agent_loop = RuntimeHost(
        bus=bus,
        workspace=config.workspace_path,
        worker_mode=config.agents.defaults.worker_mode,
        brain_mode=config.agents.defaults.brain_mode,
        providers_config=config.providers,
        memory_config=config.memory,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
    )
    
    # Show spinner when logs are off (no output to miss); skip when logs are on
    def _thinking_ctx(*, interactive: bool = False):
        if logs or interactive:
            return nullcontext()
        # Animated spinner is safe to use with prompt_toolkit input handling
        return console.status("[dim]emoticorebot is thinking...[/dim]", spinner="dots")

    async def _cli_progress(content: str, *, tool_hint: bool = False) -> None:
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:
            return
        if ch and not tool_hint and not ch.send_progress:
            return
        console.print(f"  [dim]↳ {content}[/dim]")

    if message:
        async def run_once():
            if ":" in session_id:
                cli_channel, cli_chat_id = session_id.split(":", 1)
            else:
                cli_channel, cli_chat_id = "cli", session_id
            pending_message_id = f"msg_cli_{uuid4().hex[:16]}"
            known_task_ids = {task.task_id for task in agent_loop.kernel.task_store.for_session(session_id)}
            completed = asyncio.Event()
            final_response: list[str] = []
            streamed: dict[str, bool] = {}
            awaited_task_id: str | None = None
            direct_error: list[BaseException] = []
            consume_error: list[BaseException] = []

            def _matches_pending_turn(msg: object) -> bool:
                outbound_message_id = str(getattr(msg, "reply_to", "") or "").strip()
                return outbound_message_id == pending_message_id

            async def _consume_outbound():
                nonlocal awaited_task_id
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                        metadata = msg.metadata or {}
                        if metadata.get("_progress"):
                            is_tool_hint = metadata.get("_tool_hint", False)
                            ch = agent_loop.channels_config
                            if ch and is_tool_hint and not ch.send_tool_hints:
                                continue
                            if ch and not is_tool_hint and not ch.send_progress:
                                continue
                            console.print(f"  [dim]↳ {msg.content}[/dim]")
                            continue
                        if not _matches_pending_turn(msg):
                            if msg.content:
                                _print_agent_response(msg.content, render_markdown=markdown)
                            continue
                        stream_id = str(metadata.get("_stream_id", "") or "").strip()
                        stream_state = str(metadata.get("_stream_state", "") or "").strip()
                        if metadata.get("_stream") and stream_id:
                            if stream_state in {"open", "delta"}:
                                _write_stream_chunk(
                                    content=msg.content,
                                    render_markdown=markdown,
                                    stream_started=streamed.get(stream_id, False),
                                )
                                streamed[stream_id] = True
                                continue
                            if stream_state == "superseded":
                                if streamed.pop(stream_id, False):
                                    _finish_stream_output()
                                continue
                            if streamed.get(stream_id):
                                matched_task_id = str(metadata.get("task_id", "") or "").strip() or None
                                reply_kind = str(metadata.get("reply_kind", "") or "").strip()
                                if awaited_task_id is None:
                                    awaited_task_id = await _await_one_shot_task_id(
                                        agent_loop,
                                        session_id,
                                        known_task_ids,
                                        matched_task_id,
                                        retries=30 if reply_kind == "status" and not matched_task_id else 5,
                                        delay_s=0.2,
                                    )
                                final_response[:] = [msg.content]
                                if matched_task_id == awaited_task_id and reply_kind in {"answer", "ask_user"}:
                                    completed.set()
                                    continue
                                if awaited_task_id and not _is_one_shot_task_settled(agent_loop, awaited_task_id):
                                    continue
                                completed.set()
                                continue
                        matched_task_id = str(metadata.get("task_id", "") or "").strip() or None
                        reply_kind = str(metadata.get("reply_kind", "") or "").strip()
                        if awaited_task_id is None:
                            awaited_task_id = await _await_one_shot_task_id(
                                agent_loop,
                                session_id,
                                known_task_ids,
                                matched_task_id,
                                retries=30 if reply_kind == "status" and not matched_task_id else 5,
                                delay_s=0.2,
                            )
                        final_response[:] = [msg.content]
                        if matched_task_id == awaited_task_id and reply_kind in {"answer", "ask_user"}:
                            completed.set()
                            continue
                        if awaited_task_id and not _is_one_shot_task_settled(agent_loop, awaited_task_id):
                            continue
                        completed.set()
                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break
                    except Exception as exc:
                        consume_error.append(exc)
                        completed.set()
                        raise

            async def _run_direct() -> None:
                try:
                    await agent_loop.process_direct(
                        message,
                        session_key=session_id,
                        channel=cli_channel,
                        chat_id=cli_chat_id,
                        deliver=True,
                        message_id=pending_message_id,
                    )
                except Exception as exc:
                    direct_error.append(exc)
                    completed.set()
                    raise

            try:
                outbound_task = asyncio.create_task(_consume_outbound())
                direct_task = asyncio.create_task(_run_direct())
                with _thinking_ctx():
                    await completed.wait()
                if direct_error:
                    raise direct_error[0]
                if consume_error:
                    raise consume_error[0]
                await direct_task
                if streamed:
                    _finish_stream_output()
                    if final_response and awaited_task_id:
                        _print_agent_response(final_response[0], render_markdown=markdown)
                elif final_response:
                    _print_agent_response(final_response[0], render_markdown=markdown)
            finally:
                agent_loop.stop()
                if "outbound_task" in locals():
                    outbound_task.cancel()
                if "direct_task" in locals():
                    await asyncio.gather(direct_task, outbound_task, return_exceptions=True)
                await agent_loop.close_mcp()

        asyncio.run(run_once())
    else:
        # Interactive mode — route through bus like other channels
        from emoticorebot.runtime.transport_bus import InboundMessage
        _init_prompt_session()
        console.print(f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n")

        if ":" in session_id:
            cli_channel, cli_chat_id = session_id.split(":", 1)
        else:
            cli_channel, cli_chat_id = "cli", session_id

        def _exit_on_sigint(signum, frame):
            _restore_terminal()
            console.print("\nGoodbye!")
            os._exit(0)

        signal.signal(signal.SIGINT, _exit_on_sigint)

        async def run_interactive():
            bus_task = asyncio.create_task(agent_loop.run())
            streamed: dict[str, bool] = {}

            async def _consume_outbound():
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                        if msg.metadata.get("_progress"):
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            ch = agent_loop.channels_config
                            if ch and is_tool_hint and not ch.send_tool_hints:
                                pass
                            elif ch and not is_tool_hint and not ch.send_progress:
                                pass
                            else:
                                _print_interactive_line(f"  [dim]↳ {msg.content}[/dim]")
                        elif msg.metadata.get("_stream"):
                            stream_id = str(msg.metadata.get("_stream_id", "") or "").strip()
                            if not stream_id:
                                continue
                            stream_state = str(msg.metadata.get("_stream_state", "") or "").strip()
                            if stream_state in {"open", "delta"}:
                                _write_stream_chunk(
                                    content=msg.content,
                                    render_markdown=markdown,
                                    stream_started=streamed.get(stream_id, False),
                                )
                                streamed[stream_id] = True
                            elif stream_state == "superseded":
                                if streamed.pop(stream_id, False):
                                    _finish_stream_output()
                            elif streamed.pop(stream_id, False):
                                _finish_stream_output()
                            elif msg.content:
                                _print_agent_response_interactive(msg.content, render_markdown=markdown)
                        elif msg.content:
                            _print_agent_response_interactive(msg.content, render_markdown=markdown)
                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        user_input = await _read_interactive_input_async()
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        message_id = f"msg_cli_{uuid4().hex[:16]}"

                        await bus.publish_inbound(InboundMessage(
                            channel=cli_channel,
                            sender_id="user",
                            chat_id=cli_chat_id,
                            content=user_input,
                            metadata={"message_id": message_id},
                        ))
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close_mcp()

        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from emoticorebot.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    # WhatsApp
    wa = config.channels.whatsapp
    table.add_row(
        "WhatsApp",
        "✓" if wa.enabled else "✗",
        wa.bridge_url
    )

    dc = config.channels.discord
    table.add_row(
        "Discord",
        "✓" if dc.enabled else "✗",
        dc.gateway_url
    )

    # Feishu
    fs = config.channels.feishu
    fs_config = f"app_id: {fs.app_id[:10]}..." if fs.app_id else "[dim]not configured[/dim]"
    table.add_row(
        "Feishu",
        "✓" if fs.enabled else "✗",
        fs_config
    )

    # Mochat
    mc = config.channels.mochat
    mc_base = mc.base_url or "[dim]not configured[/dim]"
    table.add_row(
        "Mochat",
        "✓" if mc.enabled else "✗",
        mc_base
    )
    
    # Telegram
    tg = config.channels.telegram
    tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
    table.add_row(
        "Telegram",
        "✓" if tg.enabled else "✗",
        tg_config
    )

    # Slack
    slack = config.channels.slack
    slack_config = "socket" if slack.app_token and slack.bot_token else "[dim]not configured[/dim]"
    table.add_row(
        "Slack",
        "✓" if slack.enabled else "✗",
        slack_config
    )

    # DingTalk
    dt = config.channels.dingtalk
    dt_config = f"client_id: {dt.client_id[:10]}..." if dt.client_id else "[dim]not configured[/dim]"
    table.add_row(
        "DingTalk",
        "✓" if dt.enabled else "✗",
        dt_config
    )

    # QQ
    qq = config.channels.qq
    qq_config = f"app_id: {qq.app_id[:10]}..." if qq.app_id else "[dim]not configured[/dim]"
    table.add_row(
        "QQ",
        "✓" if qq.enabled else "✗",
        qq_config
    )

    # Email
    em = config.channels.email
    em_config = em.imap_host if em.imap_host else "[dim]not configured[/dim]"
    table.add_row(
        "Email",
        "✓" if em.enabled else "✗",
        em_config
    )

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess
    
    # User's bridge location
    user_bridge = Path.home() / ".emoticorebot" / "bridge"
    
    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge
    
    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)
    
    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # emoticorebot/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)
    
    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge
    
    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall emoticorebot")
        raise typer.Exit(1)
    
    console.print(f"{__logo__} Setting up bridge...")
    
    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))
    
    # Install and build
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
    """Link device via QR code."""
    import subprocess
    from emoticorebot.config.loader import load_config
    
    config = load_config()
    bridge_dir = _get_bridge_dir()
    
    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")
    
    env = {**os.environ}
    if config.channels.whatsapp.bridge_token:
        env["BRIDGE_TOKEN"] = config.channels.whatsapp.bridge_token
    
    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True, env=env)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


# ============================================================================
# Cron Commands
# ============================================================================

cron_app = typer.Typer(help="Manage scheduled tasks")
app.add_typer(cron_app, name="cron")


@cron_app.command("list")
def cron_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
):
    """List scheduled jobs."""
    from emoticorebot.config.loader import get_data_dir
    from emoticorebot.cron.service import CronService
    
    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)
    
    jobs = service.list_jobs(include_disabled=all)
    
    if not jobs:
        console.print("No scheduled jobs.")
        return
    
    table = Table(title="Scheduled Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Schedule")
    table.add_column("Status")
    table.add_column("Next Run")
    
    import time
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    for job in jobs:
        # Format schedule
        if job.schedule.kind == "every":
            sched = f"every {(job.schedule.every_ms or 0) // 1000}s"
        elif job.schedule.kind == "cron":
            sched = f"{job.schedule.expr or ''} ({job.schedule.tz})" if job.schedule.tz else (job.schedule.expr or "")
        else:
            sched = "one-time"
        
        # Format next run
        next_run = ""
        if job.state.next_run_at_ms:
            ts = job.state.next_run_at_ms / 1000
            try:
                tz = ZoneInfo(job.schedule.tz) if job.schedule.tz else None
                next_run = _dt.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M")
            except Exception:
                next_run = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
        
        status = "[green]enabled[/green]" if job.enabled else "[dim]disabled[/dim]"
        
        table.add_row(job.id, job.name, sched, status, next_run)
    
    console.print(table)


@cron_app.command("add")
def cron_add(
    name: str = typer.Option(..., "--name", "-n", help="Job name"),
    message: str = typer.Option(..., "--message", "-m", help="Message for agent"),
    every: int = typer.Option(None, "--every", "-e", help="Run every N seconds"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression (e.g. '0 9 * * *')"),
    tz: str | None = typer.Option(None, "--tz", help="IANA timezone for cron (e.g. 'America/Vancouver')"),
    at: str = typer.Option(None, "--at", help="Run once at time (ISO format)"),
    deliver: bool = typer.Option(False, "--deliver", "-d", help="Deliver response to channel"),
    to: str = typer.Option(None, "--to", help="Recipient for delivery"),
    channel: str = typer.Option(None, "--channel", help="Channel for delivery (e.g. 'telegram', 'whatsapp')"),
):
    """Add a scheduled job."""
    from emoticorebot.config.loader import get_data_dir
    from emoticorebot.cron.service import CronService
    from emoticorebot.cron.types import CronSchedule
    
    if tz and not cron_expr:
        console.print("[red]Error: --tz can only be used with --cron[/red]")
        raise typer.Exit(1)

    # Determine schedule type
    if every:
        schedule = CronSchedule(kind="every", every_ms=every * 1000)
    elif cron_expr:
        schedule = CronSchedule(kind="cron", expr=cron_expr, tz=tz)
    elif at:
        import datetime
        dt = datetime.datetime.fromisoformat(at)
        schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
    else:
        console.print("[red]Error: Must specify --every, --cron, or --at[/red]")
        raise typer.Exit(1)
    
    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)
    
    try:
        job = service.add_job(
            name=name,
            schedule=schedule,
            message=message,
            deliver=deliver,
            to=to,
            channel=channel,
        )
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    console.print(f"[green]✓[/green] Added job '{job.name}' ({job.id})")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    from emoticorebot.config.loader import get_data_dir
    from emoticorebot.cron.service import CronService
    
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
    """Enable or disable a job."""
    from emoticorebot.config.loader import get_data_dir
    from emoticorebot.cron.service import CronService
    
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
    """Manually run a job."""
    from loguru import logger
    from emoticorebot.config.loader import load_config, get_data_dir
    from emoticorebot.cron.service import CronService
    from emoticorebot.cron.types import CronJob
    from emoticorebot.runtime.transport_bus import TransportBus
    from emoticorebot.bootstrap import RuntimeHost
    logger.disable("emoticorebot")

    config = load_config()
    bus = TransportBus()
    agent_loop = RuntimeHost(
        bus=bus,
        workspace=config.workspace_path,
        worker_mode=config.agents.defaults.worker_mode,
        brain_mode=config.agents.defaults.brain_mode,
        providers_config=config.providers,
        memory_config=config.memory,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
    )

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    result_holder = []

    async def on_job(job: CronJob) -> str | None:
        session_key = f"cron:{job.id}"
        channel = job.payload.channel or "cli"
        chat_id = job.payload.to or "direct"
        pending_message_id = f"msg_cron_{uuid4().hex[:16]}"
        known_task_ids = {task.task_id for task in agent_loop.kernel.task_store.for_session(session_key)}
        completed = asyncio.Event()
        final_response: list[str] = []
        awaited_task_id: str | None = None
        direct_error: list[BaseException] = []
        consume_error: list[BaseException] = []

        async def _consume_outbound() -> None:
            nonlocal awaited_task_id
            while True:
                try:
                    msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                    if str(msg.reply_to or "").strip() != pending_message_id:
                        continue
                    metadata = msg.metadata or {}
                    stream_id = str(metadata.get("_stream_id", "") or "").strip()
                    stream_state = str(metadata.get("_stream_state", "") or "").strip()
                    if metadata.get("_stream") and stream_id and stream_state in {"open", "delta", "superseded"}:
                        continue
                    task_id = str(metadata.get("task_id", "") or "").strip() or None
                    reply_kind = str(metadata.get("reply_kind", "") or "").strip()
                    if awaited_task_id is None:
                        awaited_task_id = await _await_one_shot_task_id(
                            agent_loop,
                            session_key,
                            known_task_ids,
                            task_id,
                            retries=30 if reply_kind == "status" and not task_id else 5,
                            delay_s=0.2,
                        )
                    final_response[:] = [msg.content]
                    if task_id == awaited_task_id and reply_kind in {"answer", "ask_user"}:
                        completed.set()
                        continue
                    if awaited_task_id and not _is_one_shot_task_settled(agent_loop, awaited_task_id):
                        continue
                    completed.set()
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    consume_error.append(exc)
                    completed.set()
                    raise

        async def _run_direct() -> None:
            try:
                await agent_loop.process_direct(
                    job.payload.message,
                    session_key=session_key,
                    channel=channel,
                    chat_id=chat_id,
                    deliver=True,
                    message_id=pending_message_id,
                )
            except Exception as exc:
                direct_error.append(exc)
                completed.set()
                raise

        outbound_task = asyncio.create_task(_consume_outbound())
        direct_task = asyncio.create_task(_run_direct())
        try:
            await completed.wait()
            if direct_error:
                raise direct_error[0]
            if consume_error:
                raise consume_error[0]
            await direct_task
            response = final_response[0] if final_response else ""
            result_holder.append(response)
            return response
        finally:
            outbound_task.cancel()
            await asyncio.gather(direct_task, outbound_task, return_exceptions=True)

    service.on_job = on_job

    async def run():
        return await service.run_job(job_id, force=force)

    if asyncio.run(run()):
        console.print("[green]✓[/green] Job executed")
        if result_holder:
            _print_agent_response(result_holder[0], render_markdown=True)
    else:
        console.print(f"[red]Failed to run job {job_id}[/red]")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show emoticorebot status."""
    from emoticorebot.config.loader import load_config, get_config_path

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} emoticorebot Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        console.print(f"Brain Model: {config.agents.defaults.brain_mode.model}")
        console.print(f"Worker Model: {config.agents.defaults.worker_mode.model}")


if __name__ == "__main__":
    app()

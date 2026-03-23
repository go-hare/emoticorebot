"""CLI commands for the front + resident-kernel runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.text import Text

from emoticorebot import __logo__, __version__
from emoticorebot.app.factory import build_app_context, ensure_workspace_layout
from emoticorebot.config.loader import get_config_path, load_config, save_config
from emoticorebot.config.schema import Config
from emoticorebot.desktop import DesktopBridgeServer

app = typer.Typer(name="emoticorebot", help="emoticorebot front-to-kernel runtime")
console = Console()
exit_commands = {"exit", "quit", "/exit", "/quit", ":q"}


def version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", "-v", callback=version_callback, is_eager=True),
) -> None:
    """emoticorebot command line."""


@app.command()
def onboard() -> None:
    """Create config and workspace files."""
    config_path = get_config_path()
    if config_path.exists():
        config = load_config(config_path)
    else:
        config = Config()
        save_config(config, config_path)
    ensure_workspace_layout(config.workspace_path)
    console.print(f"[green]config[/green] {config_path}")
    console.print(f"[green]workspace[/green] {config.workspace_path}")


@app.command()
def agent(
    message: str = typer.Option("", "--message", "-m", help="Send one message and exit."),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Stream front replies."),
) -> None:
    """Run the interactive front-to-kernel agent."""
    asyncio.run(run_agent(message=message, stream=stream))


@app.command()
def desktop(
    host: str = typer.Option("127.0.0.1", "--host", help="Desktop bridge host."),
    port: int = typer.Option(8765, "--port", min=1, max=65535, help="Desktop bridge port."),
    thread_id: str = typer.Option("desktop:main", "--thread-id", help="Default desktop thread id."),
) -> None:
    """Run the desktop shell bridge."""
    asyncio.run(run_desktop(host=host, port=port, thread_id=thread_id))


async def run_agent(message: str, stream: bool) -> None:
    config = load_config()
    ensure_workspace_layout(config.workspace_path)
    try:
        context = build_app_context(config)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    printer = CliPrinter()
    await context.runtime.start()
    try:
        if message.strip():
            await send_once(context, printer, message.strip(), stream=stream)
            return
        await run_interactive(context, printer, stream=stream)
    finally:
        await context.runtime.stop()


async def run_desktop(host: str, port: int, thread_id: str) -> None:
    config = load_config()
    ensure_workspace_layout(config.workspace_path)
    try:
        context = build_app_context(config)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    bridge = DesktopBridgeServer(
        runtime=context.runtime,
        workspace=context.settings.workspace,
        default_thread_id=thread_id,
    )
    console.print(f"[cyan]{__logo__} desktop bridge[/cyan] ws://{host}:{port}")
    try:
        await bridge.serve(host=host, port=port)
    finally:
        await bridge.stop()
        await context.runtime.stop()


async def send_once(context, printer: "CliPrinter", user_text: str, stream: bool) -> None:
    thread_id = "cli:direct"
    if stream:
        stream_handler = printer.stream_writer()
    else:
        stream_handler = None
    reply = await context.runtime.handle_user_text(
        thread_id=thread_id,
        session_id=thread_id,
        user_id="user",
        user_text=user_text,
        stream_handler=stream_handler,
    )
    if stream:
        await printer.finish_stream()
    else:
        await printer.print_reply(reply)
    await context.runtime.wait_for_thread_idle(thread_id)


async def run_interactive(context, printer: "CliPrinter", stream: bool) -> None:
    session = build_prompt_session()
    console.print(f"{__logo__} Interactive mode (type exit or Ctrl+C to quit)")
    while True:
        try:
            with patch_stdout():
                raw = await asyncio.to_thread(session.prompt, HTML("<b>You:</b> "))
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        user_text = str(raw or "").strip()
        if not user_text:
            continue
        if user_text.lower() in exit_commands:
            break

        stream_handler = printer.stream_writer() if stream else None
        reply = await context.runtime.handle_user_text(
            thread_id="cli:direct",
            session_id="cli:direct",
            user_id="user",
            user_text=user_text,
            stream_handler=stream_handler,
        )
        if stream:
            await printer.finish_stream()
        else:
            await printer.print_reply(reply)


def build_prompt_session() -> PromptSession:
    history_file = Path.home() / ".emoticorebot" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,
    )


class CliPrinter:
    """Print streaming and final replies safely."""

    def __init__(self) -> None:
        self.stream_started = False

    def stream_writer(self):
        async def writer(chunk: str) -> None:
            if not self.stream_started:
                with patch_stdout():
                    console.print()
                    console.print(f"[cyan]{__logo__} emoticorebot[/cyan]")
                self.stream_started = True
            with patch_stdout():
                print(chunk, end="", flush=True)

        return writer

    async def finish_stream(self) -> None:
        if not self.stream_started:
            return
        with patch_stdout():
            print()
            print()
        self.stream_started = False

    async def print_reply(self, text: str) -> None:
        with patch_stdout():
            console.print()
            console.print(f"[cyan]{__logo__} emoticorebot[/cyan]")
            console.print(Text(text or ""))
            console.print()

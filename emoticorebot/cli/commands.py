"""CLI commands for the front + resident-kernel runtime."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

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


@app.command("desktop-dev")
def desktop_dev(
    host: str = typer.Option("127.0.0.1", "--host", help="Desktop bridge host."),
    port: int = typer.Option(8765, "--port", min=1, max=65535, help="Desktop bridge port."),
    thread_id: str = typer.Option("desktop:main", "--thread-id", help="Default desktop thread id."),
    install: bool = typer.Option(True, "--install/--no-install", help="Install desktop-shell deps if missing."),
) -> None:
    """Start the desktop bridge and desktop shell together."""
    run_desktop_dev(host=host, port=port, thread_id=thread_id, install=install)


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
        await context.runtime.start()
        await bridge.serve(host=host, port=port)
    finally:
        await bridge.stop()
        await context.runtime.stop()


def run_desktop_dev(*, host: str, port: int, thread_id: str, install: bool) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    shell_root = repo_root / "desktop-shell"
    if not shell_root.exists():
        console.print(f"[red]desktop-shell not found[/red] {shell_root}")
        raise typer.Exit(code=1)

    env = build_desktop_shell_env(port=port)
    npm = resolve_command(["npm.cmd", "npm"], label="npm")
    python_cmd = sys.executable or resolve_command(["python", "python3", "py"], label="python")

    try:
        if install and not (shell_root / "node_modules").exists():
            console.print("[cyan]desktop-shell[/cyan] node_modules missing, running npm install")
            subprocess.run([npm, "install"], cwd=shell_root, env=env, check=True)

        bridge_cmd = [
            python_cmd,
            "-m",
            "emoticorebot",
            "desktop",
            "--host",
            host,
            "--port",
            str(port),
            "--thread-id",
            thread_id,
        ]
        console.print(f"[cyan]{__logo__} desktop bridge[/cyan] ws://{host}:{port}")
        bridge_process = subprocess.Popen(bridge_cmd, cwd=repo_root, env=env)
        try:
            wait_for_tcp_port(host, port, process=bridge_process, timeout=20.0)
            console.print("[green]desktop bridge ready[/green]")
            console.print("[cyan]desktop shell[/cyan] launching tauri dev")
            subprocess.run([npm, "run", "tauri", "--", "dev"], cwd=shell_root, env=env, check=True)
        finally:
            stop_process(bridge_process)
    except KeyboardInterrupt as exc:
        console.print("\n[yellow]desktop launcher interrupted[/yellow]")
        raise typer.Exit(code=130) from exc
    except subprocess.CalledProcessError as exc:
        raise typer.Exit(code=exc.returncode) from exc
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


async def send_once(context, printer: "CliPrinter", user_text: str, stream: bool) -> None:
    thread_id = "cli:direct"
    queue = context.runtime.subscribe_front_outputs()
    pump_task = asyncio.create_task(
        pump_cli_front_outputs(queue=queue, printer=printer, thread_id=thread_id, stream=stream)
    )
    try:
        await context.runtime.handle_user_text(
            thread_id=thread_id,
            session_id=thread_id,
            user_id="user",
            user_text=user_text,
        )
        await context.runtime.wait_for_thread_idle(thread_id)
        await asyncio.wait_for(queue.join(), timeout=1.0)
    finally:
        context.runtime.unsubscribe_front_outputs(queue)
        pump_task.cancel()
        try:
            await pump_task
        except asyncio.CancelledError:
            pass


async def run_interactive(context, printer: "CliPrinter", stream: bool) -> None:
    session = build_prompt_session()
    thread_id = "cli:direct"
    queue = context.runtime.subscribe_front_outputs()
    pump_task = asyncio.create_task(
        pump_cli_front_outputs(queue=queue, printer=printer, thread_id=thread_id, stream=stream)
    )
    console.print(f"{__logo__} Interactive mode (type exit or Ctrl+C to quit)")
    try:
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

            await context.runtime.handle_user_text(
                thread_id=thread_id,
                session_id=thread_id,
                user_id="user",
                user_text=user_text,
            )
    finally:
        context.runtime.unsubscribe_front_outputs(queue)
        pump_task.cancel()
        try:
            await pump_task
        except asyncio.CancelledError:
            pass


async def pump_cli_front_outputs(
    *,
    queue: asyncio.Queue,
    printer: "CliPrinter",
    thread_id: str,
    stream: bool,
) -> None:
    try:
        while True:
            packet = await queue.get()
            try:
                if packet.thread_id != thread_id:
                    continue
                if packet.type == "reply_chunk":
                    if stream:
                        await printer.write_chunk(packet.text)
                    continue
                if packet.type == "reply_done":
                    if stream:
                        if printer.stream_started:
                            await printer.finish_stream()
                        else:
                            await printer.print_reply(packet.text)
                    else:
                        await printer.print_reply(packet.text)
                    continue
                if packet.type == "turn_error":
                    await printer.print_error(packet.error)
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        raise


def build_prompt_session() -> PromptSession:
    history_file = Path.home() / ".emoticorebot" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,
    )


def resolve_command(candidates: list[str], *, label: str) -> str:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise RuntimeError(f"{label} is not available in PATH")


def build_desktop_shell_env(*, port: int) -> dict[str, str]:
    env = dict(os.environ)
    cargo_bin = Path.home() / ".cargo" / "bin"
    if cargo_bin.exists():
        current_path = env.get("PATH", "")
        env["PATH"] = f"{cargo_bin}{os.pathsep}{current_path}" if current_path else str(cargo_bin)
    env.setdefault("VITE_DESKTOP_WS_URL", f"ws://127.0.0.1:{port}")
    return env


def wait_for_tcp_port(host: str, port: int, *, process: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"desktop bridge exited early with code {process.returncode}")
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError as exc:
            last_error = str(exc)
            time.sleep(0.25)
    detail = f": {last_error}" if last_error else ""
    raise RuntimeError(f"timed out waiting for desktop bridge on ws://{host}:{port}{detail}")


def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    except OSError:
        return


class CliPrinter:
    """Print streaming and final replies safely."""

    def __init__(self) -> None:
        self.stream_started = False

    async def write_chunk(self, chunk: str) -> None:
        if not self.stream_started:
            with patch_stdout():
                console.print()
                console.print(f"[cyan]{__logo__} emoticorebot[/cyan]")
            self.stream_started = True
        with patch_stdout():
            print(chunk, end="", flush=True)

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

    async def print_error(self, text: str) -> None:
        if self.stream_started:
            await self.finish_stream()
        with patch_stdout():
            console.print(f"[red]{text}[/red]")

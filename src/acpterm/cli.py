from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from .acp_agent import ACPAgent, AgentClient
from . import agent_cache
from . import session_store

app = typer.Typer(name="acpterm", no_args_is_help=True)
_console = Console(highlight=False)

models_app = typer.Typer(help="Model discovery and control")
app.add_typer(models_app, name="models")

modes_app = typer.Typer(help="Session mode discovery and control")
app.add_typer(modes_app, name="modes")

config_app = typer.Typer(help="Configuration management")
app.add_typer(config_app, name="config")

sessions_app = typer.Typer(help="Session management")
app.add_typer(sessions_app, name="sessions")


def _run_async(coro: Any) -> None:
    asyncio.run(coro)


@app.callback()
def callback(
    ctx: typer.Context,
    agent: Annotated[
        str, typer.Option("-a", "--agent", help="ACP agent binary name")
    ] = "opencode",
    session: Annotated[
        str | None, typer.Option("-s", "--session", help="Session name")
    ] = None,
    auto_yes: Annotated[
        bool, typer.Option("-y", "--yes", help="Auto-approve all permission requests")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("-v", "--verbose", help="Show raw agent JSON-RPC responses")
    ] = False,
    read_only: Annotated[
        bool,
        typer.Option(
            "--read-only",
            help="Run agent in read-only mode (disables file modifications)",
        ),
    ] = False,
    model: Annotated[
        str | None,
        typer.Option("-m", "--model", help="Active model override for the session"),
    ] = None,
) -> None:
    ctx.ensure_object(dict)
    ctx.obj["agent"] = agent
    ctx.obj["session_name"] = session or "default"
    ctx.obj["auto_yes"] = auto_yes
    ctx.obj["verbose"] = verbose
    ctx.obj["read_only"] = read_only
    ctx.obj["model"] = model


async def _run_prompt(
    agent_binary: str,
    prompt_text: str,
    session_name: str = "default",
    auto_yes: bool = False,
    target_session_id: str | None = None,
    persist: bool = False,
    verbose: bool = False,
    read_only: bool = False,
    resources: list[Path] | None = None,
    export: Path | None = None,
    model_override: str | None = None,
) -> None:
    from .transcript import TranscriptRecorder

    project_root = Path.cwd()
    recorder = TranscriptRecorder(prompt_text, resources=resources) if export else None
    agent = ACPAgent(
        project_root=project_root,
        agent_binary=agent_binary,
        session_name=session_name,
        auto_approve=auto_yes,
        verbose=verbose,
        read_only=read_only,
        transcript_recorder=recorder,
    )
    await agent.start(
        target=target_session_id, load_existing=persist, model_override=model_override
    )
    try:
        await agent.send_prompt(prompt_text, resources=resources)
        if persist and agent.session_id:
            session_store.save(
                agent_binary,
                str(project_root.absolute()),
                agent.session_id,
                session_name,
            )
        if export and recorder:
            try:
                export.parent.mkdir(parents=True, exist_ok=True)
                export.write_text(recorder.to_markdown(), encoding="utf-8")
                _console.print(
                    f"\n[green][success][/green] Transcript exported to {export}"
                )
            except Exception as e:
                _console.print(
                    f"\n[red][error] Failed to export transcript: {e}[/red]",
                    style="bold",
                )
    finally:
        await agent.stop()


async def _create_session(
    agent_obj: ACPAgent,
    project_root: Path,
    agent_binary: str,
    session_name: str,
) -> None:
    await agent_obj.start(target=None)
    try:
        if agent_obj.session_id:
            session_store.save(
                agent_binary,
                str(project_root.absolute()),
                agent_obj.session_id,
                session_name,
            )
            sid = agent_obj.session_id[:12]
            typer.echo(f"Session created: {sid}... (name: {session_name})")
    finally:
        await agent_obj.stop()


async def _fetch_and_cache_agent_info(agent_binary: str, verbose: bool = False) -> None:
    import json
    from acp.client.connection import ClientSideConnection

    from .config import resolve_agent_command
    from acp.transports import spawn_stdio_transport

    from acp import schema as acp_schema

    capabilities = acp_schema.ClientCapabilities(
        fs=acp_schema.FileSystemCapabilities(
            read_text_file=True,
            write_text_file=True,
        ),
        session=acp_schema.ClientSessionCapabilities(
            config_options=acp_schema.SessionConfigOptionsCapabilities(
                boolean=acp_schema.BooleanConfigOptionCapabilities()
            )
        ),
    )

    cmd = resolve_agent_command(agent_binary)
    transport_ctx = spawn_stdio_transport(cmd[0], *cmd[1:])
    reader, writer, _process = await transport_ctx.__aenter__()  # type: ignore[func-returns-value]
    conn = ClientSideConnection(AgentClient(silent=True), writer, reader)
    try:
        cwd = str(Path.cwd().absolute())
        await conn.initialize(protocol_version=1, client_capabilities=capabilities)
        resp = await conn.new_session(cwd=cwd)
        if verbose:
            _console.print("[dim]--- new_session response ---[/dim]")
            _console.print(json.dumps(resp.model_dump(mode="json"), indent=2))
            _console.print("[dim]---[/dim]")
        co = getattr(resp, "config_options", None) or getattr(
            resp, "configOptions", None
        )
        modes = getattr(resp, "modes", None)
        agent_cache.store(agent_binary, co, modes)
        sid = getattr(resp, "session_id", None) or getattr(resp, "sessionId", None)
        if sid:
            try:
                await conn.close_session(session_id=sid)
            except Exception:
                pass
    finally:
        await conn.close()
        await transport_ctx.__aexit__(None, None, None)


# ── models ────────────────────────────────────────────────────────────────────


@models_app.command(name="list")
def list_models(
    ctx: typer.Context,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Force re-fetch from agent")
    ] = False,
) -> None:
    """List available models and show the currently selected one."""
    agent_binary = ctx.obj["agent"]

    if refresh or not agent_cache.is_fresh(agent_binary):
        _run_async(
            _fetch_and_cache_agent_info(
                agent_binary, verbose=ctx.obj.get("verbose", False)
            )
        )

    config_options = agent_cache.get_config_options(agent_binary) or []

    # Find standard model option in cached config options
    model_opt: dict[str, Any] | None = None
    for opt in config_options:
        if opt.get("id") == "model":
            model_opt = opt
            break

    # If not found in cache, construct fallback list from configuration/defaults
    if not model_opt:
        from .config import Config

        config = Config.load()
        fallback_models = config.get_agent_models(agent_binary)
        if fallback_models:
            model_opt = {
                "id": "model",
                "current_value": "",
                "options": [
                    {"id": m["id"], "name": m["name"]} for m in fallback_models
                ],
            }

    table = Table(title=f"Models — {agent_binary}")
    table.add_column("")
    table.add_column("Name", style="bold")
    table.add_column("ID")

    if model_opt:
        current = model_opt.get("current_value", "")
        opts: list[dict[str, Any]] = model_opt.get("options", [])
        for o in opts:
            star = "★" if o["id"] == current else " "
            table.add_row(star, o["name"], o["id"])

    if not table.row_count:
        _console.print("[dim]No model information available[/dim]")
        return
    _console.print(table)


async def _set_model_on_agent(
    agent_binary: str,
    cwd: str,
    session_name: str,
    model_id: str,
    verbose: bool = False,
) -> bool | None:
    entry = session_store.get_entry(agent_binary, cwd, session_name)
    if not entry:
        return None
    session_id = entry.get("session_id")
    if not session_id:
        return None

    agent_obj = ACPAgent(
        project_root=Path(cwd),
        agent_binary=agent_binary,
        session_name=session_name,
        verbose=verbose,
        silent=True,
    )
    try:
        await agent_obj.start(target=session_id, load_existing=True)
        await agent_obj.set_model(model_id)
        # Update cache to keep track of current model selection
        agent_cache.update_model(agent_binary, model_id)
    except Exception as e:
        if "session/set_config_option" in str(e):
            typer.echo(
                f"Error: Agent '{agent_binary}' does not support changing configuration options via the standard protocol (method not found: session/set_config_option).",
                err=True,
            )
        else:
            typer.echo(f"Error setting model: {e}", err=True)
        return False
    finally:
        await agent_obj.stop()
    return True


@models_app.command(name="set")
def set_model(
    ctx: typer.Context,
    model_id: Annotated[str, typer.Argument(help="Model ID to select")],
) -> None:
    """Set the active model for the current session."""
    agent_binary = ctx.obj["agent"]
    session_name = ctx.obj["session_name"]
    cwd = str(Path.cwd().absolute())
    verbose = ctx.obj.get("verbose", False)

    success: bool | None = None

    async def run() -> None:
        nonlocal success
        success = await _set_model_on_agent(
            agent_binary, cwd, session_name, model_id, verbose
        )

    _run_async(run())

    # Always save to the default model configuration
    from .config import Config

    try:
        config = Config.load()
        config.set_default_model(agent_binary, model_id)
    except Exception as e:
        typer.echo(f"Warning: Failed to save '{model_id}' as default: {e}", err=True)

    if success is None:
        typer.echo(
            f"No active session found. Saved '{model_id}' as the default"
            f" model for agent '{agent_binary}' for future sessions."
        )
        raise typer.Exit(code=0)
    elif not success:
        raise typer.Exit(code=1)
    else:
        typer.echo(
            f"Model set to '{model_id}' for session '{session_name}', and"
            " saved as default for future sessions."
        )


# ── modes ─────────────────────────────────────────────────────────────────────


@modes_app.command(name="list")
def list_modes(
    ctx: typer.Context,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Force re-fetch from agent")
    ] = False,
) -> None:
    """List available modes and show the currently active one."""
    agent_binary = ctx.obj["agent"]

    if refresh or not agent_cache.is_fresh(agent_binary):
        _run_async(
            _fetch_and_cache_agent_info(
                agent_binary, verbose=ctx.obj.get("verbose", False)
            )
        )

    modes = agent_cache.get_modes(agent_binary)

    table = Table(title=f"Modes — {agent_binary}")
    table.add_column("")
    table.add_column("Name", style="bold")
    table.add_column("ID")

    if modes:
        current = modes.get("current_mode_id", "")
        available: list[dict[str, Any]] = modes.get("available_modes", [])
        for m in available:
            star = "★" if m["id"] == current else " "
            table.add_row(star, m.get("name", m["id"]), m["id"])

    if not table.row_count:
        _console.print("[dim]No mode information available[/dim]")
        return
    _console.print(table)


async def _set_mode_on_agent(
    agent_binary: str,
    cwd: str,
    session_name: str,
    mode_id: str,
    verbose: bool = False,
) -> bool | None:
    entry = session_store.get_entry(agent_binary, cwd, session_name)
    if not entry:
        return None
    session_id = entry.get("session_id")
    if not session_id:
        return None

    agent_obj = ACPAgent(
        project_root=Path(cwd),
        agent_binary=agent_binary,
        session_name=session_name,
        verbose=verbose,
        silent=True,
    )
    try:
        await agent_obj.start(target=session_id, load_existing=True)
        result_mode = await agent_obj.set_mode(mode_id)
        if result_mode:
            agent_cache.update_mode(agent_binary, result_mode)
    except Exception as e:
        if "session/set_mode" in str(e):
            typer.echo(
                f"Error: Agent '{agent_binary}' does not support changing modes via the standard protocol (method not found: session/set_mode).",
                err=True,
            )
        else:
            typer.echo(f"Error setting mode: {e}", err=True)
        return False
    finally:
        await agent_obj.stop()
    return True


@modes_app.command(name="set")
def set_mode(
    ctx: typer.Context,
    mode_id: Annotated[str, typer.Argument(help="Mode ID to activate")],
) -> None:
    """Set the active mode for the current session."""
    agent_binary = ctx.obj["agent"]
    session_name = ctx.obj["session_name"]
    cwd = str(Path.cwd().absolute())
    verbose = ctx.obj.get("verbose", False)

    success: bool | None = None

    async def run() -> None:
        nonlocal success
        success = await _set_mode_on_agent(
            agent_binary, cwd, session_name, mode_id, verbose
        )

    _run_async(run())

    if success is None:
        typer.echo(
            f"Active session '{session_name}' not found for {agent_binary} in {cwd}.",
            err=True,
        )
        raise typer.Exit(code=1)
    elif not success:
        raise typer.Exit(code=1)
    else:
        typer.echo(f"Mode set to '{mode_id}' for session '{session_name}'.")


# ── config ────────────────────────────────────────────────────────────────────


@config_app.command(name="show")
def show_config() -> None:
    """Show the current configuration file."""
    from rich.syntax import Syntax
    from .config import CONFIG_FILE

    if not CONFIG_FILE.exists():
        _console.print(
            f"[yellow]Configuration file does not exist at:[/yellow] {CONFIG_FILE}"
        )
        _console.print("Run [bold]acpterm config init[/bold] to create one.")
        return

    try:
        content = CONFIG_FILE.read_text(encoding="utf-8")
        syntax = Syntax(content, "json", theme="monokai", line_numbers=True)
        _console.print(f"[bold]Configuration file:[/bold] {CONFIG_FILE}\n")
        _console.print(syntax)
    except Exception as e:
        typer.echo(f"Error reading configuration file: {e}", err=True)
        raise typer.Exit(code=1)


@config_app.command(name="init")
def init_config(
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            "-o",
            help="Overwrite existing configuration file without prompting",
        ),
    ] = False,
) -> None:
    """Initialize a default configuration file."""
    from .config import CONFIG_FILE, Config

    if CONFIG_FILE.exists() and not overwrite:
        if not typer.confirm(
            f"Configuration file already exists at {CONFIG_FILE}. Overwrite?"
        ):
            _console.print("[yellow]Aborted.[/yellow]")
            return

    # Create a nice template config
    config = Config(
        agents={
            "opencode": "opencode",
            "kiro": "kiro",
        },
        agent_models={
            "opencode": [
                {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
                {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
                {"id": "claude-3-5-sonnet", "name": "Claude 3.5 Sonnet"},
                {"id": "gpt-4o", "name": "GPT 4o"},
            ],
            "kiro": [
                {"id": "kiro-large", "name": "Kiro Large"},
                {"id": "kiro-medium", "name": "Kiro Medium"},
            ],
        },
        max_prompt_chars=100000,
    )

    try:
        config.save()
        _console.print(
            f"[green][success][/green] Initialized default configuration at"
            f" {CONFIG_FILE}"
        )
    except Exception as e:
        typer.echo(f"Error initializing configuration file: {e}", err=True)
        raise typer.Exit(code=1)


# ── sessions ──────────────────────────────────────────────────────────────────


@sessions_app.command()
def new(
    ctx: typer.Context,
    name: Annotated[
        str | None, typer.Option("--name", help="Named session identifier")
    ] = None,
) -> None:
    """Create a new session (saves session ID locally)."""
    agent_binary = ctx.obj["agent"]
    session_name = name or ctx.obj["session_name"]
    project_root = Path.cwd()
    agent_obj = ACPAgent(
        project_root=project_root,
        agent_binary=agent_binary,
        session_name=session_name,
        verbose=ctx.obj.get("verbose", False),
        read_only=ctx.obj.get("read_only", False),
        silent=True,
    )
    _run_async(_create_session(agent_obj, project_root, agent_binary, session_name))


@sessions_app.command(name="list")
def list_sessions(ctx: typer.Context) -> None:
    """List saved sessions for the current agent and working directory."""
    agent_binary = ctx.obj["agent"]
    cwd = str(Path.cwd().absolute())
    sessions = session_store.list_sessions(agent_name=agent_binary, cwd=cwd)

    table = Table(title=f"Sessions — {agent_binary}")
    table.add_column("Name", style="bold")
    table.add_column("Created")
    table.add_column("Session ID")

    for s in sessions:
        table.add_row(
            s.get("name", "—"),
            s.get("created_at", "—")[:19],
            s.get("session_id", "—")[:16] + "...",
        )

    if not table.row_count:
        _console.print("[dim]No sessions found[/dim]")
        return
    _console.print(table)


@sessions_app.command(name="show")
def show_session(
    ctx: typer.Context,
    name: Annotated[
        str | None, typer.Argument(help="Session name (defaults to current)")
    ] = None,
) -> None:
    """Show metadata for a saved session."""
    agent_binary = ctx.obj["agent"]
    session_name = name or ctx.obj["session_name"]
    cwd = str(Path.cwd().absolute())
    entry = session_store.get_entry(agent_binary, cwd, session_name)
    if not entry:
        typer.echo(f"Session '{session_name}' not found for {agent_binary} in {cwd}")
        raise typer.Exit(code=1)

    _console.print(f"[bold]Session:[/bold] {entry['name']}")
    _console.print(f"  Agent:     {entry.get('agent_name')}")
    _console.print(f"  Directory: {entry.get('cwd')}")
    _console.print(f"  Created:   {entry.get('created_at', '—')[:19]}")
    _console.print(f"  ID:        {entry.get('session_id')}")


async def _close_session_on_agent(
    agent_binary: str,
    cwd: str,
    session_name: str,
    verbose: bool = False,
) -> bool:
    entry = session_store.get_entry(agent_binary, cwd, session_name)
    if not entry:
        return False
    session_id = entry.get("session_id")
    if session_id:
        agent_obj = ACPAgent(
            project_root=Path(cwd),
            agent_binary=agent_binary,
            session_name=session_name,
            verbose=verbose,
            silent=True,
        )
        try:
            await agent_obj.start(target=session_id, load_existing=True)
            await agent_obj.close_session()
        except Exception:
            pass
        finally:
            await agent_obj.stop()
    session_store.remove(agent_binary, cwd, session_name)
    return True


@sessions_app.command(name="close")
def close_session(
    ctx: typer.Context,
    name: Annotated[
        str | None, typer.Argument(help="Session name (defaults to current)")
    ] = None,
) -> None:
    """Close a session (notifies the agent and removes local metadata)."""
    agent_binary = ctx.obj["agent"]
    session_name = name or ctx.obj["session_name"]
    cwd = str(Path.cwd().absolute())
    verbose = ctx.obj.get("verbose", False)

    removed = False

    async def run() -> None:
        nonlocal removed
        removed = await _close_session_on_agent(
            agent_binary, cwd, session_name, verbose
        )

    _run_async(run())

    if removed:
        typer.echo(f"Session '{session_name}' closed and removed.")
    else:
        typer.echo(f"Session '{session_name}' not found for {agent_binary} in {cwd}")


# ── top-level commands ────────────────────────────────────────────────────────


def _resolve_prompt_text(prompt: list[str] | None, file: Path | None) -> str:
    import sys

    parts: list[str] = []

    # 1. Read from file/stdin if --file is specified
    if file is not None:
        if str(file) == "-":
            parts.append(sys.stdin.read())
        else:
            if not file.exists():
                raise typer.BadParameter(f"File not found: {file}")
            parts.append(file.read_text(encoding="utf-8"))
    # 2. Otherwise read from stdin if input is piped and prompt is empty
    elif not prompt and not sys.stdin.isatty():
        parts.append(sys.stdin.read())

    # 3. Append positional prompt arguments if any
    if prompt:
        parts.append(" ".join(prompt))

    # 4. If we have nothing resolved, raise error
    resolved = "\n".join(parts).strip()
    if not resolved:
        raise typer.BadParameter(
            "Error: Missing prompt text. Please provide a prompt argument, use --file, or pipe input."
        )

    # 5. Configurable prompt size guardrail
    from .config import Config

    config = Config.load()
    limit = config.max_prompt_chars

    if len(resolved) > limit:
        msg = f"Warning: The prompt size ({len(resolved):,} characters) exceeds the limit of {limit:,} characters."
        if sys.stdin.isatty():
            if not typer.confirm(f"{msg} Do you want to send it anyway?"):
                raise typer.Abort()
        else:
            raise typer.BadParameter(
                f"{msg}\nTo allow larger prompts, increase 'max_prompt_chars' in your ~/.acpterm/config.json."
            )

    return resolved


@app.command()
def prompt(
    ctx: typer.Context,
    prompt: Annotated[
        list[str] | None, typer.Argument(help="Prompt text to send to the agent")
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option("--file", "-f", help="Read prompt from file (use '-' for stdin)"),
    ] = None,
    resource: Annotated[
        list[Path] | None,
        typer.Option(
            "--resource",
            "-r",
            help="Attach resource files (supports tab-completion)",
            exists=True,
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
    export: Annotated[
        Path | None,
        typer.Option(
            "--export",
            "-e",
            help="Export session run transcript to a Markdown file",
            file_okay=True,
            dir_okay=False,
            writable=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """Send a prompt to the agent (saves session for subsequent prompts)."""
    prompt_text = _resolve_prompt_text(prompt, file)
    _run_async(
        _run_prompt(
            agent_binary=ctx.obj["agent"],
            prompt_text=prompt_text,
            session_name=ctx.obj["session_name"],
            auto_yes=ctx.obj["auto_yes"],
            persist=True,
            verbose=ctx.obj.get("verbose", False),
            read_only=ctx.obj.get("read_only", False),
            resources=resource,
            export=export,
            model_override=ctx.obj.get("model"),
        )
    )


@app.command()
def exec(
    ctx: typer.Context,
    prompt: Annotated[
        list[str] | None, typer.Argument(help="Prompt text (one-shot)")
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option("--file", "-f", help="Read prompt from file (use '-' for stdin)"),
    ] = None,
    resource: Annotated[
        list[Path] | None,
        typer.Option(
            "--resource",
            "-r",
            help="Attach resource files (supports tab-completion)",
            exists=True,
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
    export: Annotated[
        Path | None,
        typer.Option(
            "--export",
            "-e",
            help="Export session run transcript to a Markdown file",
            file_okay=True,
            dir_okay=False,
            writable=True,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    """One-shot prompt (no session persistence)."""
    prompt_text = _resolve_prompt_text(prompt, file)
    _run_async(
        _run_prompt(
            agent_binary=ctx.obj["agent"],
            prompt_text=prompt_text,
            auto_yes=ctx.obj["auto_yes"],
            persist=False,
            verbose=ctx.obj.get("verbose", False),
            read_only=ctx.obj.get("read_only", False),
            resources=resource,
            export=export,
            model_override=ctx.obj.get("model"),
        )
    )


def main() -> None:
    app()

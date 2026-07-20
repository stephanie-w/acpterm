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
        bool, typer.Option("--read-only", help="Run agent in read-only mode (disables file modifications)")
    ] = False,
) -> None:
    ctx.ensure_object(dict)
    ctx.obj["agent"] = agent
    ctx.obj["session_name"] = session or "default"
    ctx.obj["auto_yes"] = auto_yes
    ctx.obj["verbose"] = verbose
    ctx.obj["read_only"] = read_only


async def _run_prompt(
    agent_binary: str,
    prompt_text: str,
    session_name: str = "default",
    auto_yes: bool = False,
    target_session_id: str | None = None,
    persist: bool = False,
    verbose: bool = False,
    read_only: bool = False,
) -> None:
    project_root = Path.cwd()
    agent = ACPAgent(
        project_root=project_root,
        agent_binary=agent_binary,
        session_name=session_name,
        auto_approve=auto_yes,
        verbose=verbose,
        read_only=read_only,
    )
    await agent.start(target=target_session_id, load_existing=persist)
    try:
        await agent.send_prompt(prompt_text)
        if persist and agent.session_id:
            session_store.save(
                agent_binary,
                str(project_root.absolute()),
                agent.session_id,
                session_name,
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

    table = Table(title=f"Models — {agent_binary}")
    table.add_column("")
    table.add_column("Name", style="bold")
    table.add_column("ID")

    for opt in config_options:
        if opt["id"] != "model":
            continue
        current = opt.get("current_value", "")
        for o in opt.get("options", []):
            star = "★" if o["id"] == current else " "
            table.add_row(star, o["name"], o["id"])

    if not table.row_count:
        _console.print("[dim]No model information available[/dim]")
        return
    _console.print(table)


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


@sessions_app.command(name="close")
def close_session(
    ctx: typer.Context,
    name: Annotated[
        str | None, typer.Argument(help="Session name (defaults to current)")
    ] = None,
) -> None:
    """Remove a saved session from the local store."""
    agent_binary = ctx.obj["agent"]
    session_name = name or ctx.obj["session_name"]
    cwd = str(Path.cwd().absolute())
    removed = session_store.remove(agent_binary, cwd, session_name)
    if removed:
        typer.echo(f"Session '{session_name}' removed.")
    else:
        typer.echo(f"Session '{session_name}' not found for {agent_binary} in {cwd}")


# ── top-level commands ────────────────────────────────────────────────────────


@app.command()
def prompt(
    ctx: typer.Context,
    prompt: Annotated[
        list[str], typer.Argument(help="Prompt text to send to the agent")
    ],
) -> None:
    """Send a prompt to the agent (saves session for subsequent prompts)."""
    prompt_text = " ".join(prompt)
    _run_async(
        _run_prompt(
            agent_binary=ctx.obj["agent"],
            prompt_text=prompt_text,
            session_name=ctx.obj["session_name"],
            auto_yes=ctx.obj["auto_yes"],
            persist=True,
            verbose=ctx.obj.get("verbose", False),
            read_only=ctx.obj.get("read_only", False),
        )
    )


@app.command()
def exec(
    ctx: typer.Context,
    prompt: Annotated[list[str], typer.Argument(help="Prompt text (one-shot)")],
) -> None:
    """One-shot prompt (no session persistence)."""
    prompt_text = " ".join(prompt)
    _run_async(
        _run_prompt(
            agent_binary=ctx.obj["agent"],
            prompt_text=prompt_text,
            auto_yes=ctx.obj["auto_yes"],
            persist=False,
            verbose=ctx.obj.get("verbose", False),
            read_only=ctx.obj.get("read_only", False),
        )
    )


def main() -> None:
    app()

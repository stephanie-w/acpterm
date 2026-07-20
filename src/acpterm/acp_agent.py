from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from acp import schema as acp_schema
from acp.client.connection import ClientSideConnection
from acp.transports import spawn_stdio_transport
from rich.console import Console
from rich.prompt import Confirm

from .config import resolve_agent_command
from .output import format_session_update, format_stop_reason
from .session_store import get as get_saved_session

PROTOCOL_VERSION = 1
_console = Console(highlight=False)


def _debug_log(verbose: bool, label: str, resp: Any) -> None:
    if not verbose:
        return
    import json

    _console.print(f"\n[dim]--- {label} ---[/dim]")
    try:
        _console.print(json.dumps(resp.model_dump(mode="json"), indent=2))
    except Exception:
        _console.print(repr(resp))
    _console.print("[dim]---[/dim]\n")


class AgentClient:
    """Implements the ACP `Client` protocol to receive session updates from the agent."""

    def __init__(
        self,
        auto_approve: bool = False,
        silent: bool = False,
        read_only: bool = False,
    ) -> None:
        self._auto_approve = auto_approve
        self._silent = silent
        self._read_only = read_only

    def on_connect(self, conn: Any) -> None:
        pass

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,
    ) -> None:
        if not self._silent:
            format_session_update(session_id, update)

    async def request_permission(
        self,
        session_id: str,
        tool_call: Any,
        options: list[Any],
        **kwargs: Any,
    ) -> acp_schema.RequestPermissionResponse:
        title = getattr(tool_call, "title", "unknown") or "unknown"
        kind = getattr(tool_call, "kind", None)
        kind_str = getattr(kind, "value", str(kind)) if kind else "unknown"

        if not self._silent:
            _console.print(f"\n[yellow][perm][/yellow] {title} [dim]({kind_str})[/dim]")

        if self._silent or self._auto_approve:
            if not self._silent:
                _console.print("[dim]  Auto-approved (--yes)[/dim]")
            option_id = options[0].option_id if options else "allow_always"
            return acp_schema.RequestPermissionResponse(
                outcome=acp_schema.AllowedOutcome(
                    outcome="selected",
                    option_id=option_id,
                )
            )

        approved = Confirm.ask("  Allow?", default=True)
        if approved and options:
            option_id = options[0].option_id
            return acp_schema.RequestPermissionResponse(
                outcome=acp_schema.AllowedOutcome(
                    outcome="selected",
                    option_id=option_id,
                )
            )
        return acp_schema.RequestPermissionResponse(
            outcome=acp_schema.DeniedOutcome(outcome="cancelled")
        )

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kwargs: Any
    ) -> acp_schema.WriteTextFileResponse:
        if self._read_only:
            raise RuntimeError("File modifications are disabled in read-only mode")
        if not self._silent:
            file_path = Path(path)
            file_path.write_text(content)
        return acp_schema.WriteTextFileResponse()

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> acp_schema.ReadTextFileResponse:
        if self._silent:
            return acp_schema.ReadTextFileResponse(content="")
        file_path = Path(path)
        text = file_path.read_text()
        lines = text.splitlines()
        if line is not None:
            start = line - 1
            end = start + limit if limit else None
            lines = lines[start:end]
        return acp_schema.ReadTextFileResponse(content="\n".join(lines))

    async def create_terminal(
        self,
        session_id: str,
        command: str,
        args: list[str] | None = None,
        env: list[acp_schema.EnvVariable] | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> acp_schema.CreateTerminalResponse:
        return acp_schema.CreateTerminalResponse(terminal_id="term_default")

    async def terminal_output(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> acp_schema.TerminalOutputResponse:
        return acp_schema.TerminalOutputResponse(output="", truncated=False)

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> acp_schema.ReleaseTerminalResponse | None:
        return None

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> acp_schema.WaitForTerminalExitResponse:
        return acp_schema.WaitForTerminalExitResponse(exit_code=0)

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> acp_schema.KillTerminalResponse | None:
        return None

    async def create_elicitation(
        self, message: str, mode: acp_schema.ElicitationMode, **kwargs: Any
    ) -> acp_schema.CreateElicitationResponse:
        return acp_schema.DeclineElicitationResponse(action="decline")

    async def complete_elicitation(self, elicitation_id: str, **kwargs: Any) -> None:
        pass

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        pass


class ACPAgent:
    """Concrete agent that spawns an ACP-protocol subprocess and communicates via ClientSideConnection."""

    def __init__(
        self,
        project_root: Path,
        agent_binary: str = "opencode",
        session_name: str = "default",
        auto_approve: bool = False,
        verbose: bool = False,
        read_only: bool = False,
    ) -> None:
        self.project_root_path = project_root
        self.agent_binary = agent_binary
        self.session_name = session_name
        self._auto_approve = auto_approve
        self._verbose = verbose
        self._read_only = read_only

        self._conn: ClientSideConnection | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._transport_ctx: AbstractAsyncContextManager | None = None
        self._session_id: str | None = None

    async def start(
        self, target: str | None = None, *, load_existing: bool = True
    ) -> None:
        capabilities = acp_schema.ClientCapabilities(
            fs=acp_schema.FileSystemCapabilities(
                read_text_file=True,
                write_text_file=not self._read_only,
            ),
            session=acp_schema.ClientSessionCapabilities(
                config_options=acp_schema.SessionConfigOptionsCapabilities(
                    boolean=acp_schema.BooleanConfigOptionCapabilities()
                )
            ),
        )

        client = AgentClient(
            auto_approve=self._auto_approve,
            read_only=self._read_only,
        )
        cmd = resolve_agent_command(self.agent_binary)
        self._transport_ctx = spawn_stdio_transport(cmd[0], *cmd[1:])
        reader, writer, self._process = await self._transport_ctx.__aenter__()  # type: ignore[func-returns-value]

        self._conn = ClientSideConnection(client, writer, reader)

        cwd = str(self.project_root_path.absolute())
        init_resp = await self._conn.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=capabilities,
        )
        _debug_log(self._verbose, "initialize", init_resp)

        if target is None and load_existing:
            saved_session_id = get_saved_session(
                self.agent_binary, cwd, self.session_name
            )
            if saved_session_id:
                target = saved_session_id

        if target:
            session_resp = await self._conn.load_session(cwd=cwd, session_id=target)
            self._session_id = target
        else:
            session_resp = await self._conn.new_session(cwd=cwd)
            self._session_id = session_resp.session_id if session_resp else None
        _debug_log(
            self._verbose, "load_session" if target else "new_session", session_resp
        )

    async def send_prompt(self, prompt: str) -> str | None:
        if self._conn is None or self._session_id is None:
            raise RuntimeError("Agent not started")

        from acp import text_block

        resp = await self._conn.prompt(
            self._session_id,
            [text_block(prompt)],
        )
        _debug_log(self._verbose, "prompt", resp)
        stop_reason = str(resp.stop_reason)
        format_stop_reason(stop_reason)
        return stop_reason

    async def cancel(self) -> bool:
        if self._conn is None or self._session_id is None:
            return False
        await self._conn.cancel(session_id=self._session_id)
        return True

    async def set_mode(self, mode_id: str) -> str | None:
        if self._conn is None or self._session_id is None:
            raise RuntimeError("Agent not started")
        resp = await self._conn.set_session_mode(
            session_id=self._session_id, mode_id=mode_id
        )
        return resp.current_mode_id if resp else None

    async def set_model(self, model_id: str) -> None:
        if self._conn is None or self._session_id is None:
            raise RuntimeError("Agent not started")
        await self._conn.set_config_option(
            config_id="model",
            session_id=self._session_id,
            value=model_id,
        )

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def close_session(self) -> None:
        if self._conn is not None and self._session_id is not None:
            try:
                await self._conn.close_session(session_id=self._session_id)
            except Exception:
                pass

    async def stop(self) -> None:
        if self._conn:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None
        if self._transport_ctx:
            try:
                await self._transport_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._transport_ctx = None
        self._process = None
        self._session_id = None

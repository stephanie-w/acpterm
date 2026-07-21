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
from .output import (
    format_session_update,
    format_stop_reason,
    display_initial_session_info,
)
from .session_store import get as get_saved_session
from .transcript import TranscriptRecorder

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
        recorder: TranscriptRecorder | None = None,
        agent_binary: str = "opencode",
    ) -> None:
        self._auto_approve = auto_approve
        self._silent = silent
        self._read_only = read_only
        self._recorder = recorder
        self._agent_binary = agent_binary

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

        if self._recorder:
            from .output import _extract_text, _format_content_blocks

            session_update = getattr(update, "session_update", None) or getattr(
                update, "sessionUpdate", None
            )

            if session_update == "agent_thought_chunk":
                text = _extract_text(update)
                if text:
                    self._recorder.add_thought(text)
            elif session_update == "agent_message_chunk":
                text = _extract_text(update)
                if text:
                    self._recorder.add_message(text)
            elif session_update == "tool_call":
                title = getattr(update, "title", "Unknown tool") or "Unknown tool"
                kind = getattr(update, "kind", None)
                kind_str = getattr(kind, "value", str(kind)) if kind else "other"
                tool_call_id = (
                    getattr(update, "tool_call_id", None)
                    or getattr(update, "toolCallId", None)
                    or "call_default"
                )
                self._recorder.add_tool_call(tool_call_id, title, kind_str)
            elif session_update == "tool_call_update":
                status = getattr(update, "status", None)
                title = getattr(update, "title", None)
                kind = getattr(update, "kind", None)
                kind_str = getattr(kind, "value", str(kind)) if kind else None
                content = getattr(update, "content", None)
                tool_call_id = (
                    getattr(update, "tool_call_id", None)
                    or getattr(update, "toolCallId", None)
                    or "call_default"
                )
                content_str = _format_content_blocks(content) if content else None
                self._recorder.update_tool_call(
                    tool_call_id, status, title, content_str
                )
            elif session_update == "usage_update":
                used = getattr(update, "used", None)
                size = getattr(update, "size", None)
                cost = getattr(update, "cost", None)
                self._recorder.set_usage({"used": used, "size": size, "cost": cost})
            elif session_update == "plan":
                entries = getattr(update, "entries", None) or []
                self._recorder.set_plan(entries)
            elif session_update == "available_commands_update":
                cmds = getattr(update, "available_commands", None) or getattr(
                    update, "availableCommands", None
                )
                if cmds:
                    from . import agent_cache

                    try:
                        agent_cache.update_commands(self._agent_binary, cmds)
                    except Exception:
                        pass

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
        # URL mode is not supported by this CLI client
        requested_schema = getattr(mode, "requested_schema", None)
        if requested_schema is None:
            return acp_schema.DeclineElicitationResponse(action="decline")

        if self._silent:
            return acp_schema.DeclineElicitationResponse(action="decline")

        from .elicitation import render_form

        result = render_form(
            message=message,
            schema=requested_schema,
            auto_accept=self._auto_approve,
        )
        if result is None:
            return acp_schema.DeclineElicitationResponse(action="decline")
        return acp_schema.AcceptElicitationResponse(action="accept", content=result)

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
        silent: bool = False,
        transcript_recorder: TranscriptRecorder | None = None,
    ) -> None:
        self.project_root_path = project_root
        self.agent_binary = agent_binary
        self.session_name = session_name
        self._auto_approve = auto_approve
        self._verbose = verbose
        self._read_only = read_only
        self._silent = silent
        self._transcript_recorder = transcript_recorder

        self._conn: ClientSideConnection | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._transport_ctx: AbstractAsyncContextManager | None = None
        self._session_id: str | None = None

    async def start(
        self,
        target: str | None = None,
        *,
        load_existing: bool = True,
        model_override: str | None = None,
        mode_override: str | None = None,
    ) -> None:
        capabilities = acp_schema.ClientCapabilities(
            fs=acp_schema.FileSystemCapabilities(
                read_text_file=True,
                write_text_file=not self._read_only,
            ),
            elicitation=acp_schema.ElicitationCapabilities(
                form=acp_schema.ElicitationFormCapabilities(),
            ),
            session=acp_schema.ClientSessionCapabilities(
                config_options=acp_schema.SessionConfigOptionsCapabilities(
                    boolean=acp_schema.BooleanConfigOptionCapabilities()
                )
            ),
            terminal=True,
            plan=acp_schema.PlanCapabilities(),
            field_meta={},
        )

        client = AgentClient(
            auto_approve=self._auto_approve,
            read_only=self._read_only,
            silent=self._silent,
            recorder=self._transcript_recorder,
            agent_binary=self.agent_binary,
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
        if not self._silent and session_resp:
            display_initial_session_info(session_resp)

        # Apply model override or config-defined default model if set
        from .config import Config

        model_to_set = model_override
        if not model_to_set:
            try:
                config = Config.load()
                model_to_set = config.get_default_model(self.agent_binary)
            except Exception:
                pass

        if model_to_set:
            try:
                await self.set_model(model_to_set)
            except Exception as e:
                # Do not fail start if setting the model fails (e.g. unsupported option/method)
                if not self._silent:
                    _console.print(
                        f"[yellow]Warning: Failed to auto-set model"
                        f" '{model_to_set}': {e}[/yellow]"
                    )

        # Apply mode override or config-defined default mode if set
        mode_to_set = mode_override
        if not mode_to_set:
            try:
                config = Config.load()
                mode_to_set = config.get_default_mode(self.agent_binary)
            except Exception:
                pass

        if mode_to_set:
            try:
                await self.set_mode(mode_to_set)
            except Exception as e:
                # Do not fail start if setting the mode fails (e.g. unsupported option/method)
                if not self._silent:
                    _console.print(
                        f"[yellow]Warning: Failed to auto-set mode"
                        f" '{mode_to_set}': {e}[/yellow]"
                    )

    async def send_prompt(
        self, prompt: str, resources: list[Path] | None = None
    ) -> str | None:
        if self._conn is None or self._session_id is None:
            raise RuntimeError("Agent not started")

        from acp import text_block, resource_link_block
        from acp.schema import (
            TextContentBlock,
            ImageContentBlock,
            AudioContentBlock,
            ResourceContentBlock,
            EmbeddedResourceContentBlock,
        )

        blocks: list[
            TextContentBlock
            | ImageContentBlock
            | AudioContentBlock
            | ResourceContentBlock
            | EmbeddedResourceContentBlock
        ] = [text_block(prompt)]
        if resources:
            for r_path in resources:
                blocks.append(
                    resource_link_block(
                        name=r_path.name,
                        uri=f"file://{r_path.absolute()}",
                        size=r_path.stat().st_size if r_path.exists() else None,
                    )
                )

        resp = await self._conn.prompt(
            self._session_id,
            blocks,
        )
        _debug_log(self._verbose, "prompt", resp)
        stop_reason = str(resp.stop_reason)
        format_stop_reason(stop_reason)
        if self._transcript_recorder:
            self._transcript_recorder.set_stop_reason(stop_reason)
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
        return mode_id if resp else None

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

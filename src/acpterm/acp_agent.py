from __future__ import annotations

import asyncio
import contextlib
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any
import warnings

from acp import schema as acp_schema
from acp.client.connection import ClientSideConnection
from acp.connection import StreamDirection, StreamEvent
from acp.exceptions import RequestError
from acp.transports import spawn_stdio_transport
from rich.console import Console
from rich.prompt import Confirm

from .config import resolve_agent_command
from .hooks import HookDefinition
from .output import (
    _state,
    display_initial_session_info,
    format_session_update,
    format_stop_reason,
)
from .session_store import get as get_saved_session
from .transcript import TranscriptRecorder


# Suppress serialization UserWarnings emitted by Pydantic for experimental ACP extension fields (e.g. auth)
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")


if False:  # TYPE_CHECKING
    from .hooks import HookDefinition


PROTOCOL_VERSION = 1
_console = Console(highlight=False)


def _verbose_stream_observer(event: StreamEvent) -> None:
    """Log every JSON-RPC message sent or received when --verbose is active."""
    import json

    msg = event.message
    direction = "\u2192" if event.direction == StreamDirection.OUTGOING else "\u2190"
    method = msg.get("method", "")
    has_id = "id" in msg

    if method:
        label = f"{direction} {method}"
    elif has_id:
        label = f"{direction} response (id={msg['id']})"
    else:
        label = f"{direction} message"

    # Streaming notifications: compact single-line summary, no body dump
    is_notification = method and not has_id
    if is_notification and method == "session/update":
        params = msg.get("params", {})
        update = params.get("update", params)
        update_type = (
            update.get("sessionUpdate", "?") if isinstance(update, dict) else "?"
        )
        # Only collapse streaming text chunks -- tool calls, usage, etc. still dump full body
        if update_type in ("agent_message_chunk", "agent_thought_chunk"):
            _console.print(f"[dim]  {direction} {update_type}[/dim]")
            return

    _console.print(f"\n[dim]--- {label} ---[/dim]")
    body = msg.get("result") or msg.get("error") or msg.get("params", {})
    rendered = (
        json.dumps(body, indent=2) if isinstance(body, (dict, list)) else repr(body)
    )
    _console.print(rendered)
    _console.print("[dim]---[/dim]")


class AgentClient:
    """Implements the ACP `Client` protocol to receive session updates from the agent."""

    def __init__(
        self,
        auto_approve: bool = False,
        silent: bool = False,
        read_only: bool = False,
        recorder: TranscriptRecorder | None = None,
        agent_binary: str = "opencode",
        permission_hooks: list[HookDefinition] | None = None,
        project_root: Path | None = None,
    ) -> None:
        self._auto_approve = auto_approve
        self._silent = silent
        self._read_only = read_only
        self._recorder = recorder
        self._agent_binary = agent_binary
        self._permission_hooks = permission_hooks or []
        self._project_root = project_root or Path.cwd()

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
                raw_input = getattr(update, "raw_input", None) or getattr(
                    update, "rawInput", None
                )
                self._recorder.add_tool_call(
                    tool_call_id, title, kind_str, raw_input=raw_input
                )
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
                raw_output = getattr(update, "raw_output", None) or getattr(
                    update, "rawOutput", None
                )
                # Extract diff paths from content blocks for richer transcript
                if content:
                    self._record_diff_paths(content)
                self._recorder.update_tool_call(
                    tool_call_id, status, title, content_str, raw_output=raw_output
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

                    with contextlib.suppress(Exception):
                        agent_cache.update_commands(self._agent_binary, cmds)

    def _record_diff_paths(self, content_blocks: list[Any]) -> None:
        """Extract file paths from diff content blocks and record as file operations."""
        if not self._recorder:
            return
        for block in content_blocks:
            block_type = getattr(block, "type", None)
            if block_type != "diff":
                continue
            # v1: path field directly on the diff block
            diff_path = getattr(block, "path", None)
            if diff_path:
                old_text = getattr(block, "old_text", None) or getattr(
                    block, "oldText", None
                )
                op = "create" if old_text is None else "modify"
                self._recorder.add_file_operation(op, diff_path)
            # v2: changes[] array with structured operations
            changes = getattr(block, "changes", None)
            if changes:
                for change in changes:
                    operation = getattr(change, "operation", "modify")
                    change_path = getattr(change, "path", None)
                    if change_path:
                        self._recorder.add_file_operation(operation, change_path)

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
        target_path = getattr(tool_call, "path", None) or getattr(
            tool_call, "uri", None
        )

        if not self._silent:
            _console.print(f"\n[yellow][perm][/yellow] {title} [dim]({kind_str})[/dim]")

        # Check for permission hooks if defined
        if self._permission_hooks:
            from .hooks import HookEvent, run_permission_hook

            event = HookEvent(
                event_type="permission",
                session_id=session_id,
                agent_name=self._agent_binary,
                cwd=self._project_root,
                extra={
                    "title": title,
                    "kind": kind_str,
                    "path": str(target_path) if target_path else "",
                },
            )
            hook_code = await run_permission_hook(
                event, self._permission_hooks, verbose=not self._silent
            )
            if hook_code == 0:
                option_id = options[0].option_id if options else "allow_always"
                if self._recorder:
                    self._recorder.add_permission(title, kind_str, "approved")
                return acp_schema.RequestPermissionResponse(
                    outcome=acp_schema.AllowedOutcome(
                        outcome="selected",
                        option_id=option_id,
                    )
                )
            if hook_code == 1:
                if self._recorder:
                    self._recorder.add_permission(title, kind_str, "denied")
                return acp_schema.RequestPermissionResponse(
                    outcome=acp_schema.DeniedOutcome(outcome="cancelled")
                )

        if self._silent or self._auto_approve:
            if not self._silent:
                _console.print("[dim]  Auto-approved (--yes)[/dim]")
            option_id = options[0].option_id if options else "allow_always"
            if self._recorder:
                self._recorder.add_permission(title, kind_str, "approved")
            return acp_schema.RequestPermissionResponse(
                outcome=acp_schema.AllowedOutcome(
                    outcome="selected",
                    option_id=option_id,
                )
            )

        approved = Confirm.ask("  Allow?", default=True)
        if approved and options:
            option_id = options[0].option_id
            if self._recorder:
                self._recorder.add_permission(title, kind_str, "approved")
            return acp_schema.RequestPermissionResponse(
                outcome=acp_schema.AllowedOutcome(
                    outcome="selected",
                    option_id=option_id,
                )
            )
        if self._recorder:
            self._recorder.add_permission(title, kind_str, "denied")
        return acp_schema.RequestPermissionResponse(
            outcome=acp_schema.DeniedOutcome(outcome="cancelled")
        )

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kwargs: Any
    ) -> acp_schema.WriteTextFileResponse:
        if self._read_only:
            raise RuntimeError("File modifications are disabled in read-only mode")
        if not self._silent:
            file_path = self._project_root / path
            file_path.write_text(content)
        if self._recorder:
            self._recorder.add_file_operation("write", path)
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
        file_path = self._project_root / path
        text = file_path.read_text()
        lines = text.splitlines()
        if line is not None:
            start = line - 1
            end = start + limit if limit else None
            lines = lines[start:end]
        if self._recorder:
            self._recorder.add_file_operation("read", path, line=line, limit=limit)
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
        from .auth import resolve_auth_token

        result = await resolve_auth_token(
            agent_name=self._agent_binary,
            method=method,
            params=params,
        )
        if result:
            return result
        raise RequestError.method_not_found(method)

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
        permission_hooks: list[HookDefinition] | None = None,
    ) -> None:
        self.project_root_path = project_root
        self.agent_binary = agent_binary
        self.session_name = session_name
        self._auto_approve = auto_approve
        self._verbose = verbose
        self._read_only = read_only
        self._silent = silent
        self._transcript_recorder = transcript_recorder
        self._permission_hooks = permission_hooks or []

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
            auth=acp_schema.AuthCapabilities(terminal=False),
            field_meta={},
        )

        client = AgentClient(
            auto_approve=self._auto_approve,
            read_only=self._read_only,
            silent=self._silent,
            recorder=self._transcript_recorder,
            agent_binary=self.agent_binary,
            permission_hooks=self._permission_hooks,
            project_root=self.project_root_path,
        )
        import os

        cmd = resolve_agent_command(self.agent_binary)
        self._transport_ctx = spawn_stdio_transport(
            cmd[0], *cmd[1:], env=dict(os.environ)
        )
        reader, writer, self._process = await self._transport_ctx.__aenter__()  # type: ignore[func-returns-value]

        self._conn = ClientSideConnection(client, writer, reader)

        if self._verbose:
            self._conn._conn.add_observer(_verbose_stream_observer)

        cwd = str(self.project_root_path.absolute())
        init_resp = await self._conn.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=capabilities,
            client_info=acp_schema.Implementation(
                name="acpterm",
                title="ACP Terminal Client",
                version="0.1.0",
            ),
        )

        if target is None and load_existing:
            saved_session_id = get_saved_session(
                self.agent_binary, cwd, self.session_name
            )
            if saved_session_id:
                target = saved_session_id

        session_resp = None
        if target:
            try:
                agent_caps = getattr(init_resp, "agent_capabilities", None) or getattr(
                    init_resp, "agentCapabilities", None
                )
                sess_caps = (
                    getattr(agent_caps, "session_capabilities", None)
                    or getattr(agent_caps, "sessionCapabilities", None)
                    if agent_caps
                    else None
                )
                can_resume = (
                    bool(getattr(sess_caps, "resume", None)) if sess_caps else False
                )

                if can_resume:
                    session_resp = await self._conn.resume_session(
                        session_id=target, cwd=cwd
                    )
                else:
                    session_resp = await self._conn.load_session(
                        cwd=cwd, session_id=target
                    )
                self._session_id = target
            except Exception as e:
                from . import session_store

                if not self._silent:
                    _console.print(
                        f"[yellow]Warning: Could not resume session '{target}' ({e}). Starting a new session...[/yellow]"
                    )
                session_store.remove(self.agent_binary, cwd, self.session_name)
                target = None
                session_resp = None

        if session_resp is None:
            session_resp = await self._conn.new_session(cwd=cwd, mcp_servers=[])
            self._session_id = session_resp.session_id if session_resp else None

        if not self._silent and session_resp:
            display_initial_session_info(session_resp)

        # Store updated config options & modes in cache automatically
        if session_resp:
            from . import agent_cache

            co = getattr(session_resp, "config_options", None) or getattr(
                session_resp, "configOptions", None
            )
            modes = getattr(session_resp, "modes", None)
            with contextlib.suppress(Exception):
                agent_cache.store(self.agent_binary, config_options=co, modes=modes)

        # Apply model override or config-defined default model if set
        import difflib

        from . import agent_cache
        from .config import Config

        model_to_set = model_override
        if not model_to_set:
            with contextlib.suppress(Exception):
                config = Config.load()
                model_to_set = config.get_default_model(self.agent_binary)

        if model_to_set:
            valid_models: list[str] = []
            co_opts = agent_cache.get_config_options(self.agent_binary) or []
            for opt in co_opts:
                if opt.get("id") == "model":
                    valid_models = [
                        o["id"] for o in opt.get("options", []) if o.get("id")
                    ]

            if valid_models and model_to_set not in valid_models:
                matches = difflib.get_close_matches(
                    model_to_set, valid_models, n=1, cutoff=0.5
                )
                if not self._silent:
                    if matches:
                        _console.print(
                            f"[yellow]Warning: Model '{model_to_set}' is not recognized for agent '{self.agent_binary}'. Did you mean '{matches[0]}'?[/yellow]"
                        )
                    else:
                        _console.print(
                            f"[yellow]Warning: Model '{model_to_set}' is not recognized for agent '{self.agent_binary}'. Available models: {', '.join(valid_models)}[/yellow]"
                        )
            else:
                try:
                    await self.set_model(model_to_set)
                    if not self._silent:
                        _console.print(f"[dim]\\[model][/dim] {model_to_set}")
                        _state._model_displayed = True
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
            with contextlib.suppress(Exception):
                config = Config.load()
                mode_to_set = config.get_default_mode(self.agent_binary)

        if mode_to_set:
            valid_modes: list[str] = []
            cached_modes = agent_cache.get_modes(self.agent_binary)
            if cached_modes and cached_modes.get("available_modes"):
                valid_modes = [
                    m["id"] for m in cached_modes["available_modes"] if m.get("id")
                ]

            if valid_modes and mode_to_set not in valid_modes:
                matches = difflib.get_close_matches(
                    mode_to_set, valid_modes, n=1, cutoff=0.5
                )
                if not self._silent:
                    if matches:
                        _console.print(
                            f"[yellow]Warning: Mode '{mode_to_set}' is not recognized for agent '{self.agent_binary}'. Did you mean '{matches[0]}'?[/yellow]"
                        )
                    else:
                        _console.print(
                            f"[yellow]Warning: Mode '{mode_to_set}' is not recognized for agent '{self.agent_binary}'. Available modes: {', '.join(valid_modes)}[/yellow]"
                        )
            else:
                try:
                    await self.set_mode(mode_to_set)
                    if not self._silent:
                        _console.print(f"[dim]\\[mode][/dim] {mode_to_set}")
                        _state._mode_displayed = True
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

        from acp import resource_link_block, text_block
        from acp.schema import (
            AudioContentBlock,
            EmbeddedResourceContentBlock,
            ImageContentBlock,
            ResourceContentBlock,
            TextContentBlock,
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
        try:
            await self._conn.set_config_option(
                config_id="model",
                session_id=self._session_id,
                value=model_id,
            )
        except Exception:
            # agent-client-protocol v0.11.0 lacks set_session_model().
            # Fallback: raw session/set_model request (supported by opencode v1.2.24+).
            # TODO: replace with self._conn.set_session_model() when the SDK ships it.
            await self._conn._conn.send_request(
                "session/set_model",
                {"sessionId": self._session_id, "modelId": model_id},
            )

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def close_session(self) -> None:
        if self._conn is not None and self._session_id is not None:
            with contextlib.suppress(Exception):
                await self._conn.close_session(session_id=self._session_id)

    async def stop(self) -> None:
        # Terminate the subprocess first so the receive loop exits
        # cleanly before we close the connection and its message queue.
        if self._process is not None:
            with contextlib.suppress(Exception):
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
        if self._conn:
            with contextlib.suppress(Exception):
                await self._conn.close()
            self._conn = None
        if self._transport_ctx:
            with contextlib.suppress(Exception):
                await self._transport_ctx.__aexit__(None, None, None)
            self._transport_ctx = None
        self._process = None
        self._session_id = None

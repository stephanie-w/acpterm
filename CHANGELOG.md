# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-11-15

### Changed

- **Breaking**: raised the `agent-client-protocol` floor from `>=0.11.0,<1.0.0` to
  `>=1.0.0,<2.0.0`. Downstream projects pinned to the `0.11.x` SDK will now fail to
  resolve `acpterm`.
- **Breaking**: expanded the `AgentClient` protocol with four MCP-over-ACP handlers
  (`connect_mcp`, `disconnect_mcp`, `mcp_message`, `notify_mcp`). Subclasses of
  `AgentClient` must implement them, or inherit the default `RequestError` / no-op
  behavior.

### Added

- MCP-over-ACP stub handlers on `AgentClient` for protocol conformance (not opted in).

## [0.1.0] - unreleased

### Added

- Initial `acpterm` CLI client for the Agent Client Protocol.

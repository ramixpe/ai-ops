"""The MCP transport seam: one reusable stdio client over the MCP SDK.

Extracted from `cli._cmd_inspect`, which used to build a `ClientSession` +
`stdio_client` inline and was the only caller (Phase 8's smoke test). Pulling
it out here does two things at once:

1. **Reuse.** A second caller (a future agent loop, a health-check script)
   gets a tested, sync `McpToolset` instead of copying the inline
   `asyncio.run`/`StdioServerParameters` dance a second time.
2. **The environment-forwarding fix, made structural.** `mcp.client.stdio.
   stdio_client` merges `get_default_environment()` (on POSIX: `HOME`,
   `LOGNAME`, `PATH`, `SHELL`, `TERM`, `USER` -- see that function's own
   source) with whatever is passed as `StdioServerParameters(env=...)`. It
   does **not** forward the caller's `os.environ` on its own. Before this
   module existed, `_cmd_inspect` built the forwarded dict inline; every
   *other* imagined caller (there was only ever the one) would have had to
   remember to copy that same dict or silently lose `NETTOOLS_*`/device
   credentials to the spawned child -- for `NETTOOLS_MCP_SURFACE` specifically,
   that is exactly how `NETTOOLS_MCP_SURFACE=staged nettools inspect` used to
   silently inspect the classic surface instead (operator walkthrough,
   stumble 7). `_forwarded_operator_env` below is that dict, built once;
   `child_server_env` goes further and makes three specific values immune to
   the operator's own environment entirely -- see its docstring.

Lazy `mcp` import
------------------
`mcp` is a required dependency (`pyproject.toml`), unlike `neo4j`/`pynetbox`
(true optional extras) -- but `_cmd_inspect`'s own comment already framed
"the rest of the CLI works without the MCP SDK installed" as a property worth
keeping regardless: a broken wheel, a `pip install --no-deps`, or a future
demotion to an extra should degrade this one command, not take the whole CLI
down at import time. So `mcp` is imported only inside `McpToolset.__enter__`,
never at this module's top level -- `tests/test_mcp_client.py` proves the
module imports with `mcp` simulated absent (a required dependency can't be
proven *genuinely* absent in this venv the way `test_graph.py`/`test_netbox.py`
prove it for `neo4j`/`pynetbox`; simulating it via `sys.modules["mcp"] = None`
is the same fix `test_graph.py`'s `write_graph` test already uses for the
identical reason) and that using the toolset without it fails at `__enter__`,
never at import.

Async is an implementation detail
-----------------------------------
The MCP SDK is async top to bottom (`ClientSession`, `stdio_client`). Every
caller imagined for this module so far -- `cli._cmd_inspect`, a bounded
tool-calling loop -- is itself synchronous, and a caller that already has its
own asyncio loop running can always reach the SDK directly rather than through
this facade. `McpToolset` keeps one private event loop alive for the lifetime
of the `with` block (created in `__enter__`, closed in `__exit__`) rather than
`asyncio.run()` per call: `asyncio.run()` tears its loop down (and, with it,
the subprocess's stdio streams) when the block it wraps returns, so it cannot
back a session used across more than one call. A background thread driven
with `run_coroutine_threadsafe` was considered and rejected for the same
reason the next paragraph explains stdio_client's ENTER and EXIT specifically
cannot be split across ordinary calls -- moving the split to a second thread
does not change which *task* owns it.

**Why `list_tools`/`call_tool` are plain `loop.run_until_complete(coro)` calls
but opening and closing the session are not, measured not assumed:** an
earlier version of this module called `stdio_client(params).__aenter__()` in
one `run_until_complete` and `AsyncExitStack.aclose()` in a later, separate
one -- symmetric with how `list_tools`/`call_tool` are written below, and it
looked right. It crashed on every real run, in `__exit__`, with anyio's own
``RuntimeError: Attempted to exit cancel scope in a different task than it
was entered in``. `run_until_complete(coro)` wraps a bare coroutine in a
**new** `asyncio.Task` every time it is called; `stdio_client`/`ClientSession`
each open an `anyio` task group internally, and an anyio cancel scope may only
be exited by the task that entered it. Two calls to `run_until_complete` are
two different tasks, so entering in one and exiting in the other is exactly
the failure anyio's own message names -- confirmed by removing the split (this
module's actual shape) and watching the `RuntimeError` disappear. Individual
`list_tools`/`call_tool` calls do not hit this: each is self-contained (open
nothing that outlives its own `await`), so running each in its own
`run_until_complete`-created task, even a *different* task each time, is
fine -- confirmed by every result printed in a real `nettools inspect` run
against the live lab before this docstring was written.

So `__enter__` starts one long-lived coroutine (`_lifecycle`, run as its own
`asyncio.Task` via `loop.create_task`) that opens `stdio_client`/`ClientSession`
and then suspends on an `asyncio.Future` (`_close_requested`) until `__exit__`
resolves it -- keeping the SAME task alive, suspended rather than finished,
for the whole `with` block, so the eventual `async with` exits run in the
identical task that ran the entries. `__enter__` itself only waits for a
second future (`_ready`) that `_lifecycle` resolves once `session.initialize()`
succeeds (or fails) -- `run_until_complete` on a bare `Future` does not create
a task of its own, so it does not compete with `_lifecycle`'s. `list_tools`/
`call_tool` run in between as ordinary, independent `run_until_complete`
calls against the already-open `session`, undisturbed by `_lifecycle` sitting
suspended on the same loop.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ToolDescriptor",
    "ResourceDescriptor",
    "PromptDescriptor",
    "ToolCallResult",
    "McpToolset",
    "child_server_env",
]


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ResourceDescriptor:
    """`list_resources`'s element type -- not in this task's required Public
    API, but `_cmd_inspect` prints resources too, and the whole point of
    extracting this module is that it prints them through the same sync
    facade as everything else, not by reaching around it at the raw SDK."""

    uri: str
    description: str


@dataclass(frozen=True)
class PromptDescriptor:
    """`list_prompts`'s element type -- see `ResourceDescriptor` above."""

    name: str
    description: str


@dataclass(frozen=True)
class ToolCallResult:
    name: str
    arguments: dict[str, Any]
    text: str
    is_error: bool
    elapsed_s: float
    truncated_chars: int | None


# --------------------------------------------------------------------------- #
# child_server_env -- the env a SPAWNED mcp_server.server child gets.
# --------------------------------------------------------------------------- #

#: Forwarded verbatim from the operator's own process environment: every
#: `NETTOOLS_*` setting (inventory path, timeouts, evidence backend, ...) plus
#: the three names credentials can arrive under. This is `_cmd_inspect`'s own
#: pre-extraction dict, unchanged -- the deliberate-forwarding half of the fix
#: described in this module's docstring. Device credentials are named
#: explicitly rather than swept up by a prefix because they do NOT start with
#: `NETTOOLS_` (`.env.example`'s own naming) and a spawned server needs them
#: exactly as much as the CLI process does whenever the operator's own shell
#: -- not a `.env` file the child can find on its own -- is where they live.
_FORWARDED_CREDENTIAL_NAMES = ("DEVICE_USERNAME", "DEVICE_PASSWORD", "DEVICE_SSH_KEYFILE")


def _forwarded_operator_env() -> dict[str, str]:
    """Deliberate env forwarding for a spawned `mcp_server.server` child.

    See this module's own docstring ("The environment-forwarding fix, made
    structural") for the SDK behaviour this exists to work around. This is
    the WHOLE fix for every `NETTOOLS_*`/credential value except the three
    `FORCED_ENV_NAMES` -- those three additionally get `child_server_env`'s
    unconditional override (below) when a caller opts into it; this function
    alone is what `McpToolset` uses by default, so a caller that never passes
    `env_overrides` (`_cmd_inspect`, today) gets exactly the forwarding it
    always had -- an operator's own `NETTOOLS_MCP_SURFACE=staged` (or any
    other `NETTOOLS_*` value) reaches the child unmodified.
    """

    return {
        name: value
        for name, value in os.environ.items()
        if name.startswith("NETTOOLS_") or name in _FORWARDED_CREDENTIAL_NAMES
    }


def child_server_env(
    *,
    surface: str = "staged",
    allow_active_probes: bool = False,
    allow_external_sources: bool = True,
) -> dict[str, str]:
    """The env a SPAWNED `mcp_server.server` child gets, chosen deliberately
    rather than inherited by accident of whatever the operator's shell or
    `.env` happens to hold.

    We **spawn** the MCP server as a subprocess (`McpToolset.__enter__`), so
    its environment is entirely ours to set -- there is no ambient
    inheritance to reason about, only what we choose to put in the dict
    handed to `StdioServerParameters(env=...)`. This function is that choice,
    made explicit and reusable, for the two knobs that gate what an MCP
    client on this surface can make the fabric or the network do:

    * ``NETTOOLS_MCP_SURFACE`` (default here: ``"staged"``) -- the 6-tool
      wide/narrow gradient in `mcp_server/staged_surface.py`, not the
      37-tool classic surface. `mcp_server/server.py`'s own `_select_surface`
      already fails CLOSED to `"staged"` on an unrecognised value (an
      explicitly-set typo narrows rather than widens -- see that function's
      EER-008b docstring); this function does not depend on that fallback
      for correctness (it only ever emits the literal strings `"classic"` or
      `"staged"`, validated below), but the two mechanisms agree in
      direction, not by coincidence: both treat "narrower" as the safe
      default when something is unclear.

    * ``NETTOOLS_MCP_ALLOW_ACTIVE_PROBES`` (default here: ``"0"``, i.e.
      *disabled*) -- set in the child **regardless of the operator's own
      `.env` or shell environment**. `MCP-EXPERIMENT.md` §12.3 measured a
      model firing `get_lab_ping` completely unprompted mid-investigation
      ("initiative scales with capability" -- the model most likely to start
      probing on its own is the capable one), and `server.py`'s own comment
      block records that tool annotations (`readOnlyHint`/`openWorldHint`)
      were measured as ignored by that client -- signalling, not enforcement.
      `NETTOOLS_MCP_ALLOW_ACTIVE_PROBES` is the actual enforcement:
      `_active_probes_refused` fires *inside the server*, before
      `ping_device`/`traceroute_device`/`probe_lab`'s body ever runs, so a
      refused call generates zero packets. Forcing this value here rather
      than merely defaulting it means a caller cannot accidentally inherit an
      operator's `NETTOOLS_MCP_ALLOW_ACTIVE_PROBES=true` (set, say, so a
      *different*, human-driven MCP client can probe) into an unattended
      caller of this function -- getting that back requires **asking for it
      explicitly** (``allow_active_probes=True``), not merely having it
      already sitting in the environment.

      **Why regardless-of-`.env` is actually true, not just asserted:** the
      value this function returns becomes a real subprocess environment
      variable at spawn time (`_create_platform_compatible_process`, inside
      the SDK's `stdio_client`) -- it exists in the child's `os.environ`
      *before* `mcp_server.server.main()` ever runs. `main()` calls
      `load_dotenv()` to pick up the child's own `.env`, and `python-dotenv`'s
      `load_dotenv()` never overrides an already-set environment variable by
      default (confirmed by `_select_surface`'s own docstring: "`load_dotenv()`
      never overrides an already-set variable"). So a `.env` file the child
      finds on `NETTOOLS_MCP_ALLOW_ACTIVE_PROBES=true` cannot un-force what
      this function set -- the spawn-time value wins structurally, not by
      convention.

    * ``NETTOOLS_MCP_ALLOW_EXTERNAL_SOURCES`` (default here: ``"1"``, i.e.
      *enabled*) -- the opposite default from active probes, deliberately:
      `server.py`'s own comment above `_external_source_tool` gives the full
      reasoning (no caller-controlled destination, so no SSRF-shaped risk;
      an allowlisted query surface, not free text). Forced the same way as
      the other two, for the same reason: explicit, not inherited.

    ``surface`` is validated against the two names the server itself
    recognises (`"classic"`/`"staged"`) -- raising `ValueError` here on
    anything else, rather than silently forwarding a typo for the server's
    own fail-closed logic to catch later with only a log line the caller of
    *this* function might never see.
    """

    if surface not in ("classic", "staged"):
        raise ValueError(f"surface must be 'classic' or 'staged', not {surface!r}")

    env = _forwarded_operator_env()
    env["NETTOOLS_MCP_SURFACE"] = surface
    env["NETTOOLS_MCP_ALLOW_ACTIVE_PROBES"] = "1" if allow_active_probes else "0"
    env["NETTOOLS_MCP_ALLOW_EXTERNAL_SOURCES"] = "1" if allow_external_sources else "0"
    return env


# --------------------------------------------------------------------------- #
# truncated_chars -- what boundary.sanitize withheld, measured from the
# ALREADY-SANITISED text this client receives (never from anything raw: this
# process never sees the device text sanitize() stripped, only the record
# that it did).
# --------------------------------------------------------------------------- #


def _measure_truncated_chars(text: str) -> int | None:
    """How many characters `mcp_server.boundary.sanitize` withheld from one
    call's result, or `None` when that cannot be measured from `text` alone.

    Every tool envelope this server returns is JSON; `sanitize()` marks each
    raw-text key it strips with a sibling `<field>_withheld` key. This module
    deliberately does not import `mcp_server.boundary`'s own `RAW_TEXT_KEYS`
    table to recognise them -- `mcp_server` depends on `agent_nettools`,
    never the reverse (`boundary.py`'s own docstring) -- so it walks the
    parsed JSON looking for the `_withheld` suffix `sanitize()` writes, not
    the specific field names that currently produce it.

    Two shapes exist today, and only one carries a character count:

    * ``commands_withheld``: ``{command: {"chars": N, "lines": M, ...}}`` --
      the dominant vector (up to ~38 kB for one call, 2026-08-17 audit). Every
      `chars` value found anywhere in the payload is summed; this is the only
      source of a real character count.
    * any other ``<field>_withheld`` key (today: ``unaccounted_lines_withheld``)
      is a **count of lines or items**, not characters -- real withholding
      whose size in characters this function cannot state. A value of
      exactly `0` means nothing was actually withheld under that key (a
      genuinely measured zero, same as no `commands_withheld` entries at
      all); a positive value means something WAS withheld and this function
      does not know how large it was in characters.

    **Absence is never zero.** If nothing character-measurable was withheld
    (``known_chars == 0``) but a same-payload marker records real,
    unmeasured withholding, this returns `None` rather than `0` -- reporting
    `0` there would read as "nothing was withheld", which is false: something
    was, this function just cannot say how many characters it was. When
    `known_chars` is already positive, the return value is an honest
    (possibly incomplete) lower bound, not a lie in the `0` direction, so it
    is returned as-is rather than also collapsing to `None`.

    Returns `None` when `text` is not this server's JSON envelope at all --
    e.g. an SDK/protocol-level message like `"Unknown tool: ..."`, which
    never passed through `sanitize()` and carries no withholding information
    to measure one way or the other.
    """

    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None

    known_chars = 0
    unmeasured = False

    def walk(node: Any) -> None:
        nonlocal known_chars, unmeasured
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "commands_withheld" and isinstance(value, dict):
                    for entry in value.values():
                        chars = entry.get("chars") if isinstance(entry, dict) else None
                        if isinstance(chars, int) and not isinstance(chars, bool):
                            known_chars += chars
                        else:
                            unmeasured = True
                elif key.endswith("_withheld"):
                    if isinstance(value, int) and not isinstance(value, bool) and value <= 0:
                        pass  # a measured zero under this key -- nothing withheld here
                    else:
                        unmeasured = True
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)

    if unmeasured and known_chars == 0:
        return None
    return known_chars


# --------------------------------------------------------------------------- #
# McpToolset
# --------------------------------------------------------------------------- #


class McpToolset:
    """Sync facade over one stdio MCP session talking to `mcp_server.server`.

    Context manager: `__enter__` spawns the server, opens a `ClientSession`
    over stdio, and calls `session.initialize()`; `__exit__` tears both down.
    `list_tools`/`call_tool` are ordinary synchronous methods usable any
    number of times inside the `with` block -- see this module's docstring
    ("Async is an implementation detail") for why one private event loop
    backs all of them instead of a fresh `asyncio.run()` per call.

    ``env_overrides``, if given, is layered ON TOP of `_forwarded_operator_env()`
    (last-write-wins per key) -- so a caller gets the operator's own
    `NETTOOLS_*`/credential environment by default, and may force specific
    keys away from it by passing them here. `child_server_env()` is exactly
    such a dict, built for the unattended-caller case; `_cmd_inspect` (a
    human typing a diagnostic command) passes none at all, which is why its
    printed output is unchanged by this module's existence -- it gets
    precisely the forwarding it always had, nothing forced.
    """

    def __init__(
        self,
        *,
        env_overrides: Mapping[str, str] | None = None,
        startup_timeout_s: float = 30.0,
    ) -> None:
        self._env_overrides = dict(env_overrides) if env_overrides else {}
        self._startup_timeout_s = startup_timeout_s
        self._loop: Any = None
        self._lifecycle_task: Any = None
        self._close_requested: Any = None
        self._closed: Any = None
        self._session: Any = None

    def __enter__(self) -> "McpToolset":
        # Imported here, not at module level -- see this module's docstring
        # ("Lazy `mcp` import"). Anything that raises above this point (the
        # import itself) needs no cleanup: nothing has been opened yet.
        import asyncio

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = _forwarded_operator_env()
        env.update(self._env_overrides)
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "mcp_server.server"], env=env,
        )

        loop = asyncio.new_event_loop()
        ready: Any = loop.create_future()
        close_requested: Any = loop.create_future()
        closed: Any = loop.create_future()

        async def _lifecycle() -> None:
            # Runs as ONE task for the whole `with` block -- see this
            # module's docstring for why that is load-bearing (anyio's
            # cancel scopes are task-bound; splitting entry and exit across
            # separate `run_until_complete` calls, each its own task, is
            # exactly what used to raise anyio's "different task" RuntimeError
            # here). `await close_requested` is where this task spends nearly
            # all its life: suspended, not finished, so `stdio_client`'s and
            # `ClientSession`'s `async with` blocks stay open across however
            # many `list_tools`/`call_tool` calls happen while this task
            # waits -- each of those runs as its own, separate, short-lived
            # task and is unaffected by this one sitting suspended alongside
            # it on the same loop.
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        try:
                            await asyncio.wait_for(
                                session.initialize(), timeout=self._startup_timeout_s
                            )
                        except BaseException as exc:
                            if not ready.done():
                                ready.set_exception(exc)
                            return
                        if not ready.done():
                            ready.set_result(session)
                        await close_requested
            finally:
                if not closed.done():
                    closed.set_result(None)

        lifecycle_task = loop.create_task(_lifecycle())

        try:
            # A bare Future, not a coroutine -- run_until_complete does not
            # wrap it in a new task, so this does not compete with
            # lifecycle_task for "who owns the cancel scope".
            session = loop.run_until_complete(ready)
        except BaseException:
            # __enter__ raised, so Python will never call __exit__ for this
            # instance. lifecycle_task is still unwinding its `async with`
            # blocks (the `return` inside `_lifecycle` above triggers that
            # the normal way) -- wait for it to finish, in the SAME task it
            # started in, before this loop goes away.
            try:
                loop.run_until_complete(closed)
            finally:
                loop.close()
            raise

        self._loop = loop
        self._lifecycle_task = lifecycle_task
        self._close_requested = close_requested
        self._closed = closed
        self._session = session
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._loop is None:
            return
        try:
            if not self._close_requested.done():
                self._close_requested.set_result(None)
            self._loop.run_until_complete(self._closed)
        finally:
            self._loop.close()
            self._loop = None
            self._lifecycle_task = None
            self._close_requested = None
            self._closed = None
            self._session = None

    def _run(self, coro: Any) -> Any:
        if self._loop is None:
            raise RuntimeError(
                "McpToolset used outside its own `with` block -- call within "
                "`with McpToolset(...) as toolset:`"
            )
        return self._loop.run_until_complete(coro)

    @staticmethod
    def _tool_input_schema(tool: Any) -> dict[str, Any]:
        """`Tool.input_schema` is a pydantic model (`InputSchema`) on the
        installed 2.0 SDK; `getattr` with both spellings tolerates an older
        SDK build within `pyproject.toml`'s declared `mcp>=1.9.0,<3.0` range
        -- the same defensive-across-SDK-versions posture `mcp_server/server.py`
        already applies to `ToolAnnotations`/`FastMCP`."""

        schema = getattr(tool, "input_schema", None)
        if schema is None:
            schema = getattr(tool, "inputSchema", None)
        if hasattr(schema, "model_dump"):
            return schema.model_dump(mode="json", exclude_none=True)
        if isinstance(schema, dict):
            return schema
        return {}

    def list_tools(self) -> tuple[ToolDescriptor, ...]:
        result = self._run(self._session.list_tools())
        return tuple(
            ToolDescriptor(
                name=tool.name,
                description=tool.description or "",
                input_schema=self._tool_input_schema(tool),
            )
            for tool in result.tools
        )

    def list_resources(self) -> tuple[ResourceDescriptor, ...]:
        result = self._run(self._session.list_resources())
        return tuple(
            ResourceDescriptor(uri=str(resource.uri), description=resource.description or "")
            for resource in result.resources
        )

    def list_prompts(self) -> tuple[PromptDescriptor, ...]:
        result = self._run(self._session.list_prompts())
        return tuple(
            PromptDescriptor(name=prompt.name, description=prompt.description or "")
            for prompt in result.prompts
        )

    def call_tool(self, name: str, arguments: dict, *, timeout_s: float) -> ToolCallResult:
        start = time.monotonic()
        result = self._run(
            self._session.call_tool(name, arguments, read_timeout_seconds=timeout_s)
        )
        elapsed_s = time.monotonic() - start
        text = "".join(getattr(block, "text", "") for block in result.content)
        return ToolCallResult(
            name=name,
            arguments=dict(arguments),  # AS DISPATCHED -- a copy of exactly what was sent
            text=text,
            is_error=bool(result.is_error),
            elapsed_s=elapsed_s,
            truncated_chars=_measure_truncated_chars(text),
        )

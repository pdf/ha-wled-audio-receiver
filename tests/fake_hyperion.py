"""A fake Hyperion.NG JSON API server for the tests."""

from __future__ import annotations

import asyncio
import json

# The constraints of Hyperion.NG 2.2's schema-image.json and schema-clear.json
# (libsrc/api/JSONRPC_schema), checked the way its JSON API validates commands.
SCHEMAS: dict[str, dict[str, tuple[type, int | None, int | None, bool]]] = {
    # key: (type, minimum or min length, maximum or max length, required)
    "image": {
        "command": (str, None, None, True),
        "instance": (list, None, None, False),
        "tan": (int, None, None, False),
        "priority": (int, 1, 253, True),
        "origin": (str, 4, 20, False),
        "duration": (int, None, None, False),
        "imagewidth": (int, 0, None, False),
        "imageheight": (int, 0, None, False),
        "imagedata": (str, None, None, True),
        "format": (str, None, None, False),
        "scale": (int, 25, 2000, False),
        "name": (str, None, None, False),
    },
    "clear": {
        "command": (str, None, None, True),
        "instance": (list, None, None, False),
        "tan": (int, None, None, False),
        "priority": (int, -1, 253, True),
    },
    "authorize": {
        "command": (str, None, None, True),
        "subcommand": (str, None, None, True),
        "tan": (int, None, None, False),
        "token": (str, 36, None, False),
    },
    "serverinfo": {
        "command": (str, None, None, True),
        "subcommand": (str, None, None, False),
        "instance": (int, 0, 254, False),
        "tan": (int, None, None, False),
    },
}

TOKEN = "0123456789abcdef0123456789abcdef0123"  # 36 characters, as Hyperion requires

# Instances served by default: id, friendly name, running.
INSTANCES = [(0, "TV backlight", True), (1, "Shelf strip", True), (2, "Spare", False)]


def schema_errors(command: dict) -> list[str]:
    """Return what Hyperion's schema validation would reject in a command."""
    schema = SCHEMAS.get(command.get("command"))
    if schema is None:
        return ["unknown command"]
    errors = [f"{key}: not allowed" for key in command if key not in schema]
    for key, (kind, low, high, required) in schema.items():
        if key not in command:
            if required:
                errors.append(f"{key}: missing")
            continue
        value = command[key]
        if not isinstance(value, kind) or isinstance(value, bool):
            errors.append(f"{key}: wrong type")
            continue
        size = len(value) if kind is str else value
        if low is not None and size < low:
            errors.append(f"{key}: below minimum {low}")
        if high is not None and size > high:
            errors.append(f"{key}: above maximum {high}")
    return errors


class FakeHyperion:
    """Accepts JSON API connections and records the commands."""

    def __init__(self) -> None:
        """Start empty."""
        self.commands: asyncio.Queue[dict] = asyncio.Queue()
        self.rejected: list[dict] = []
        self.received: list[dict] = []
        self.reject_all = False
        self.token: str | None = None
        self.instances = list(INSTANCES)
        self.connections = 0
        self.writers: list[asyncio.StreamWriter] = []
        self.server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        """Listen on a free loopback port."""
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Stop listening, close every connection and wait for them to finish."""
        assert self.server is not None
        self.server.close()  # no new connections, e.g. from a reconnecting client
        self.server.close_clients()
        self.drop()
        await asyncio.wait_for(self.server.wait_closed(), 5)

    def drop(self) -> None:
        """Close all client connections."""
        for writer in self.writers:
            writer.close()
        self.writers.clear()

    async def next(self) -> dict:
        """Return the next command."""
        return await asyncio.wait_for(self.commands.get(), 2)

    def _handle(self, command: dict, state: dict) -> dict:
        """Answer one command the way Hyperion's JSON API does."""
        name = command.get("command")
        reply: dict = {"command": name, "success": True, "tan": command.get("tan", 0)}
        errors = schema_errors(command)
        if errors:
            return {
                **reply,
                "success": False,
                "error": "Invalid command",
                "errorData": [{"description": e} for e in errors],
            }
        if name == "authorize":
            if command.get("subcommand") == "login" and command.get("token") == self.token:
                state["authorized"] = True
                return reply
            return {**reply, "success": False, "error": "No Authorization"}
        if not state["authorized"]:
            return {**reply, "success": False, "error": "No Authorization"}
        if self.reject_all:
            return {
                **reply,
                "success": False,
                "error": "Invalid command",
                "errorData": [{"description": "rejected by test"}],
            }
        if name == "serverinfo":
            info = [{"instance": i, "friendly_name": n, "running": r} for i, n, r in self.instances]
            return {**reply, "info": {"instance": info}}
        running = {i for i, _, r in self.instances if r}
        known = {i for i, _, _ in self.instances}
        wanted = command.get("instance")
        if wanted is not None:
            bad = [i for i in wanted if i not in known or i not in running]
            if bad or not wanted:
                return {**reply, "success": False, "error": "Errors for instance(s) given"}
        return reply

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self.writers.append(writer)
        state = {"authorized": self.token is None}
        try:
            while line := await reader.readline():
                command = json.loads(line)
                self.received.append(command)
                reply = self._handle(command, state)
                if not reply["success"]:
                    self.rejected.append(command)
                elif command.get("command") in ("image", "clear"):
                    await self.commands.put(command)
                writer.write(json.dumps(reply).encode() + b"\n")
        except ConnectionError, asyncio.CancelledError:
            pass
        finally:
            # Close our side too when the client hangs up, or wait_closed() waits forever.
            writer.close()

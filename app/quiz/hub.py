"""Beheer van de WebSocket-verbindingen en het uitsturen van de toestand.

Er is één quiz, dus er is geen roombeheer. De hub houdt gewoon alle open sockets
bij en stuurt iedereen bij elke verandering een volledige, rolgebonden snapshot.
Dat maakt reconnecten triviaal: wie binnenkomt krijgt meteen het hele plaatje.

Meerdere hostschermen tegelijk zijn de normale gang van zaken: /present op de
beamer en /admin op de laptop van de leiding zijn allebei "host". Ze delen
dezelfde rol en dezelfde snapshot; enkel hun UI verschilt.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from starlette.websockets import WebSocket, WebSocketState

from .game import Game

log = logging.getLogger("quiz.hub")


class Connection:
    __slots__ = ("ws", "role", "view", "player_id", "id")

    _counter = 0

    def __init__(
        self,
        ws: WebSocket,
        role: str,
        player_id: Optional[str] = None,
        view: str = "",
    ) -> None:
        Connection._counter += 1
        self.id = Connection._counter
        self.ws = ws
        self.role = role  # "player" | "host"
        self.view = view  # "present" | "admin" | "" -- puur informatief
        self.player_id = player_id

    async def send(self, payload: dict[str, Any]) -> bool:
        if self.ws.client_state is not WebSocketState.CONNECTED:
            return False
        try:
            await self.ws.send_json(payload)
            return True
        except Exception:
            # Socket weg tijdens het schrijven; de endpoint-loop ruimt hem op.
            return False


class Hub:
    def __init__(self, game: Game) -> None:
        self.game = game
        self.connections: set[Connection] = set()
        game.notifier = self

    # -- registratie --------------------------------------------------------

    def add(self, conn: Connection) -> None:
        self.connections.add(conn)
        self._sync_host_flag()

    def remove(self, conn: Connection) -> None:
        self.connections.discard(conn)
        self._sync_host_flag()

    def _sync_host_flag(self) -> None:
        self.game.host_connected = any(c.role == "host" for c in self.connections)

    def host_views(self) -> list[str]:
        return sorted(c.view for c in self.connections if c.role == "host")

    def player_connection_count(self, player_id: str) -> int:
        return sum(1 for c in self.connections if c.role == "player" and c.player_id == player_id)

    # -- uitsturen ----------------------------------------------------------

    async def _fanout(self, payloads: list[tuple[Connection, dict[str, Any]]]) -> None:
        if not payloads:
            return
        results = await asyncio.gather(
            *(conn.send(payload) for conn, payload in payloads), return_exceptions=True
        )
        for (conn, _), ok in zip(payloads, results):
            if ok is not True:
                self.connections.discard(conn)

    async def broadcast_state(self) -> None:
        # Eerst alle payloads synchroon opbouwen: zo krijgt iedereen exact
        # dezelfde momentopname, ook als er ondertussen iets verandert.
        payloads = [
            (conn, self.game.snapshot(conn.role, conn.player_id)) for conn in list(self.connections)
        ]
        await self._fanout(payloads)

    async def broadcast_event(self, name: str, data: dict[str, Any] | None = None) -> None:
        message = {"t": "event", "name": name, "data": data or {}}
        await self._fanout([(conn, message) for conn in list(self.connections)])

    async def broadcast_tick(self) -> None:
        snapshot = self.game.snapshot("host")
        if snapshot["timer"] is None:
            return
        message = {"t": "tick", "timer": snapshot["timer"], "answers": snapshot["answers"] or {}}
        await self._fanout([(conn, message) for conn in list(self.connections)])

    async def send_to(self, conn: Connection, payload: dict[str, Any]) -> None:
        if not await conn.send(payload):
            self.connections.discard(conn)

    async def send_state_to(self, conn: Connection) -> None:
        await self.send_to(conn, self.game.snapshot(conn.role, conn.player_id))

    async def send_error(self, conn: Connection, message: str, code: str = "error") -> None:
        await self.send_to(conn, {"t": "error", "code": code, "message": message})

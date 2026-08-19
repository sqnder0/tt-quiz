"""Jonge Helden Boomhutten Quiz — FastAPI-app met twee WebSocket-endpoints.

    GET  /            deelnemersscherm (idem /quiz)
    GET  /host        presentatorscherm
    GET  /healthz     health check voor Dokploy
    WS   /ws/play     spelers
    WS   /ws/host     presentator (secret vereist)
"""

from __future__ import annotations

import hmac
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .quiz.game import game
from .quiz.hub import Connection, Hub

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("quiz")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

hub = Hub(game)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Quiz klaar: %r met %d vragen", config.QUIZ_TITLE, game.total_questions)
    if config.HOST_SECRET == "boomhut":
        log.warning(
            "HOST_SECRET staat nog op de standaardwaarde. Zet een eigen waarde "
            "als environment variable voor je dit publiek zet."
        )
    yield


app = FastAPI(title=config.QUIZ_TITLE, docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Pagina's
# ---------------------------------------------------------------------------


def _page(name: str) -> FileResponse:
    return FileResponse(
        TEMPLATE_DIR / name,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/", include_in_schema=False)
@app.get("/quiz", include_in_schema=False)
async def play_page() -> FileResponse:
    return _page("play.html")


@app.get("/host", include_in_schema=False)
async def host_page() -> FileResponse:
    return _page("host.html")


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "phase": game.phase.value,
            "players": len(game.players),
            "questions": game.total_questions,
        }
    )


@app.get("/robots.txt", include_in_schema=False)
async def robots() -> PlainTextResponse:
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "img" / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/info", include_in_schema=False)
async def api_info(request: Request) -> JSONResponse:
    """Handig om tijdens het kamp snel de deelnamelink op het scherm te tonen."""
    return JSONResponse(
        {
            "title": config.QUIZ_TITLE,
            "subtitle": config.QUIZ_SUBTITLE,
            "questions": game.total_questions,
            "join_url": str(request.url_for("play_page")),
        }
    )


# ---------------------------------------------------------------------------
# WebSocket: spelers
# ---------------------------------------------------------------------------


async def _recv_json(ws: WebSocket) -> dict[str, Any] | None:
    """Lees één bericht.

    Geeft None terug bij rommel (die negeren we), gooit WebSocketDisconnect zodra
    de socket weg is. Belangrijk: hier nooit blind alle excepties opvangen. Een
    `continue` op een fout die zich blijft herhalen levert een lus zonder await op,
    en die legt de hele event loop -- en dus de hele quiz -- plat.
    """
    message = await ws.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))

    raw = message.get("text")
    if raw is None:
        payload = message.get("bytes")
        raw = payload.decode("utf-8", "replace") if payload else ""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


@app.websocket("/ws/play")
async def ws_play(ws: WebSocket) -> None:
    await ws.accept()
    conn = Connection(ws, role="player")
    hub.add(conn)
    player_id: str | None = None
    try:
        await hub.send_to(conn, {"t": "hello", "role": "player", "phase": game.phase.value})
        await hub.send_state_to(conn)

        while True:
            message = await _recv_json(ws)
            if message is None:
                continue
            kind = message.get("t") or message.get("type")

            if kind == "ping":
                await hub.send_to(conn, {"t": "pong"})
                continue

            if kind == "join":
                try:
                    player, reconnected = await game.join(
                        message.get("name", ""), message.get("player_id")
                    )
                except ValueError as exc:
                    await hub.send_error(conn, str(exc), code="join_failed")
                    continue
                # Eén tabblad per speler: een tweede tab van dezelfde speler krijgt
                # gewoon dezelfde toestand te zien, dat is prima.
                player_id = player.id
                conn.player_id = player.id
                await hub.send_to(
                    conn,
                    {
                        "t": "joined",
                        "player_id": player.id,
                        "name": player.name,
                        "reconnected": reconnected,
                    },
                )
                await hub.send_state_to(conn)
                continue

            if kind == "submit_answer":
                if not player_id:
                    await hub.send_error(conn, "Je zit nog niet in de quiz.", code="not_joined")
                    continue
                try:
                    await game.submit_answer(
                        player_id,
                        choice=message.get("choice"),
                        value=message.get("value"),
                    )
                except ValueError as exc:
                    await hub.send_error(conn, str(exc), code="answer_rejected")
                    await hub.send_state_to(conn)
                continue

            if kind == "state":
                await hub.send_state_to(conn)
                continue

            # Spelers kennen simpelweg geen host-commando's: die tak bestaat hier niet.
            await hub.send_error(conn, "Onbekend commando.", code="unknown")

    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover
        log.exception("Fout in spelerverbinding")
    finally:
        hub.remove(conn)
        if player_id and hub.player_connection_count(player_id) == 0:
            await game.mark_disconnected(player_id)


# ---------------------------------------------------------------------------
# WebSocket: host
# ---------------------------------------------------------------------------

_HOST_ACTIONS = {
    "host_start": lambda msg: game.host_start(),
    "host_reveal": lambda msg: game.host_reveal(),
    "host_next": lambda msg: game.host_next(),
    "host_pause": lambda msg: game.host_pause(),
    "host_resume": lambda msg: game.host_resume(),
    "host_restart": lambda msg: game.host_restart(),
    "host_finish": lambda msg: game.host_finish(),
    "host_kick": lambda msg: game.kick(str(msg.get("player_id", ""))),
    "host_clear_absent": lambda msg: game.clear_absent(),
    "host_options": lambda msg: game.host_set_options(
        auto_advance=msg.get("auto_advance"),
        auto_reveal_seconds=msg.get("auto_reveal_seconds"),
        auto_leaderboard_seconds=msg.get("auto_leaderboard_seconds"),
    ),
}


def _secret_ok(candidate: str | None) -> bool:
    if not candidate:
        return False
    return hmac.compare_digest(candidate, config.HOST_SECRET)


@app.get("/api/host/check", include_in_schema=False)
async def host_check(secret: str = Query(default="")) -> JSONResponse:
    """Laat het hostscherm het geheim controleren vóór het de socket opent."""
    return JSONResponse({"ok": _secret_ok(secret)})


@app.websocket("/ws/host")
async def ws_host(ws: WebSocket, secret: str = Query(default="")) -> None:
    if not _secret_ok(secret):
        await ws.close(code=4403, reason="Verkeerde hostcode")
        return

    await ws.accept()
    conn = Connection(ws, role="host")
    superseded = hub.add(conn)
    if superseded is not None:
        await hub.send_to(
            superseded,
            {
                "t": "error",
                "code": "host_superseded",
                "message": "Een ander hostscherm heeft de controle overgenomen.",
            },
        )
        try:
            # De oude socket komt hierdoor uit zijn receive-loop en ruimt zichzelf op.
            await superseded.ws.close(code=4409, reason="Overgenomen door een ander hostscherm")
        except Exception:
            hub.connections.discard(superseded)

    try:
        await hub.send_to(conn, {"t": "hello", "role": "host", "phase": game.phase.value})
        await hub.broadcast_state()

        while True:
            message = await _recv_json(ws)
            if message is None:
                continue
            kind = message.get("t") or message.get("type")

            if kind == "ping":
                await hub.send_to(conn, {"t": "pong"})
                continue
            if kind == "state":
                await hub.send_state_to(conn)
                continue
            if not hub.is_active_host(conn):
                await hub.send_error(
                    conn, "Dit scherm is niet meer de actieve host.", code="host_superseded"
                )
                continue

            action = _HOST_ACTIONS.get(kind or "")
            if action is None:
                await hub.send_error(conn, "Onbekend hostcommando.", code="unknown")
                continue
            await action(message)

    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover
        log.exception("Fout in hostverbinding")
    finally:
        hub.remove(conn)
        await hub.broadcast_state()

"""Jonge Helden Boomhutten Quiz — FastAPI-app met twee WebSocket-endpoints.

    GET  /            deelnemersscherm
    GET  /present     beamerscherm (alleen tonen)
    GET  /admin       bedieningspaneel (knoppen, spelers, vrageneditor)
    GET  /healthz     health check voor Dokploy
    WS   /ws/play     spelers
    WS   /ws/host     /present en /admin (secret vereist)

/present en /admin praten allebei over dezelfde hostsocket en mogen tegelijk
open staan -- dat is net de bedoeling: beamer op het ene scherm, bediening op
het andere.
"""

from __future__ import annotations

import hmac
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
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
async def play_page() -> FileResponse:
    return _page("play.html")


@app.get("/present", include_in_schema=False)
async def present_page() -> FileResponse:
    return _page("present.html")


@app.get("/admin", include_in_schema=False)
async def admin_page() -> FileResponse:
    return _page("admin.html")


@app.get("/host", include_in_schema=False)
async def host_page() -> RedirectResponse:
    """Het oude adres. Bediening zit nu op /admin."""
    return RedirectResponse("/admin", status_code=307)


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


# ---------------------------------------------------------------------------
# WebSocket-hulp
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


# ---------------------------------------------------------------------------
# WebSocket: spelers
# ---------------------------------------------------------------------------


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
# WebSocket: host (/present en /admin)
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
    "host_clear_all": lambda msg: game.clear_all_players(),
    "host_questions_set": lambda msg: game.set_questions(msg.get("items")),
    "host_questions_reset": lambda msg: game.reset_questions(),
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
    """Laat /present en /admin het geheim controleren vóór ze de socket openen."""
    return JSONResponse({"ok": _secret_ok(secret)})


@app.websocket("/ws/host")
async def ws_host(
    ws: WebSocket,
    secret: str = Query(default=""),
    view: str = Query(default=""),
) -> None:
    if not _secret_ok(secret):
        await ws.close(code=4403, reason="Verkeerde hostcode")
        return

    await ws.accept()
    conn = Connection(ws, role="host", view=view[:16])
    hub.add(conn)

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
            if kind == "host_questions":
                await hub.send_to(conn, game.questions_payload())
                continue

            action = _HOST_ACTIONS.get(kind or "")
            if action is None:
                await hub.send_error(conn, "Onbekend hostcommando.", code="unknown")
                continue
            try:
                await action(message)
            except ValueError as exc:
                # Bv. een vraag die niet klopt, of vragen aanpassen tijdens de quiz.
                await hub.send_error(conn, str(exc), code="command_rejected")
                continue
            if kind in ("host_questions_set", "host_questions_reset"):
                await hub.send_to(conn, game.questions_payload())

    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover
        log.exception("Fout in hostverbinding")
    finally:
        hub.remove(conn)
        await hub.broadcast_state()

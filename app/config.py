"""Centrale configuratie. Alles via environment variables, met veilige defaults."""

from __future__ import annotations

import os


def _env_str(key: str, default: str) -> str:
    value = os.getenv(key)
    return value if value not in (None, "") else default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, ""))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "ja"}


# --- Quiz -------------------------------------------------------------------
QUIZ_TITLE = _env_str("QUIZ_TITLE", "Jonge Helden Boomhutten Quiz")
QUIZ_SUBTITLE = _env_str("QUIZ_SUBTITLE", "Boomhuttenkamp — regendag-editie")

# --- Host -------------------------------------------------------------------
# Enige "beveiliging": een gedeeld geheim. Geen accounts, geen wachtwoorddb.
# Zet dit in Dokploy als environment variable HOST_SECRET.
HOST_SECRET = _env_str("HOST_SECRET", "boomhut")

# --- Spelers ----------------------------------------------------------------
MAX_NAME_LENGTH = _env_int("MAX_NAME_LENGTH", 20)
MIN_NAME_LENGTH = 1
MAX_PLAYERS = _env_int("MAX_PLAYERS", 200)

# --- Timing -----------------------------------------------------------------
DEFAULT_TIME_LIMIT = _env_int("DEFAULT_TIME_LIMIT", 20)
# Als iedereen die verbonden is geantwoord heeft, sluit de vraag vanzelf af.
AUTO_CLOSE_WHEN_ALL_ANSWERED = _env_bool("AUTO_CLOSE_WHEN_ALL_ANSWERED", True)
AUTO_CLOSE_GRACE_SECONDS = 1.2  # even laten staan zodat de laatste speler zijn bevestiging ziet

# Hands-free modus (host kan dit live aan/uit zetten op /host).
AUTO_ADVANCE_DEFAULT = _env_bool("AUTO_ADVANCE", False)
AUTO_REVEAL_SECONDS = _env_int("AUTO_REVEAL_SECONDS", 8)
AUTO_LEADERBOARD_SECONDS = _env_int("AUTO_LEADERBOARD_SECONDS", 7)

# --- Netwerk ----------------------------------------------------------------
PORT = _env_int("PORT", 8000)
HOST_BIND = _env_str("HOST_BIND", "0.0.0.0")

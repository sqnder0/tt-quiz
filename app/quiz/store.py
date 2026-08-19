"""Bewaren en terughalen van aangepaste vragen.

De ingebouwde lijst in `questions.py` is de startlijst. Zodra de leiding op
/admin iets aanpast, schrijven we de volledige lijst naar één JSON-bestand; dat
bestand heeft daarna voorrang. Zo blijft een aanpassing een herstart overleven
zonder dat er een database bij komt kijken.

Bewust géén migraties of versienummers: bij twijfel valt de app terug op de
ingebouwde vragen en blijft het kamp gewoon doorgaan.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .. import config
from .questions import QUESTIONS, Question, from_dict, to_dict, validate_questions

log = logging.getLogger("quiz.store")


def path() -> Path:
    return config.QUESTIONS_FILE


def defaults() -> list[Question]:
    return list(QUESTIONS)


def load() -> list[Question]:
    """Lees de bewaarde vragen, of geef de ingebouwde lijst terug.

    Een kapot of half geschreven bestand mag de app nooit tegenhouden: dan starten
    we met de standaardvragen en zeggen we dat luid in de log.
    """
    target = path()
    if not target.exists():
        return defaults()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        items = raw.get("questions") if isinstance(raw, dict) else raw
        if not isinstance(items, list) or not items:
            raise ValueError("geen vragen in het bestand")
        questions = [from_dict(item) for item in items]
        validate_questions(questions)
    except Exception as exc:
        log.warning("Kan %s niet lezen (%s). Ik val terug op de standaardvragen.", target, exc)
        return defaults()
    log.info("%d aangepaste vragen geladen uit %s", len(questions), target)
    return questions


def save(questions: list[Question]) -> None:
    """Schrijf de vragen weg. Eerst naar een tijdelijk bestand, dan hernoemen.

    Zonder die omweg houd je bij een crash halverwege een afgeknot bestand over,
    en dat is precies het moment waarop je het niet kan gebruiken.
    """
    validate_questions(questions)
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"questions": [to_dict(q) for q in questions]},
        ensure_ascii=False,
        indent=2,
    )
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    os.replace(tmp, target)
    log.info("%d vragen bewaard in %s", len(questions), target)


def reset() -> list[Question]:
    """Gooi de aanpassingen weg en keer terug naar de ingebouwde vragen."""
    target = path()
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:  # pragma: no cover - rechtenprobleem op de server
        log.warning("Kan %s niet verwijderen: %s", target, exc)
    return defaults()


def is_customised() -> bool:
    return path().exists()

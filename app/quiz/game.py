"""De volledige quizlogica: één globale sessie, server is altijd de baas.

State machine
-------------
    LOBBY ──host_start──▶ QUESTION ──tijd op / host_reveal──▶ ANSWER_REVEAL
                             ▲                                     │
                             │                                 host_next
                             │                                     ▼
                             └──────host_next────────────── LEADERBOARD
                                                                   │
                                            (na de laatste vraag)  ▼
                                                              FINISHED

`host_restart` brengt je vanuit eender welke toestand terug naar LOBBY met
gewiste scores maar mét behoud van de spelers. `host_finish` springt naar
FINISHED.

Alle publieke methodes nemen de lock. Methodes met een `_`-prefix gaan ervan uit
dat de lock al gehouden wordt en mogen zelf niet awaiten op broadcasts.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol, Sequence

from .. import config
from . import scoring, store
from .questions import (
    CATEGORIES,
    OPTION_KEYS,
    Question,
    from_dict as question_from_dict,
    to_dict as question_to_dict,
)

log = logging.getLogger("quiz.game")


class Phase(str, Enum):
    LOBBY = "LOBBY"
    QUESTION = "QUESTION"
    ANSWER_REVEAL = "ANSWER_REVEAL"
    LEADERBOARD = "LEADERBOARD"
    FINISHED = "FINISHED"


class Notifier(Protocol):
    """De hub implementeert dit. Zo blijft de spellogica testbaar zonder sockets."""

    async def broadcast_state(self) -> None: ...
    async def broadcast_event(self, name: str, data: dict[str, Any] | None = None) -> None: ...
    async def broadcast_tick(self) -> None: ...


class _NullNotifier:
    async def broadcast_state(self) -> None: ...
    async def broadcast_event(self, name: str, data: dict[str, Any] | None = None) -> None: ...
    async def broadcast_tick(self) -> None: ...


# ---------------------------------------------------------------------------
# Namen opkuisen
# ---------------------------------------------------------------------------

_WHITESPACE = re.compile(r"\s+")


def sanitize_name(raw: str) -> str:
    """Maak er een veilige, korte weergavenaam van.

    Geeft "" terug als er niets bruikbaars overblijft. Unicode-categorie "C" dekt
    stuurtekens, zero-width tekens en RTL-overrides -- precies de rommel waarmee
    iemand een namenlijst op een projector zou kunnen slopen.
    """
    if not isinstance(raw, str):
        return ""
    name = unicodedata.normalize("NFC", raw)
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = _WHITESPACE.sub(" ", name).strip()
    # De frontend zet namen via textContent, dus HTML kan sowieso niet uitvoeren.
    # We halen de haakjes er toch uit: schoner op het scherm, een zorg minder.
    name = name.replace("<", "").replace(">", "")
    return name[: config.MAX_NAME_LENGTH].strip()


# ---------------------------------------------------------------------------
# Datamodellen
# ---------------------------------------------------------------------------


@dataclass
class Player:
    id: str
    reconnect_token: str
    name: str
    joined_at: float
    connected: bool = True
    score: int = 0
    streak: int = 0
    best_streak: int = 0
    correct_count: int = 0
    answered_count: int = 0
    last_points: int = 0
    last_correct: Optional[bool] = None
    last_rank_change: int = 0
    last_seen: float = field(default_factory=time.time)


@dataclass
class Answer:
    player_id: str
    choice: Optional[int] = None
    value: Optional[float] = None
    elapsed: float = 0.0
    points: int = 0
    base: int = 0
    speed_bonus: int = 0
    streak_bonus: int = 0
    correct: bool = False
    accuracy: float = 0.0
    graded: bool = False


# ---------------------------------------------------------------------------
# Het spel
# ---------------------------------------------------------------------------


class Game:
    def __init__(
        self,
        questions: Sequence[Question] | None = None,
        scoring_config: scoring.ScoringConfig = scoring.SCORING,
        notifier: Notifier | None = None,
    ) -> None:
        # Geen lijst meegegeven? Dan pakken we wat de leiding bewaard heeft, en
        # anders de ingebouwde vragen.
        self.questions = list(questions) if questions is not None else store.load()
        self.scoring = scoring_config
        self.notifier: Notifier = notifier or _NullNotifier()

        self._lock = asyncio.Lock()
        self.players: dict[str, Player] = {}
        self.phase: Phase = Phase.LOBBY
        self.q_index: int = -1
        self.answers: list[dict[str, Answer]] = [dict() for _ in self.questions]

        # Timer (monotone klok, immuun voor NTP-sprongen)
        self._started_at: float = 0.0
        self._deadline: float = 0.0
        self._time_limit: float = 0.0
        self.paused: bool = False
        self._paused_remaining: float = 0.0
        self._all_answered: bool = False

        # Elke faseovergang verhoogt de token; oude achtergrondtaken zien dat en stoppen.
        self._token: int = 0
        self._task: Optional[asyncio.Task] = None

        # Hands-free instellingen (host kan ze live wijzigen)
        self.auto_advance: bool = config.AUTO_ADVANCE_DEFAULT
        self.auto_reveal_seconds: int = config.AUTO_REVEAL_SECONDS
        self.auto_leaderboard_seconds: int = config.AUTO_LEADERBOARD_SECONDS

        self._ranks_before_round: dict[str, int] = {}
        self.host_connected: bool = False

    # -- kleine helpers -----------------------------------------------------

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    @property
    def current_question(self) -> Optional[Question]:
        if 0 <= self.q_index < len(self.questions):
            return self.questions[self.q_index]
        return None

    @property
    def total_questions(self) -> int:
        return len(self.questions)

    def _remaining(self) -> float:
        if self.phase != Phase.QUESTION:
            return 0.0
        if self.paused:
            return max(0.0, self._paused_remaining)
        return max(0.0, self._deadline - self._now())

    def _connected_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.connected]

    def _current_answers(self) -> dict[str, Answer]:
        if 0 <= self.q_index < len(self.answers):
            return self.answers[self.q_index]
        return {}

    def _ranks(self) -> dict[str, int]:
        ordered = self._sorted_players()
        return {p.id: i + 1 for i, p in enumerate(ordered)}

    def _sorted_players(self) -> list[Player]:
        return sorted(
            self.players.values(),
            key=lambda p: (-p.score, p.name.lower(), p.joined_at),
        )

    # -- spelers ------------------------------------------------------------

    def _unique_name(self, base: str, exclude_id: str | None = None) -> str:
        taken = {p.name.lower() for p in self.players.values() if p.id != exclude_id}
        if base.lower() not in taken:
            return base
        n = 2
        while True:
            candidate = f"{base} ({n})"
            # Zorg dat we niet over de maximumlengte gaan door het suffix.
            if len(candidate) > config.MAX_NAME_LENGTH:
                trimmed = base[: max(1, config.MAX_NAME_LENGTH - len(f" ({n})"))].strip()
                candidate = f"{trimmed} ({n})"
            if candidate.lower() not in taken:
                return candidate
            n += 1

    async def join(
        self,
        raw_name: str,
        reconnect_token: str | None = None,
        player_id: str | None = None,
    ) -> tuple[Player, bool]:
        """Nieuwe speler of reconnect. Geeft (speler, was_reconnect) terug."""
        async with self._lock:
            player = next(
                (
                    candidate
                    for candidate in self.players.values()
                    if (reconnect_token and candidate.reconnect_token == reconnect_token)
                    or (player_id and candidate.id == player_id)
                ),
                None,
            )
            if player is not None:
                player.connected = True
                player.last_seen = time.time()
                # Naam mag bijgewerkt worden zolang we in de lobby zitten.
                new_name = sanitize_name(raw_name)
                if new_name and self.phase == Phase.LOBBY and new_name.lower() != player.name.lower():
                    player.name = self._unique_name(new_name, exclude_id=player.id)
                reconnected = True
            else:
                name = sanitize_name(raw_name)
                if not name:
                    raise ValueError("Geef een geldige naam in.")
                if len(self.players) >= config.MAX_PLAYERS:
                    raise ValueError("De quiz zit vol. Vraag de leiding om hulp.")
                # Zowel de interne id als het reconnect-token zijn server-side.
                pid = uuid.uuid4().hex
                player = Player(
                    id=pid,
                    reconnect_token=uuid.uuid4().hex,
                    name=self._unique_name(name),
                    joined_at=time.time(),
                )
                self.players[pid] = player
                reconnected = False

        if not reconnected:
            await self.notifier.broadcast_event("player_joined", {"name": player.name, "id": player.id})
        return player, reconnected

    async def mark_disconnected(self, player_id: str) -> None:
        async with self._lock:
            player = self.players.get(player_id)
            if player is None or not player.connected:
                return
            player.connected = False
            player.last_seen = time.time()
        await self.notifier.broadcast_state()

    async def clear_absent(self) -> int:
        """Verwijder iedereen die niet verbonden is. Enkel in de lobby.

        Tijdens een vraag mag dit nooit kunnen: een gsm die even op slot ging telt
        ook als "niet verbonden", en die speler moet gewoon kunnen terugkomen.
        """
        async with self._lock:
            if self.phase is not Phase.LOBBY:
                return 0
            gone = [pid for pid, player in self.players.items() if not player.connected]
            for pid in gone:
                self.players.pop(pid, None)
                for round_answers in self.answers:
                    round_answers.pop(pid, None)
        if gone:
            await self.notifier.broadcast_state()
        return len(gone)

    async def kick(self, player_id: str) -> Optional[str]:
        """Zet één speler uit de quiz. Geeft de naam terug, of None."""
        async with self._lock:
            player = self.players.pop(player_id, None)
            if player is None:
                return None
            for round_answers in self.answers:
                round_answers.pop(player_id, None)
        await self.notifier.broadcast_event("player_kicked", {"id": player_id, "name": player.name})
        await self.notifier.broadcast_state()
        return player.name

    async def clear_all_players(self) -> int:
        """Maak de lobby helemaal leeg. Enkel in de lobby.

        Tijdens een lopende quiz zou dit iedereen midden in een vraag buitengooien,
        dus dat laten we niet toe -- gebruik dan eerst "Opnieuw starten".
        """
        async with self._lock:
            if self.phase is not Phase.LOBBY:
                return 0
            removed = len(self.players)
            self.players.clear()
            self.answers = [dict() for _ in self.questions]
            self._ranks_before_round = {}
        if removed:
            await self.notifier.broadcast_event("lobby_cleared", {"count": removed})
            await self.notifier.broadcast_state()
        return removed

    # -- vragen beheren -----------------------------------------------------

    def questions_payload(self) -> dict[str, Any]:
        """De volledige vragenlijst voor de editor op /admin.

        Bewust géén onderdeel van de gewone snapshot: die gaat bij elke
        verandering naar alle schermen, en dertig vragen meesturen op elke tik is
        zonde van de bandbreedte van dertig gsm's.
        """
        return {
            "t": "questions",
            "items": [question_to_dict(q) for q in self.questions],
            "editable": self.phase is Phase.LOBBY,
            "customised": store.is_customised(),
            "categories": list(CATEGORIES),
            "limits": {
                "min_time": config.MIN_TIME_LIMIT,
                "max_time": config.MAX_TIME_LIMIT,
                "default_time": config.DEFAULT_TIME_LIMIT,
            },
        }

    def _apply_questions(self, questions: list[Question]) -> None:
        """Vervang de vragenlijst. Lock wordt al gehouden, fase is LOBBY."""
        self.questions = questions
        self.answers = [dict() for _ in questions]
        self.q_index = -1
        self._ranks_before_round = {}

    async def set_questions(self, items: Any) -> int:
        """Bewaar een volledig nieuwe vragenlijst vanuit de editor.

        De editor stuurt telkens de hele lijst terug in plaats van losse
        toevoeg/verwijder/verplaats-commando's. Dat scheelt een hoop protocol, en
        de volgorde in de editor is per definitie de volgorde van de quiz.
        """
        if not isinstance(items, list) or not items:
            raise ValueError("Er moet minstens één vraag overblijven.")
        if len(items) > 200:
            raise ValueError("Maximaal 200 vragen.")

        parsed: list[Question] = []
        seen: set[str] = set()
        for number, item in enumerate(items, start=1):
            try:
                question = question_from_dict(item)
            except ValueError as exc:
                raise ValueError(f"Vraag {number}: {exc}") from exc
            if question.id in seen:
                raise ValueError(f"Vraag {number}: de id {question.id!r} bestaat al.")
            seen.add(question.id)
            parsed.append(question)

        async with self._lock:
            if self.phase is not Phase.LOBBY:
                raise ValueError("Vragen aanpassen kan enkel vanuit de lobby.")
            store.save(parsed)
            self._apply_questions(parsed)

        await self.notifier.broadcast_event("questions_changed", {"total": len(parsed)})
        await self.notifier.broadcast_state()
        return len(parsed)

    async def reset_questions(self) -> int:
        """Aanpassingen weggooien en terug naar de ingebouwde vragenlijst."""
        async with self._lock:
            if self.phase is not Phase.LOBBY:
                raise ValueError("Vragen aanpassen kan enkel vanuit de lobby.")
            self._apply_questions(store.reset())
            total = len(self.questions)
        await self.notifier.broadcast_event("questions_changed", {"total": total})
        await self.notifier.broadcast_state()
        return total

    # -- antwoorden ---------------------------------------------------------

    async def submit_answer(
        self,
        player_id: str,
        choice: int | None = None,
        value: float | None = None,
    ) -> dict[str, Any]:
        """Registreer één antwoord. Tweede pogingen en late antwoorden worden geweigerd."""
        async with self._lock:
            player = self.players.get(player_id)
            if player is None:
                raise ValueError("Je zit niet in de quiz. Herlaad de pagina.")
            if self.phase != Phase.QUESTION:
                raise ValueError("Er loopt nu geen vraag.")
            question = self.current_question
            if question is None:
                raise ValueError("Er loopt nu geen vraag.")
            if self.paused:
                raise ValueError("De quiz staat even op pauze.")

            elapsed = self._now() - self._started_at
            if self._remaining() <= 0:
                raise ValueError("Te laat!")

            bucket = self.answers[self.q_index]
            if player_id in bucket:
                raise ValueError("Je hebt al geantwoord.")

            if question.type == "estimate":
                if value is None:
                    raise ValueError("Geef een getal in.")
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    raise ValueError("Geef een geldig getal in.")
                if numeric != numeric or numeric in (float("inf"), float("-inf")):
                    raise ValueError("Geef een geldig getal in.")
                answer = Answer(player_id=player_id, value=numeric, elapsed=elapsed)
            else:
                if choice is None or not isinstance(choice, int) or not 0 <= choice < len(question.options):
                    raise ValueError("Ongeldig antwoord.")
                answer = Answer(player_id=player_id, choice=choice, elapsed=elapsed)

            bucket[player_id] = answer
            received = len(bucket)
            expected = len(self._connected_players())

            # Iedereen binnen? Kort laten hangen en dan automatisch afsluiten.
            if (
                config.AUTO_CLOSE_WHEN_ALL_ANSWERED
                and expected > 0
                and received >= expected
                and not self._all_answered
            ):
                self._all_answered = True
                self._deadline = min(self._deadline, self._now() + config.AUTO_CLOSE_GRACE_SECONDS)

            result = {
                "choice": answer.choice,
                "value": answer.value,
                "received": received,
                "expected": expected,
            }

        await self.notifier.broadcast_event("answer_received", {"received": result["received"], "expected": result["expected"]})
        await self.notifier.broadcast_state()
        return result

    # -- host-acties --------------------------------------------------------

    async def host_start(self) -> None:
        async with self._lock:
            if self.phase not in (Phase.LOBBY,):
                return
            self._start_question(0)
        await self.notifier.broadcast_event("quiz_started", {})
        await self.notifier.broadcast_event("question_started", {"index": self.q_index})
        await self.notifier.broadcast_state()

    async def host_reveal(self) -> None:
        """Vraag vroegtijdig afsluiten en het antwoord tonen."""
        await self._close_question(reason="host")

    async def host_next(self) -> None:
        async with self._lock:
            if self.phase == Phase.LOBBY:
                self._start_question(0)
                event = "question_started"
            elif self.phase == Phase.ANSWER_REVEAL:
                if self.q_index >= len(self.questions) - 1:
                    self._to_finished()
                    event = "quiz_finished"
                else:
                    self._to_leaderboard()
                    event = "leaderboard_updated"
            elif self.phase == Phase.LEADERBOARD:
                self._start_question(self.q_index + 1)
                event = "question_started"
            elif self.phase == Phase.QUESTION:
                # "Volgende" tijdens een vraag = eerst afsluiten.
                self._grade_and_reveal()
                event = "answer_revealed"
            else:
                return
        await self.notifier.broadcast_event(event, {"index": self.q_index})
        await self.notifier.broadcast_state()

    async def host_pause(self) -> None:
        async with self._lock:
            if self.phase != Phase.QUESTION or self.paused:
                return
            self.paused = True
            self._paused_remaining = max(0.0, self._deadline - self._now())
        await self.notifier.broadcast_state()

    async def host_resume(self) -> None:
        async with self._lock:
            if self.phase != Phase.QUESTION or not self.paused:
                return
            self.paused = False
            self._deadline = self._now() + self._paused_remaining
            # Startpunt meeschuiven zodat de snelheidsbonus eerlijk blijft.
            self._started_at = self._deadline - self._time_limit
        await self.notifier.broadcast_state()

    async def host_restart(self) -> None:
        """Scores wissen, spelers behouden, terug naar de lobby."""
        async with self._lock:
            self._cancel_task()
            self._token += 1
            self.phase = Phase.LOBBY
            self.q_index = -1
            self.paused = False
            self._all_answered = False
            self.answers = [dict() for _ in self.questions]
            self._ranks_before_round = {}
            for player in self.players.values():
                player.score = 0
                player.streak = 0
                player.best_streak = 0
                player.correct_count = 0
                player.answered_count = 0
                player.last_points = 0
                player.last_correct = None
                player.last_rank_change = 0
        await self.notifier.broadcast_event("quiz_reset", {})
        await self.notifier.broadcast_state()

    async def host_finish(self) -> None:
        async with self._lock:
            if self.phase == Phase.QUESTION:
                self._grade_and_reveal()
            self._to_finished()
        await self.notifier.broadcast_event("quiz_finished", {})
        await self.notifier.broadcast_state()

    async def host_set_options(
        self,
        auto_advance: bool | None = None,
        auto_reveal_seconds: int | None = None,
        auto_leaderboard_seconds: int | None = None,
    ) -> None:
        async with self._lock:
            if auto_advance is not None:
                self.auto_advance = bool(auto_advance)
            if auto_reveal_seconds is not None:
                self.auto_reveal_seconds = max(2, min(60, int(auto_reveal_seconds)))
            if auto_leaderboard_seconds is not None:
                self.auto_leaderboard_seconds = max(2, min(60, int(auto_leaderboard_seconds)))
            if self.auto_advance and self.phase in (Phase.ANSWER_REVEAL, Phase.LEADERBOARD):
                self._schedule_auto_advance()
        await self.notifier.broadcast_state()

    # -- interne overgangen (lock wordt al gehouden) -------------------------

    def _cancel_task(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        try:
            running = asyncio.current_task()
        except RuntimeError:  # pragma: no cover - geen draaiende loop
            running = None
        if task is running:
            # We zitten zelf in die taak (bv. de timer die de vraag afsluit).
            # Cancelen zou de broadcast erna onderbreken; de tokencheck stopt de lus wel.
            return
        task.cancel()

    def _start_question(self, index: int) -> None:
        if not 0 <= index < len(self.questions):
            self._to_finished()
            return
        self._cancel_task()
        self._token += 1
        self.q_index = index
        self.phase = Phase.QUESTION
        self.paused = False
        self._all_answered = False
        self._ranks_before_round = self._ranks()
        question = self.questions[index]
        self._time_limit = float(question.time_limit)
        self._started_at = self._now()
        self._deadline = self._started_at + self._time_limit
        for player in self.players.values():
            player.last_points = 0
            player.last_correct = None
            player.last_rank_change = 0
        self._task = asyncio.ensure_future(self._question_runner(self._token))

    def _grade_and_reveal(self) -> None:
        """Punten toekennen en naar ANSWER_REVEAL. Idempotent per vraag."""
        self._cancel_task()
        self._token += 1
        question = self.current_question
        if question is None:
            return
        bucket = self.answers[self.q_index]

        for player in self.players.values():
            answer = bucket.get(player.id)
            if answer is None:
                # Niet geantwoord = fout: streak breekt, geen punten.
                player.streak = 0
                player.last_points = 0
                player.last_correct = None
                continue
            if answer.graded:
                continue
            accuracy = self._grade_answer(question, answer)
            result = scoring.compute_score(
                accuracy=accuracy,
                elapsed_seconds=answer.elapsed,
                time_limit=self._time_limit,
                points_multiplier=question.points_multiplier,
                streak_before=player.streak,
                cfg=self.scoring,
            )
            answer.accuracy = accuracy
            answer.correct = result.correct
            answer.points = result.points
            answer.base = result.base
            answer.speed_bonus = result.speed_bonus
            answer.streak_bonus = result.streak_bonus
            answer.graded = True

            player.answered_count += 1
            player.score += result.points
            player.last_points = result.points
            player.last_correct = result.correct
            if result.correct:
                player.streak += 1
                player.correct_count += 1
                player.best_streak = max(player.best_streak, player.streak)
            else:
                player.streak = 0

        # Positieverschuiving t.o.v. de start van deze vraag.
        ranks_after = self._ranks()
        for pid, rank_after in ranks_after.items():
            before = self._ranks_before_round.get(pid)
            player = self.players[pid]
            player.last_rank_change = 0 if before is None else (before - rank_after)

        self.phase = Phase.ANSWER_REVEAL
        self.paused = False
        if self.auto_advance:
            self._schedule_auto_advance()

    def _grade_answer(self, question: Question, answer: Answer) -> float:
        """Geeft de nauwkeurigheid terug: 1.0 juist, 0.0 fout, of iets ertussen."""
        if question.type == "estimate":
            assert question.correct_value is not None
            if answer.value is None:
                return 0.0
            return scoring.estimate_accuracy(
                answer.value,
                question.correct_value,
                question.tolerance,
                question.effective_max_error,
            )
        return 1.0 if answer.choice == question.correct_index else 0.0

    def _to_leaderboard(self) -> None:
        self._cancel_task()
        self._token += 1
        self.phase = Phase.LEADERBOARD
        if self.auto_advance:
            self._schedule_auto_advance()

    def _to_finished(self) -> None:
        self._cancel_task()
        self._token += 1
        self.phase = Phase.FINISHED
        self.paused = False

    def _schedule_auto_advance(self) -> None:
        delay = (
            self.auto_reveal_seconds
            if self.phase == Phase.ANSWER_REVEAL
            else self.auto_leaderboard_seconds
        )
        self._cancel_task()
        self._task = asyncio.ensure_future(self._auto_advance_runner(self._token, delay))

    # -- achtergrondtaken ---------------------------------------------------

    async def _question_runner(self, token: int) -> None:
        """Server-side klok. Tikt elke seconde en sluit de vraag af op de deadline."""
        try:
            while True:
                if token != self._token or self.phase != Phase.QUESTION:
                    return
                if self.paused:
                    await asyncio.sleep(0.25)
                    continue
                remaining = self._remaining()
                if remaining <= 0:
                    await self._close_question(reason="time", expected_token=token)
                    return
                await self.notifier.broadcast_tick()
                await asyncio.sleep(min(1.0, max(0.05, remaining)))
        except asyncio.CancelledError:  # normale gang van zaken bij een faseovergang
            raise
        except Exception:  # pragma: no cover - mag de quiz nooit doen crashen
            log.exception("Fout in de vraagtimer")

    async def _auto_advance_runner(self, token: int, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            if token != self._token:
                return
            await self.host_next()
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            log.exception("Fout bij automatisch doorgaan")

    async def _close_question(self, reason: str, expected_token: int | None = None) -> None:
        async with self._lock:
            if self.phase != Phase.QUESTION:
                return
            if expected_token is not None and expected_token != self._token:
                return
            self._grade_and_reveal()
        await self.notifier.broadcast_event("answer_revealed", {"reason": reason, "index": self.q_index})
        await self.notifier.broadcast_state()

    # -- snapshots ----------------------------------------------------------

    def snapshot(self, role: str = "player", player_id: str | None = None) -> dict[str, Any]:
        """Volledige, zelfdragende toestand. Clients tekenen hier hun hele UI uit."""
        is_host = role == "host"
        question = self.current_question
        connected = self._connected_players()

        state: dict[str, Any] = {
            "t": "state",
            "phase": self.phase.value,
            "server_ts": int(time.time() * 1000),
            "quiz": {
                "title": config.QUIZ_TITLE,
                "subtitle": config.QUIZ_SUBTITLE,
                "total_questions": self.total_questions,
            },
            "counts": {
                "players": len(self.players),
                "connected": len(connected),
            },
            "host_connected": self.host_connected,
            # Voortgang zit in elke snapshot, ook tijdens de tussenstand: daar is
            # `question` leeg maar wil het scherm nog steeds "na vraag 7" tonen.
            "progress": (
                {"number": self.q_index + 1, "total": self.total_questions}
                if self.q_index >= 0
                else None
            ),
        }

        if self.phase == Phase.QUESTION and question is not None:
            state["timer"] = {
                "remaining_ms": int(self._remaining() * 1000),
                "total_ms": int(self._time_limit * 1000),
                "running": not self.paused,
                "paused": self.paused,
                "all_answered": self._all_answered,
            }
        else:
            state["timer"] = None

        if question is not None and self.phase in (Phase.QUESTION, Phase.ANSWER_REVEAL):
            state["question"] = self._question_payload(question, reveal=self.phase == Phase.ANSWER_REVEAL)
            bucket = self._current_answers()
            state["answers"] = {
                "received": len(bucket),
                "expected": max(len(connected), len(bucket)),
            }
        else:
            state["question"] = None
            state["answers"] = None

        if self.phase == Phase.ANSWER_REVEAL and question is not None:
            state["reveal"] = self._reveal_payload(question)
        else:
            state["reveal"] = None

        if self.phase in (Phase.LEADERBOARD, Phase.FINISHED) or is_host:
            limit = None if (is_host or self.phase == Phase.FINISHED) else 5
            state["leaderboard"] = self._leaderboard_payload(limit=limit)
        else:
            state["leaderboard"] = None

        if self.phase == Phase.FINISHED:
            state["podium"] = self._leaderboard_payload(limit=3)
            state["standings"] = self._leaderboard_payload(limit=None)
        else:
            state["podium"] = None
            state["standings"] = None

        if is_host:
            state["players"] = [
                {
                    "id": p.id,
                    "name": p.name,
                    "connected": p.connected,
                    "score": p.score,
                    "streak": p.streak,
                    "answered": p.id in self._current_answers(),
                }
                for p in sorted(self.players.values(), key=lambda p: p.joined_at)
            ]
            state["options"] = {
                "auto_advance": self.auto_advance,
                "auto_reveal_seconds": self.auto_reveal_seconds,
                "auto_leaderboard_seconds": self.auto_leaderboard_seconds,
            }
        else:
            state["you"] = self._you_payload(player_id)

        return state

    def _question_payload(self, question: Question, reveal: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": question.id,
            "index": self.q_index,
            "number": self.q_index + 1,
            "total": self.total_questions,
            "category": question.category,
            "type": question.type,
            "text": question.text,
            "image": question.image,
            "visual": question.visual,
            "time_limit": question.time_limit,
            "double": question.is_double,
            "multiplier": question.points_multiplier,
        }
        if question.type == "estimate":
            payload["unit"] = question.unit
        else:
            payload["options"] = [
                {"key": OPTION_KEYS[i], "index": i, "text": text}
                for i, text in enumerate(question.options)
            ]
        # Het juiste antwoord verlaat de server pas in de onthulfase.
        if reveal:
            payload["correct_index"] = question.correct_index if question.type != "estimate" else None
            payload["correct_text"] = question.correct_answer_text
            payload["explanation"] = question.explanation
        return payload

    def _reveal_payload(self, question: Question) -> dict[str, Any]:
        bucket = self._current_answers()
        total_answered = len(bucket)
        payload: dict[str, Any] = {
            "type": question.type,
            "correct_text": question.correct_answer_text,
            "explanation": question.explanation,
            "num_answered": total_answered,
            "num_players": len(self.players),
            "no_answer": max(0, len(self._connected_players()) - total_answered),
            "num_correct": sum(1 for a in bucket.values() if a.correct),
        }

        if question.type == "estimate":
            values = sorted(
                (
                    {
                        "name": self.players[a.player_id].name,
                        "value": a.value,
                        "diff": abs((a.value or 0) - float(question.correct_value or 0)),
                        "points": a.points,
                    }
                    for a in bucket.values()
                    if a.value is not None and a.player_id in self.players
                ),
                key=lambda item: item["diff"],
            )
            payload["correct_value"] = question.correct_value
            payload["unit"] = question.unit
            payload["closest"] = values[:5]
            payload["average"] = (
                round(sum(v["value"] for v in values) / len(values), 2) if values else None
            )
        else:
            counts = [0] * len(question.options)
            for answer in bucket.values():
                if answer.choice is not None and 0 <= answer.choice < len(counts):
                    counts[answer.choice] += 1
            percentages = _percentages(counts)
            payload["correct_index"] = question.correct_index
            payload["counts"] = [
                {
                    "key": OPTION_KEYS[i],
                    "index": i,
                    "text": text,
                    "count": counts[i],
                    "pct": percentages[i],
                    "correct": i == question.correct_index,
                }
                for i, text in enumerate(question.options)
            ]
        return payload

    def _leaderboard_payload(self, limit: int | None = None) -> list[dict[str, Any]]:
        ordered = self._sorted_players()
        if limit is not None:
            ordered = ordered[:limit]
        return [
            {
                "rank": i + 1,
                "id": p.id,
                "name": p.name,
                "score": p.score,
                "delta": p.last_points,
                "rank_change": p.last_rank_change,
                "streak": p.streak,
                "best_streak": p.best_streak,
                "correct_count": p.correct_count,
                "connected": p.connected,
            }
            for i, p in enumerate(ordered)
        ]

    def _you_payload(self, player_id: str | None) -> Optional[dict[str, Any]]:
        if not player_id or player_id not in self.players:
            return None
        player = self.players[player_id]
        ranks = self._ranks()
        answer = self._current_answers().get(player_id)
        data: dict[str, Any] = {
            "id": player.id,
            "name": player.name,
            "score": player.score,
            "rank": ranks.get(player_id, len(self.players)),
            "total_players": len(self.players),
            "streak": player.streak,
            "best_streak": player.best_streak,
            "correct_count": player.correct_count,
            "rank_change": player.last_rank_change,
            "answered": answer is not None,
            "choice": answer.choice if answer else None,
            "value": answer.value if answer else None,
        }
        if self.phase in (Phase.ANSWER_REVEAL, Phase.LEADERBOARD, Phase.FINISHED):
            data["last_points"] = player.last_points
            data["last_correct"] = player.last_correct
            if answer is not None and answer.graded:
                data["breakdown"] = {
                    "base": answer.base,
                    "speed": answer.speed_bonus,
                    "streak": answer.streak_bonus,
                    "accuracy": round(answer.accuracy, 3),
                }
        return data


def _percentages(counts: list[int]) -> list[int]:
    """Ronde percentages die altijd exact op 100 uitkomen.

    Gewoon elk percentage apart afronden geeft 33+33+33=99 op het scherm. Met de
    grootste-restmethode gaat de overschot naar de opties die er het dichtst bij
    zaten, zodat de balken naast elkaar wel kloppen.
    """
    total = sum(counts)
    if total <= 0:
        return [0] * len(counts)

    exact = [value * 100 / total for value in counts]
    rounded = [int(value) for value in exact]
    remainder = 100 - sum(rounded)
    # Verdeel wat overblijft over de grootste restwaarden.
    order = sorted(range(len(counts)), key=lambda i: (exact[i] - rounded[i], counts[i]), reverse=True)
    for i in order[:remainder]:
        rounded[i] += 1
    return rounded


# De ene, globale quizsessie.
game = Game()

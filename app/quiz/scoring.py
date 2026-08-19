"""Puntenberekening — centraal en configureerbaar.

Uitgangspunt uit de opdracht: snelheid mag nooit belangrijker zijn dan juistheid.

Met de defaults hieronder levert dat:

    juist + supersnel   -> 1000 punten
    juist + rustig aan  ->  500 punten
    fout (hoe snel ook) ->    0 punten

Een correct antwoord op de valreep haalt dus nog altijd de helft van het maximum,
en verslaat altijd een snel fout antwoord. Bij een dubbele-puntenvraag wordt
alles vermenigvuldigd met de `points_multiplier` van de vraag.

De snelheidsbonus loopt af over `speed_window_seconds`, niet over de volledige
bedenktijd van de vraag. Zo kan een vraag ruim open blijven staan zonder dat de
bonus zijn onderscheidend vermogen verliest.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoringConfig:
    """Alle knoppen van het puntensysteem op één plek."""

    base_points: int = 500
    """Punten die je krijgt voor een juist antwoord, ongeacht de snelheid."""

    max_speed_bonus: int = 500
    """Maximale extra punten voor snelheid (bovenop base_points)."""

    grace_seconds: float = 1.0
    """De eerste seconde telt als 'onmiddellijk'. Compenseert lezen + netwerk."""

    speed_window_seconds: float = 20.0
    """Binnen hoeveel seconden je de snelheidsbonus verdient.

    Bewust losgekoppeld van de bedenktijd van de vraag. Een vraag mag gerust 90
    seconden open staan zodat niemand moet haasten, maar als de bonus over die
    volle 90 seconden zou uitsmeren, krijgt iedereen die binnen 10 seconden
    antwoordt bijna hetzelfde -- en dan meet de bonus niets meer. Is de
    bedenktijd korter dan dit venster, dan telt de bedenktijd.
    """

    streak_bonus_per_step: int = 25
    """Kleine bonus per extra juist antwoord op rij (vanaf 2 op rij)."""

    max_streak_bonus: int = 100
    """Plafond op de streakbonus, zodat een streak nooit de quiz beslist."""

    estimate_correct_threshold: float = 0.5
    """Nauwkeurigheid (0..1) vanaf wanneer een schatting als 'juist' telt voor streaks."""


SCORING = ScoringConfig()


@dataclass(frozen=True)
class ScoreResult:
    points: int
    base: int
    speed_bonus: int
    streak_bonus: int
    correct: bool
    accuracy: float  # 1.0 voor een juist meerkeuzeantwoord, 0..1 voor schattingen
    speed_ratio: float  # 1.0 = onmiddellijk, 0.0 = op de valreep


def speed_ratio(elapsed_seconds: float, time_limit: float, cfg: ScoringConfig = SCORING) -> float:
    """Hoeveel van het bonusvenster was er nog over, als fractie tussen 0 en 1."""
    if time_limit <= 0:
        return 1.0
    horizon = time_limit
    if cfg.speed_window_seconds > 0:
        horizon = min(time_limit, cfg.speed_window_seconds)
    effective = max(0.0, elapsed_seconds - cfg.grace_seconds)
    window = max(1e-6, horizon - cfg.grace_seconds)
    return max(0.0, min(1.0, 1.0 - (effective / window)))


def estimate_accuracy(
    guess: float,
    correct_value: float,
    tolerance: float,
    max_error: float,
) -> float:
    """Nauwkeurigheid van een schatting, 1.0 binnen de tolerantie, 0.0 vanaf max_error."""
    error = abs(guess - correct_value)
    if error <= tolerance:
        return 1.0
    if error >= max_error:
        return 0.0
    span = max(1e-9, max_error - tolerance)
    return max(0.0, min(1.0, 1.0 - (error - tolerance) / span))


def compute_score(
    *,
    accuracy: float,
    elapsed_seconds: float,
    time_limit: float,
    points_multiplier: float = 1.0,
    streak_before: int = 0,
    cfg: ScoringConfig = SCORING,
) -> ScoreResult:
    """Bereken de punten voor één antwoord.

    `accuracy` is 1.0 voor een juist meerkeuzeantwoord, 0.0 voor een fout antwoord,
    en een waarde daartussen voor schattingsvragen.
    """
    accuracy = max(0.0, min(1.0, accuracy))
    ratio = speed_ratio(elapsed_seconds, time_limit, cfg)
    correct = accuracy >= cfg.estimate_correct_threshold

    if accuracy <= 0.0:
        return ScoreResult(0, 0, 0, 0, False, 0.0, ratio)

    base = round(cfg.base_points * accuracy * points_multiplier)
    speed_bonus = round(cfg.max_speed_bonus * accuracy * ratio * points_multiplier)

    streak_bonus = 0
    if correct and streak_before >= 1:
        # streak_before is het aantal juiste antwoorden vóór deze vraag.
        streak_bonus = min(cfg.max_streak_bonus, cfg.streak_bonus_per_step * streak_before)

    return ScoreResult(
        points=base + speed_bonus + streak_bonus,
        base=base,
        speed_bonus=speed_bonus,
        streak_bonus=streak_bonus,
        correct=correct,
        accuracy=accuracy,
        speed_ratio=ratio,
    )

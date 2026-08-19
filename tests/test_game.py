"""Tests voor de spellogica. Draaien met:  python -m unittest discover -s tests

Bewust stdlib-only (unittest.IsolatedAsyncioTestCase) zodat er geen extra
dependency nodig is om dit op eender welke machine te draaien.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.quiz import scoring, store  # noqa: E402
from app.quiz.game import Game, Phase, sanitize_name  # noqa: E402
from app.quiz.questions import QUESTIONS, Question, from_dict, to_dict  # noqa: E402

DEMO = (
    Question(
        id="a",
        category="🌳 Test",
        text="Eerste vraag",
        options=("Juist", "Fout", "Fout", "Fout"),
        correct_index=0,
        time_limit=10,
    ),
    Question(
        id="b",
        category="🌳 Test",
        text="Tweede vraag",
        options=("Fout", "Juist", "Fout", "Fout"),
        correct_index=1,
        time_limit=10,
        points_multiplier=2.0,
    ),
    Question(
        id="c",
        category="🪵 Test",
        text="Schatting",
        type="estimate",
        correct_value=100,
        unit="m",
        tolerance=10,
        max_error=60,
        time_limit=10,
    ),
)


def make_game() -> Game:
    return Game(questions=DEMO)


class NameTests(unittest.TestCase):
    def test_trims_and_collapses(self):
        self.assertEqual(sanitize_name("   Sander   de    Grote  "), "Sander de Grote")

    def test_strips_control_characters(self):
        self.assertEqual(sanitize_name("San​der‮"), "Sander")

    def test_length_limit(self):
        self.assertLessEqual(len(sanitize_name("S" * 200)), 20)

    def test_empty_is_empty(self):
        self.assertEqual(sanitize_name("  \n\t "), "")
        self.assertEqual(sanitize_name(None), "")

    def test_strips_angle_brackets(self):
        self.assertEqual(sanitize_name("<b>Sander</b>"), "bSander/b")


class ScoringTests(unittest.TestCase):
    def test_correct_but_slow_beats_wrong_but_fast(self):
        slow = scoring.compute_score(accuracy=1.0, elapsed_seconds=20, time_limit=20)
        fast_wrong = scoring.compute_score(accuracy=0.0, elapsed_seconds=0.1, time_limit=20)
        self.assertGreater(slow.points, fast_wrong.points)
        self.assertEqual(fast_wrong.points, 0)

    def test_instant_answer_hits_the_maximum(self):
        result = scoring.compute_score(accuracy=1.0, elapsed_seconds=0.2, time_limit=20)
        self.assertEqual(result.points, 1000)

    def test_last_second_answer_still_gets_the_base(self):
        result = scoring.compute_score(accuracy=1.0, elapsed_seconds=20, time_limit=20)
        self.assertEqual(result.points, 500)

    def test_speed_bonus_decreases_monotonically(self):
        previous = 10**9
        for elapsed in range(0, 21):
            points = scoring.compute_score(accuracy=1.0, elapsed_seconds=elapsed, time_limit=20).points
            self.assertLessEqual(points, previous)
            previous = points

    def test_double_points_question(self):
        result = scoring.compute_score(
            accuracy=1.0, elapsed_seconds=0.2, time_limit=20, points_multiplier=2.0
        )
        self.assertEqual(result.points, 2000)

    def test_streak_bonus_is_capped(self):
        result = scoring.compute_score(
            accuracy=1.0, elapsed_seconds=20, time_limit=20, streak_before=99
        )
        self.assertEqual(result.streak_bonus, scoring.SCORING.max_streak_bonus)

    def test_estimate_accuracy(self):
        self.assertEqual(scoring.estimate_accuracy(100, 100, 10, 60), 1.0)
        self.assertEqual(scoring.estimate_accuracy(110, 100, 10, 60), 1.0)
        self.assertEqual(scoring.estimate_accuracy(1000, 100, 10, 60), 0.0)
        middle = scoring.estimate_accuracy(135, 100, 10, 60)
        self.assertTrue(0.2 < middle < 0.8)


class JoinTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_names_get_a_suffix(self):
        game = make_game()
        first, _ = await game.join("Sander")
        second, _ = await game.join("Sander")
        third, _ = await game.join("sander")
        self.assertEqual(first.name, "Sander")
        self.assertEqual(second.name, "Sander (2)")
        # Botsingen zijn hoofdletterongevoelig, maar de speler houdt zijn eigen schrijfwijze.
        self.assertEqual(third.name, "sander (3)")
        self.assertNotEqual(first.id, second.id)

    async def test_long_duplicate_names_stay_within_the_limit(self):
        game = make_game()
        for _ in range(3):
            player, _ = await game.join("A" * 20)
            self.assertLessEqual(len(player.name), 20)
        self.assertEqual(len({p.name for p in game.players.values()}), 3)

    async def test_reconnect_keeps_identity_and_score(self):
        game = make_game()
        player, _ = await game.join("Wout")
        self.assertTrue(player.reconnect_token)
        player.score = 1234
        await game.mark_disconnected(player.id)
        self.assertFalse(game.players[player.id].connected)

        same, reconnected = await game.join("Wout", reconnect_token=player.reconnect_token)
        self.assertTrue(reconnected)
        self.assertEqual(same.id, player.id)
        self.assertEqual(same.score, 1234)
        self.assertTrue(same.connected)
        self.assertEqual(len(game.players), 1)

    async def test_unknown_reconnect_token_creates_a_fresh_player(self):
        game = make_game()
        player, reconnected = await game.join("Nele", reconnect_token="invalid-token")
        self.assertFalse(reconnected)
        self.assertNotEqual(player.reconnect_token, "invalid-token")

    async def test_unknown_player_id_creates_a_fresh_player(self):
        game = make_game()
        player, reconnected = await game.join("Nele", player_id="deadbeefdeadbeef")
        self.assertFalse(reconnected)
        # De server kiest zelf de id: een client mag nooit bepalen wie hij is.
        self.assertNotEqual(player.id, "deadbeefdeadbeef")

    async def test_empty_name_is_refused(self):
        game = make_game()
        with self.assertRaises(ValueError):
            await game.join("   ")


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.game = make_game()
        self.a, _ = await self.game.join("Ann")
        self.b, _ = await self.game.join("Bram")
        self.c, _ = await self.game.join("Cis")

    async def asyncTearDown(self):
        self.game._cancel_task()

    async def test_full_state_machine(self):
        game = self.game
        self.assertIs(game.phase, Phase.LOBBY)

        await game.host_start()
        self.assertIs(game.phase, Phase.QUESTION)
        self.assertEqual(game.q_index, 0)

        await game.submit_answer(self.a.id, choice=0)
        await game.submit_answer(self.b.id, choice=1)
        # Cis antwoordt niet.
        await game.host_reveal()
        self.assertIs(game.phase, Phase.ANSWER_REVEAL)

        self.assertGreater(game.players[self.a.id].score, 0)
        self.assertEqual(game.players[self.b.id].score, 0)
        self.assertEqual(game.players[self.c.id].score, 0)
        self.assertEqual(game.players[self.a.id].streak, 1)
        self.assertEqual(game.players[self.c.id].streak, 0)

        await game.host_next()
        self.assertIs(game.phase, Phase.LEADERBOARD)

        await game.host_next()
        self.assertIs(game.phase, Phase.QUESTION)
        self.assertEqual(game.q_index, 1)

        await game.host_reveal()
        await game.host_next()
        self.assertIs(game.phase, Phase.LEADERBOARD)

        await game.host_next()  # laatste vraag (de schatting)
        self.assertEqual(game.q_index, 2)
        await game.submit_answer(self.a.id, value=105)
        await game.host_reveal()
        await game.host_next()
        self.assertIs(game.phase, Phase.FINISHED)

    async def test_second_answer_is_refused(self):
        await self.game.host_start()
        await self.game.submit_answer(self.a.id, choice=0)
        with self.assertRaises(ValueError):
            await self.game.submit_answer(self.a.id, choice=1)
        stored = self.game.answers[0][self.a.id]
        self.assertEqual(stored.choice, 0)

    async def test_simultaneous_answers_are_all_recorded(self):
        """Drie spelers die op exact hetzelfde moment tikken."""
        await self.game.host_start()
        results = await asyncio.gather(
            self.game.submit_answer(self.a.id, choice=0),
            self.game.submit_answer(self.b.id, choice=0),
            self.game.submit_answer(self.c.id, choice=0),
        )
        self.assertEqual(len(results), 3)
        self.assertEqual(len(self.game.answers[0]), 3)
        await self.game.host_reveal()
        scores = {p.name: p.score for p in self.game.players.values()}
        # Alle drie juist, alle drie ongeveer even snel: geen enkele score op 0.
        self.assertTrue(all(value > 0 for value in scores.values()), scores)

    async def test_same_player_racing_itself_only_counts_once(self):
        await self.game.host_start()
        outcomes = await asyncio.gather(
            self.game.submit_answer(self.a.id, choice=0),
            self.game.submit_answer(self.a.id, choice=2),
            return_exceptions=True,
        )
        errors = [o for o in outcomes if isinstance(o, ValueError)]
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(self.game.answers[0]), 1)

    async def test_answer_after_deadline_is_refused(self):
        game = self.game
        await game.host_start()
        game._deadline = game._now() - 0.01  # tijd is om
        with self.assertRaises(ValueError):
            await game.submit_answer(self.a.id, choice=0)

    async def test_answer_outside_question_phase_is_refused(self):
        with self.assertRaises(ValueError):
            await self.game.submit_answer(self.a.id, choice=0)
        await self.game.host_start()
        await self.game.host_reveal()
        with self.assertRaises(ValueError):
            await self.game.submit_answer(self.a.id, choice=0)

    async def test_timer_closes_the_question_by_itself(self):
        game = Game(questions=(Question(
            id="snel", category="🌳 Test", text="Kort",
            options=("Ja", "Nee", "Misschien", "Wortel"), correct_index=0, time_limit=1,
        ),))
        player, _ = await game.join("Ann")
        await game.host_start()
        await game.submit_answer(player.id, choice=0)
        for _ in range(40):
            if game.phase is not Phase.QUESTION:
                break
            await asyncio.sleep(0.1)
        self.assertIs(game.phase, Phase.ANSWER_REVEAL)
        self.assertGreater(game.players[player.id].score, 0)
        game._cancel_task()

    async def test_pause_freezes_the_clock(self):
        game = self.game
        await game.host_start()
        await game.host_pause()
        remaining = game._remaining()
        await asyncio.sleep(0.35)
        self.assertAlmostEqual(remaining, game._remaining(), places=3)
        with self.assertRaises(ValueError):
            await game.submit_answer(self.a.id, choice=0)
        await game.host_resume()
        await game.submit_answer(self.a.id, choice=0)
        self.assertIn(self.a.id, game.answers[0])

    async def test_restart_clears_scores_but_keeps_players(self):
        game = self.game
        await game.host_start()
        await game.submit_answer(self.a.id, choice=0)
        await game.host_reveal()
        self.assertGreater(game.players[self.a.id].score, 0)

        await game.host_restart()
        self.assertIs(game.phase, Phase.LOBBY)
        self.assertEqual(game.q_index, -1)
        self.assertEqual(len(game.players), 3)
        self.assertTrue(all(p.score == 0 and p.streak == 0 for p in game.players.values()))
        self.assertTrue(all(len(bucket) == 0 for bucket in game.answers))

    async def test_finish_jumps_straight_to_the_end(self):
        await self.game.host_start()
        await self.game.host_finish()
        self.assertIs(self.game.phase, Phase.FINISHED)
        podium = self.game.snapshot("host")["podium"]
        self.assertIsNotNone(podium)

    async def test_streaks_break_on_a_wrong_answer(self):
        game = self.game
        await game.host_start()
        await game.submit_answer(self.a.id, choice=0)
        await game.host_reveal()
        self.assertEqual(game.players[self.a.id].streak, 1)
        await game.host_next()
        await game.host_next()
        await game.submit_answer(self.a.id, choice=0)  # fout op vraag 2
        await game.host_reveal()
        self.assertEqual(game.players[self.a.id].streak, 0)
        self.assertEqual(game.players[self.a.id].best_streak, 1)

    async def test_estimate_scoring_rewards_the_closest(self):
        game = self.game
        await game.host_start()
        await game.host_reveal(); await game.host_next(); await game.host_next()   # vraag 2
        await game.host_reveal(); await game.host_next(); await game.host_next()   # vraag 3 (schatting)
        self.assertEqual(game.q_index, 2)

        await game.submit_answer(self.a.id, value=100)  # perfect
        await game.submit_answer(self.b.id, value=130)  # er redelijk naast
        await game.submit_answer(self.c.id, value=5000)  # kilometers ernaast
        await game.host_reveal()

        a, b, c = (game.players[p.id] for p in (self.a, self.b, self.c))
        self.assertGreater(a.last_points, b.last_points)
        self.assertGreater(b.last_points, c.last_points)
        self.assertEqual(c.last_points, 0)

        reveal = game.snapshot("host")["reveal"]
        self.assertEqual(reveal["closest"][0]["name"], "Ann")


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.game = make_game()
        self.player, _ = await self.game.join("Ann")

    async def asyncTearDown(self):
        self.game._cancel_task()

    async def test_players_never_see_the_answer_during_a_question(self):
        await self.game.host_start()
        snapshot = self.game.snapshot("player", self.player.id)
        self.assertNotIn("correct_index", snapshot["question"])
        self.assertIsNone(snapshot["reveal"])

        host_snapshot = self.game.snapshot("host")
        self.assertNotIn("correct_index", host_snapshot["question"])

    async def test_answer_appears_after_the_reveal(self):
        await self.game.host_start()
        await self.game.host_reveal()
        snapshot = self.game.snapshot("player", self.player.id)
        self.assertEqual(snapshot["question"]["correct_index"], 0)
        self.assertEqual(snapshot["reveal"]["correct_text"], "Juist")

    async def test_player_snapshot_has_no_player_list(self):
        snapshot = self.game.snapshot("player", self.player.id)
        self.assertNotIn("players", snapshot)
        self.assertIn("you", snapshot)

    async def test_unknown_player_gets_no_you_block(self):
        snapshot = self.game.snapshot("player", "bestaat-niet")
        self.assertIsNone(snapshot["you"])

    async def test_percentages_add_up(self):
        game = self.game
        b, _ = await game.join("Bram")
        c, _ = await game.join("Cis")
        d, _ = await game.join("Dries")
        await game.host_start()
        await game.submit_answer(self.player.id, choice=0)
        await game.submit_answer(b.id, choice=0)
        await game.submit_answer(c.id, choice=1)
        await game.submit_answer(d.id, choice=3)
        await game.host_reveal()
        counts = game.snapshot("host")["reveal"]["counts"]
        self.assertEqual(sum(entry["count"] for entry in counts), 4)
        self.assertEqual(sum(entry["pct"] for entry in counts), 100)


class QuestionDataTests(unittest.TestCase):
    def test_shipped_quiz_is_valid(self):
        from app.quiz.questions import QUESTIONS, validate_questions

        validate_questions(QUESTIONS)
        self.assertGreaterEqual(len(QUESTIONS), 20)
        self.assertLessEqual(len(QUESTIONS), 40)
        for question in QUESTIONS:
            self.assertTrue(question.text.strip())
            self.assertTrue(question.category.strip())
            if question.type != "estimate":
                self.assertEqual(len(question.options), 4)
                self.assertEqual(len(set(question.options)), 4, question.id)

    def test_rejects_a_broken_question(self):
        with self.assertRaises(ValueError):
            Question(id="x", category="c", text="t", options=("a", "b"))
        with self.assertRaises(ValueError):
            Question(id="x", category="c", text="t", type="estimate")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class PlayerAdminTests(unittest.IsolatedAsyncioTestCase):
    """Spelers verwijderen: kick, afwezigen opkuisen, lobby leegmaken."""

    async def asyncSetUp(self):
        self.game = make_game()
        self.a, _ = await self.game.join("Aya")
        self.b, _ = await self.game.join("Bram")
        self.c, _ = await self.game.join("Cis")

    async def test_kick_removes_one_player_and_reports_the_name(self):
        name = await self.game.kick(self.b.id)
        self.assertEqual(name, "Bram")
        self.assertNotIn(self.b.id, self.game.players)
        self.assertEqual(len(self.game.players), 2)

    async def test_kick_of_an_unknown_player_is_harmless(self):
        self.assertIsNone(await self.game.kick("bestaat-niet"))
        self.assertEqual(len(self.game.players), 3)

    async def test_kicked_player_loses_the_you_block(self):
        await self.game.kick(self.a.id)
        snapshot = self.game.snapshot("player", self.a.id)
        self.assertIsNone(snapshot["you"])

    async def test_kick_also_drops_the_given_answer(self):
        await self.game.host_start()
        await self.game.submit_answer(self.a.id, choice=0)
        self.assertEqual(len(self.game.answers[0]), 1)
        await self.game.kick(self.a.id)
        self.assertEqual(len(self.game.answers[0]), 0)

    async def test_clear_absent_only_removes_the_disconnected(self):
        await self.game.mark_disconnected(self.b.id)
        removed = await self.game.clear_absent()
        self.assertEqual(removed, 1)
        self.assertEqual(set(self.game.players), {self.a.id, self.c.id})

    async def test_clear_all_empties_the_lobby(self):
        removed = await self.game.clear_all_players()
        self.assertEqual(removed, 3)
        self.assertEqual(self.game.players, {})

    async def test_clear_all_is_refused_once_the_quiz_runs(self):
        await self.game.host_start()
        self.assertEqual(await self.game.clear_all_players(), 0)
        self.assertEqual(len(self.game.players), 3)


class QuestionEditingTests(unittest.IsolatedAsyncioTestCase):
    """De vrageneditor van /admin, inclusief bewaren op schijf."""

    def setUp(self):
        self._original = config.QUESTIONS_FILE
        self._dir = Path(tempfile.mkdtemp())
        config.QUESTIONS_FILE = self._dir / "questions.json"

    def tearDown(self):
        config.QUESTIONS_FILE = self._original
        shutil.rmtree(self._dir, ignore_errors=True)

    @property
    def _file(self) -> Path:
        return config.QUESTIONS_FILE

    @staticmethod
    def _payload(**overrides):
        data = {
            "id": "nieuw",
            "category": "Kamp",
            "text": "Hoe heet deze knoop?",
            "type": "multiple_choice",
            "options": ["Mastworp", "Paalsteek", "Platte knoop", "Schootsteek"],
            "correct_index": 0,
            "time_limit": 45,
        }
        data.update(overrides)
        return data

    async def test_saving_replaces_the_list_and_writes_the_file(self):
        game = make_game()
        total = await game.set_questions([self._payload()])
        self.assertEqual(total, 1)
        self.assertEqual(game.total_questions, 1)
        self.assertEqual(game.questions[0].text, "Hoe heet deze knoop?")
        self.assertTrue(self._file.exists())

    async def test_saved_questions_survive_a_restart(self):
        game = make_game()
        await game.set_questions([self._payload(text="Blijft dit staan?")])
        # Een verse Game leest wat er bewaard is -- net als na een herstart.
        self.assertEqual(Game().questions[0].text, "Blijft dit staan?")

    async def test_reset_returns_to_the_built_in_questions(self):
        game = make_game()
        await game.set_questions([self._payload()])
        total = await game.reset_questions()
        self.assertEqual(total, len(QUESTIONS))
        self.assertFalse(self._file.exists())

    async def test_answer_buckets_follow_the_new_length(self):
        game = make_game()
        await game.set_questions([self._payload(id="x"), self._payload(id="y")])
        self.assertEqual(len(game.answers), 2)

    async def test_editing_is_refused_while_the_quiz_runs(self):
        game = make_game()
        await game.join("Aya")
        await game.host_start()
        with self.assertRaises(ValueError):
            await game.set_questions([self._payload()])
        self.assertEqual(game.total_questions, len(DEMO))

    async def test_a_broken_question_is_refused_before_anything_changes(self):
        game = make_game()
        with self.assertRaises(ValueError) as caught:
            await game.set_questions(
                [self._payload(), self._payload(id="stuk", options=["een", "twee"])]
            )
        self.assertIn("Vraag 2", str(caught.exception))
        # De oude lijst staat er nog: een halve opslag mag niet bestaan.
        self.assertEqual(game.total_questions, len(DEMO))
        self.assertFalse(self._file.exists())

    async def test_duplicate_ids_are_refused(self):
        game = make_game()
        with self.assertRaises(ValueError):
            await game.set_questions([self._payload(), self._payload()])

    async def test_empty_list_is_refused(self):
        game = make_game()
        with self.assertRaises(ValueError):
            await game.set_questions([])

    async def test_time_limit_is_clamped_to_the_allowed_range(self):
        game = make_game()
        await game.set_questions([self._payload(time_limit=6000)])
        self.assertEqual(game.questions[0].time_limit, config.MAX_TIME_LIMIT)

    async def test_estimate_question_needs_a_tolerance(self):
        game = make_game()
        with self.assertRaises(ValueError):
            await game.set_questions(
                [self._payload(type="estimate", correct_value=10, tolerance=0)]
            )

    async def test_questions_payload_carries_what_the_editor_needs(self):
        game = make_game()
        payload = game.questions_payload()
        self.assertEqual(len(payload["items"]), len(DEMO))
        self.assertTrue(payload["editable"])
        self.assertIn("categories", payload)
        self.assertEqual(payload["limits"]["max_time"], config.MAX_TIME_LIMIT)

    async def test_payload_is_not_editable_outside_the_lobby(self):
        game = make_game()
        await game.host_start()
        self.assertFalse(game.questions_payload()["editable"])

    def test_round_trip_through_json(self):
        for question in QUESTIONS:
            self.assertEqual(from_dict(to_dict(question)), question)

    def test_a_corrupt_file_falls_back_to_the_defaults(self):
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text("{ dit is geen json", encoding="utf-8")
        # De waarschuwing hoort erbij, maar niet in de testuitvoer.
        with self.assertLogs("quiz.store", level="WARNING"):
            self.assertEqual(len(store.load()), len(QUESTIONS))


class SpeedWindowTests(unittest.TestCase):
    """Een ruime bedenktijd mag de snelheidsbonus niet uitvlakken."""

    def test_bonus_window_is_independent_of_the_time_limit(self):
        # Binnen het bonusvenster telt enkel hoe snel je was, niet hoe lang de
        # vraag openstond. Anders krijgt iedereen bij 90 seconden bijna hetzelfde.
        short = scoring.compute_score(accuracy=1.0, elapsed_seconds=6, time_limit=20)
        long = scoring.compute_score(accuracy=1.0, elapsed_seconds=6, time_limit=90)
        self.assertEqual(short.points, long.points)

    def test_a_long_question_still_separates_fast_from_slow(self):
        fast = scoring.compute_score(accuracy=1.0, elapsed_seconds=2, time_limit=90)
        slow = scoring.compute_score(accuracy=1.0, elapsed_seconds=18, time_limit=90)
        self.assertGreater(fast.points - slow.points, 300)

    def test_short_questions_keep_their_own_window(self):
        # Bij een vraag korter dan het bonusvenster telt de bedenktijd zelf.
        self.assertAlmostEqual(scoring.speed_ratio(10, 10), 0.0, places=3)

    def test_answering_after_the_window_still_earns_the_base(self):
        late = scoring.compute_score(accuracy=1.0, elapsed_seconds=75, time_limit=90)
        self.assertEqual(late.speed_bonus, 0)
        self.assertEqual(late.points, scoring.SCORING.base_points)

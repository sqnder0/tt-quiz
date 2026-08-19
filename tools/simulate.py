"""End-to-end test: speelt de volledige quiz met echte WebSocket-clients.

Start eerst de server (zie README) en dan:

    python tools/simulate.py --players 25

Wat dit doet:
  * verbindt N spelers via /ws/play en laat ze meedoen
  * verbindt een host via /ws/host en speelt de hele quiz af
  * laat sommige spelers midden in een vraag "crashen" en opnieuw verbinden
  * laat sommige spelers te laat of helemaal niet antwoorden
  * controleert achteraf of de scores kloppen met wat de spelers zelf zagen

Aan het einde volgt een samenvatting met OK/FOUT per controle.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from typing import Any, Optional

from websockets.asyncio.client import connect

FAILURES: list[str] = []
CHECKS: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        CHECKS.append(f"  OK   {label}")
    else:
        message = f"  FOUT {label}" + (f" -- {detail}" if detail else "")
        CHECKS.append(message)
        FAILURES.append(message)


class Client:
    """Minimale WebSocket-client die de laatste snapshot bijhoudt."""

    def __init__(self, url: str, label: str) -> None:
        self.url = url
        self.label = label
        self.ws = None
        self.state: dict[str, Any] = {}
        self.events: list[str] = []
        self.errors: list[str] = []
        self.player_id: Optional[str] = None
        self.name: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        self._ready = asyncio.Event()

    async def open(self) -> None:
        self.ws = await connect(self.url, max_queue=64)
        self._task = asyncio.create_task(self._reader())

    async def _reader(self) -> None:
        try:
            async for raw in self.ws:
                message = json.loads(raw)
                kind = message.get("t")
                if kind == "state":
                    self.state = message
                    self._ready.set()
                elif kind == "joined":
                    self.player_id = message["player_id"]
                    self.name = message["name"]
                elif kind == "event":
                    self.events.append(message["name"])
                elif kind == "error":
                    self.errors.append(message.get("message", ""))
        except Exception:
            pass

    async def send(self, payload: dict[str, Any]) -> None:
        await self.ws.send(json.dumps(payload))

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
        if self._task is not None:
            self._task.cancel()

    async def wait_state(self, timeout: float = 5.0) -> None:
        await asyncio.wait_for(self._ready.wait(), timeout)

    async def wait_for(self, predicate, timeout: float = 12.0, poll: float = 0.05) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self.state and predicate(self.state):
                return True
            await asyncio.sleep(poll)
        return False


class Player(Client):
    def __init__(self, base: str, name: str) -> None:
        super().__init__(base.replace("http", "ws", 1) + "/ws/play", name)
        self.wanted_name = name
        self.answered_questions: set[str] = set()
        self.expected_points = 0

    async def join(self) -> None:
        await self.send({"t": "join", "name": self.wanted_name, "player_id": self.player_id})
        for _ in range(60):
            if self.player_id:
                return
            await asyncio.sleep(0.05)
        raise RuntimeError(f"{self.wanted_name} raakte niet binnen")

    async def reconnect(self) -> None:
        """Simuleert een gsm die op slot ging: socket weg, daarna terug met dezelfde id."""
        await self.close()
        self._ready.clear()
        await self.open()
        await self.join()

    async def answer(self, number: int | None = None) -> bool:
        if number is not None:
            # Wachten tot deze speler zelf de juiste vraag binnen heeft. Zonder dit
            # leest de simulator soms nog de vorige vraag en slaat hij er een over.
            ready = await self.wait_for(
                lambda s: s.get("phase") == "QUESTION"
                and s.get("question")
                and s["question"]["number"] == number,
                timeout=6,
            )
            if not ready:
                return False
        question = self.state.get("question")
        if not question or question["id"] in self.answered_questions:
            return False
        self.answered_questions.add(question["id"])
        if question["type"] == "estimate":
            await self.send({"t": "submit_answer", "value": random.choice([1, 4, 10, 30, 95])})
        else:
            # De simulator kent het juiste antwoord niet (dat stuurt de server niet mee),
            # dus gokken we -- precies zoals een speler.
            await self.send({"t": "submit_answer", "choice": random.randrange(4)})
        return True


class Host(Client):
    def __init__(self, base: str, secret: str, view: str = "admin") -> None:
        url = base.replace("http", "ws", 1) + "/ws/host?secret=" + secret + "&view=" + view
        super().__init__(url, view)
        self.questions: dict[str, Any] = {}

    async def _reader(self) -> None:
        # Naast de gewone berichten vangt de host ook de vragenlijst op.
        try:
            async for raw in self.ws:
                message = json.loads(raw)
                kind = message.get("t")
                if kind == "state":
                    self.state = message
                    self._ready.set()
                elif kind == "questions":
                    self.questions = message
                elif kind == "event":
                    self.events.append(message["name"])
                elif kind == "error":
                    self.errors.append(message.get("message", ""))
        except Exception:
            pass

    async def phase(self) -> str:
        return self.state.get("phase", "?")


async def run(base: str, secret: str, player_count: int) -> None:
    random.seed(20260819)

    print(f">> Host verbinden met {base}/ws/host")
    host = Host(base, secret)
    await host.open()
    await host.wait_state()
    check("host krijgt een snapshot", host.state.get("phase") is not None)

    # Er kan al iemand in de lobby zitten (een browsertab die je zelf open hebt).
    # We meten dus alles ten opzichte van deze beginstand in plaats van nul aan te nemen.
    baseline = len(host.state.get("players", []))
    if baseline:
        print(f"   ({baseline} speler(s) zaten er al in; die tellen we niet mee)")

    # --- spelers laten binnenkomen ----------------------------------------
    names = ["Sander", "Wout", "Nele", "Lars", "Fien", "Jonas", "Marit", "Tuur",
             "Lotte", "Seppe", "Anke", "Rune", "Elise", "Milan", "Jarne"]
    players: list[Player] = []
    for index in range(player_count):
        # Bewust dubbele namen: de server moet er "Sander (2)" van maken.
        name = names[index % 5]
        player = Player(base, name)
        await player.open()
        players.append(player)
    await asyncio.gather(*(p.join() for p in players))
    await asyncio.sleep(0.4)

    unique_names = {p.name for p in players}
    check("iedereen heeft een unieke naam", len(unique_names) == player_count,
          f"{len(unique_names)} van {player_count}")
    check("host ziet alle spelers", len(host.state.get("players", [])) == baseline + player_count,
          f"host ziet er {len(host.state.get('players', []))}")

    duplicates = [p.name for p in players if p.name != p.wanted_name]
    check("dubbele namen kregen een achtervoegsel", len(duplicates) > 0,
          "geen enkele naam werd aangepast")

    # --- beveiliging ------------------------------------------------------
    # Een deelnemer mag nooit een hostcommando kunnen uitvoeren.
    intruder = players[0]
    before_errors = len(intruder.errors)
    before_phase = host.state["phase"]
    for command in ("host_start", "host_next", "host_reveal", "host_restart", "host_finish"):
        await intruder.send({"t": command})
    await asyncio.sleep(0.4)
    check("spelers kunnen geen hostcommando's uitvoeren",
          host.state["phase"] == before_phase and len(intruder.errors) > before_errors,
          f"fase ging van {before_phase} naar {host.state['phase']}")

    # Een verkeerde hostcode moet geweigerd worden.
    try:
        bad = await connect(base.replace("http", "ws", 1) + "/ws/host?secret=fout-natuurlijk")
        await bad.recv()
        await bad.close()
        check("verkeerde hostcode wordt geweigerd", False, "de verbinding werd aanvaard")
    except Exception:
        check("verkeerde hostcode wordt geweigerd", True)

    # --- twee hostschermen tegelijk ---------------------------------------
    # /present op de beamer en /admin op de laptop moeten naast elkaar werken;
    # vroeger verdrong het tweede hostscherm het eerste.
    beamer = Host(base, secret, view="present")
    await beamer.open()
    await beamer.wait_state()
    check("beamer en bediening kunnen tegelijk verbonden zijn",
          beamer.state.get("phase") is not None and host.state.get("phase") is not None)

    before_errors = len(host.errors)
    await host.send({"t": "state"})
    await asyncio.sleep(0.3)
    check("de eerste host blijft commando's mogen sturen", len(host.errors) == before_errors,
          f"host kreeg: {host.errors[before_errors:]}")

    # --- vrageneditor -----------------------------------------------------
    await host.send({"t": "host_questions"})
    await asyncio.sleep(0.3)
    check("host krijgt de vragenlijst voor de editor",
          len(host.questions.get("items", [])) == host.state["quiz"]["total_questions"],
          f"kreeg er {len(host.questions.get('items', []))}")
    check("de vragenlijst is bewerkbaar in de lobby", host.questions.get("editable") is True)
    check("de editor kent de bedenktijdgrenzen",
          host.questions.get("limits", {}).get("max_time", 0) >= 60)

    # Een kapotte vraag mag nooit bewaard worden.
    before_errors = len(host.errors)
    await host.send({"t": "host_questions_set", "items": [{"id": "stuk", "text": "Half", "options": ["een"]}]})
    await asyncio.sleep(0.35)
    check("een onvolledige vraag wordt geweigerd", len(host.errors) > before_errors,
          "de server aanvaardde een vraag met één antwoord")

    before_errors = len(host.errors)
    await host.send({"t": "host_questions_set", "items": []})
    await asyncio.sleep(0.35)
    check("een lege vragenlijst wordt geweigerd", len(host.errors) > before_errors)

    # --- host-refresh -----------------------------------------------------
    await host.close()
    host = Host(base, secret)
    await host.open()
    await host.wait_state()
    check("host kan herverbinden na refresh", len(host.state.get("players", [])) == baseline + player_count)

    # --- de quiz spelen ---------------------------------------------------
    await host.send({"t": "host_start"})
    ok = await host.wait_for(lambda s: s["phase"] == "QUESTION")
    check("quiz gestart", ok)

    total_questions = host.state["quiz"]["total_questions"]
    print(f">> {total_questions} vragen spelen met {player_count} spelers")

    reconnect_victim = players[0]
    late_player = players[1]
    silent_player = players[2]

    for number in range(1, total_questions + 1):
        ok = await host.wait_for(lambda s: s["phase"] == "QUESTION" and s["question"]["number"] == number)
        if not ok:
            check(f"vraag {number} gestart", False, f"fase bleef {host.state.get('phase')}")
            break

        # Iedereen antwoordt, behalve de stille speler.
        answered = await asyncio.gather(
            *(p.answer(number) for p in players if p is not silent_player and p is not late_player)
        )
        expected_answers = sum(1 for ok in answered if ok)

        if number == 1:
            # Speler 0 valt weg midden in de vraag en komt terug.
            await reconnect_victim.reconnect()
            back = await reconnect_victim.wait_for(
                lambda s: s.get("you") is not None and s["you"]["answered"], timeout=5
            )
            check("speler ziet na reconnect zijn antwoord terug", back)

        # De trage speler antwoordt pas nadat de host onthuld heeft: moet geweigerd worden.
        await host.send({"t": "host_reveal"})
        ok = await host.wait_for(lambda s: s["phase"] == "ANSWER_REVEAL")
        if not ok:
            check(f"vraag {number} onthuld", False)
            break

        before = len(late_player.errors)
        await late_player.send({"t": "submit_answer", "choice": 0})
        await asyncio.sleep(0.25)
        if number == 1:
            check("te laat antwoorden wordt geweigerd", len(late_player.errors) > before,
                  "de server accepteerde een antwoord na de onthulling")

            # Vragen aanpassen midden in een quiz zou de nummering doen schuiven.
            host_errors = len(host.errors)
            await host.send({"t": "host_questions_set", "items": [{
                "id": "tijdens", "text": "Mag niet", "category": "🏕️ Kamp",
                "options": ["a", "b", "c", "d"], "correct_index": 0, "time_limit": 30,
            }]})
            await host.send({"t": "host_clear_all"})
            await asyncio.sleep(0.35)
            check("vragen aanpassen wordt geweigerd tijdens de quiz",
                  len(host.errors) > host_errors)
            check("lobby leegmaken doet niets tijdens de quiz",
                  len(host.state.get("players", [])) == baseline + player_count,
                  f"er blijven er {len(host.state.get('players', []))}")

        reveal = host.state["reveal"]
        registered = reveal["num_answered"]
        check(f"vraag {number}: aantal antwoorden klopt", registered == expected_answers,
              f"{registered} geregistreerd, {expected_answers} verwacht")

        if reveal["type"] != "estimate":
            check(f"vraag {number}: percentages tellen op tot 100",
                  sum(c["pct"] for c in reveal["counts"]) == 100 or registered == 0)

        # Spelers mogen het juiste antwoord nooit vroeger zien dan de host.
        player_view = players[3].state
        if player_view.get("phase") == "ANSWER_REVEAL":
            check(f"vraag {number}: speler ziet nu pas het juiste antwoord",
                  player_view["reveal"]["correct_text"] == reveal["correct_text"])

        await host.send({"t": "host_next"})
        if number < total_questions:
            ok = await host.wait_for(lambda s: s["phase"] == "LEADERBOARD")
            check(f"vraag {number}: tussenstand verschijnt", ok)
            await host.send({"t": "host_next"})

    # --- finale -----------------------------------------------------------
    ok = await host.wait_for(lambda s: s["phase"] == "FINISHED", timeout=15)
    check("quiz eindigt in de finale", ok, f"fase is {host.state.get('phase')}")

    if ok:
        standings = host.state["standings"]
        check("iedereen staat in het eindklassement", len(standings) == baseline + player_count)
        scores = [row["score"] for row in standings]
        check("klassement is aflopend gesorteerd", scores == sorted(scores, reverse=True))
        check("de stille speler heeft 0 punten",
              next(r["score"] for r in standings if r["id"] == silent_player.player_id) == 0)
        check("er is een winnaar met punten", scores[0] > 0, f"topscore is {scores[0]}")

        # Wat de speler zelf zag moet gelijk zijn aan wat de host ziet.
        sample = players[5]
        host_row = next(r for r in standings if r["id"] == sample.player_id)
        check("spelerscore komt overeen met de hostscore",
              sample.state["you"]["score"] == host_row["score"],
              f"speler {sample.state['you']['score']} vs host {host_row['score']}")

        print(f"   Winnaar: {standings[0]['name']} met {standings[0]['score']} punten")

    # --- reset ------------------------------------------------------------
    await host.send({"t": "host_restart"})
    ok = await host.wait_for(lambda s: s["phase"] == "LOBBY")
    check("reset brengt ons terug naar de lobby", ok)
    await asyncio.sleep(0.3)
    check("reset houdt de spelers binnen", len(host.state.get("players", [])) == baseline + player_count)
    check("reset wist alle scores", all(p["score"] == 0 for p in host.state.get("players", [])))
    check("spelers zien de lobby opnieuw",
          all(p.state.get("phase") == "LOBBY" for p in players if p.state))

    # Opruimen: onze eigen spelers weer uit de quiz halen, zodat een volgende run
    # met een propere lobby begint (en de kick-actie meteen getest is).
    for player in players:
        await host.send({"t": "host_kick", "player_id": player.player_id})
    await asyncio.sleep(0.5)
    check("kick verwijdert de spelers", len(host.state.get("players", [])) == baseline,
          f"er blijven er {len(host.state.get('players', []))} staan, {baseline} verwacht")

    if baseline == 0:
        # Alleen veilig als er niemand anders in de lobby zit: dit gooit iedereen buiten.
        rejoin = Player(base, "Laatkomer")
        await rejoin.open()
        await rejoin.join()
        await asyncio.sleep(0.3)
        await host.send({"t": "host_clear_all"})
        await asyncio.sleep(0.4)
        check("lobby leegmaken haalt iedereen eruit",
              len(host.state.get("players", [])) == 0,
              f"er blijven er {len(host.state.get('players', []))}")
        check("de weggehaalde speler ziet zijn naamscherm terug",
              rejoin.state.get("you") is None)
        await rejoin.close()
    else:
        print("   (lobby leegmaken overgeslagen: er zaten al spelers in)")

    for player in players:
        await player.close()
    await beamer.close()
    await host.close()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Simuleert een volledige quizsessie.")
    parser.add_argument("--url", default="http://localhost:8000", help="basis-URL van de server")
    parser.add_argument("--players", type=int, default=12, help="aantal spelers")
    parser.add_argument("--secret", default="boomhut", help="HOST_SECRET van de server")
    args = parser.parse_args()

    try:
        await run(args.url.rstrip("/"), args.secret, args.players)
    except Exception as exc:
        print(f"\nSimulatie afgebroken: {exc!r}")
        FAILURES.append(f"  FOUT simulatie liep vast: {exc!r}")

    print("\n--- Resultaten ---")
    for line in CHECKS:
        print(line)
    print(f"\n{len(CHECKS) - len(FAILURES)}/{len(CHECKS)} controles geslaagd.")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(asyncio.run(main()))

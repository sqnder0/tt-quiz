# 🌳 Jonge Helden Boomhutten Quiz

Een live quiz voor het boomhuttenkamp. Het regent, de boomhutten kunnen niet
verder — iedereen krijgt de link, tikt zijn naam in en staat meteen in de lobby.

**Geen roomcodes. Geen accounts. Geen wachtwoorden.** Eén quiz, één sessie.

| Wie | Waar | Wat |
|---|---|---|
| Deelnemers | `/` | Naam ingeven en spelen |
| Beamer | `/present` | Alleen tonen — vraag, timer, tussenstand, podium |
| Leiding | `/admin` | Knoppen, spelers en de vrageneditor |

`/present` en `/admin` mogen **tegelijk** open staan: beamer op het grote scherm,
bediening op je eigen laptop of gsm. Beide vragen eenmalig de hostcode.

---

## Snel starten (lokaal)

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

Open daarna `http://localhost:8000/admin` (hostcode: `boomhut`),
`http://localhost:8000/present` op de beamer en `http://localhost:8000/` op de gsm's.

Op macOS/Linux is dat `.venv/bin/python` in plaats van `.venv/Scripts/python.exe`.

### Met meerdere spelers tegelijk testen

Open meerdere browservensters. Let op: gebruik **aparte** vensters in privémodus
of verschillende browsers — spelers worden herkend aan een id in `localStorage`,
dus twee gewone tabs in dezelfde browser zijn dezelfde speler.

Of laat het scriptje het werk doen (server moet al draaien):

```bash
.venv/Scripts/python.exe tools/simulate.py --players 25
```

Dat verbindt 25 echte WebSocket-clients, speelt de hele quiz af, laat spelers
crashen en herverbinden, antwoordt te laat, opent beamer en bediening tegelijk,
test de vrageneditor en de opkuisknoppen, en rapporteert per controle OK of FOUT.

---

## Instellingen

Alles gaat via environment variables. Enkel `HOST_SECRET` wil je zeker aanpassen.

| Variabele | Standaard | Waarvoor |
|---|---|---|
| `HOST_SECRET` | `boomhut` | Code voor `/present` en `/admin`. **Zet dit voor je deployt.** |
| `PORT` | `8000` | Poort (Dokploy vult dit zelf in) |
| `QUIZ_TITLE` | Jonge Helden Boomhutten Quiz | Titel op alle schermen |
| `QUIZ_SUBTITLE` | Boomhuttenkamp — regendag-editie | Ondertitel op het naamscherm |
| `QUESTIONS_FILE` | `data/questions.json` | Waar aangepaste vragen bewaard worden |
| `MAX_NAME_LENGTH` | `20` | Maximale lengte van een nickname |
| `MAX_PLAYERS` | `200` | Plafond op het aantal spelers |
| `DEFAULT_TIME_LIMIT` | `90` | Bedenktijd voor een nieuwe vraag |
| `MAX_TIME_LIMIT` | `90` | Bovengrens in de vrageneditor |
| `AUTO_CLOSE_WHEN_ALL_ANSWERED` | `true` | Vraag meteen afsluiten als iedereen binnen is |
| `AUTO_ADVANCE` | `false` | Hands-free modus (ook live schakelbaar op `/admin`) |
| `AUTO_REVEAL_SECONDS` | `8` | Hoe lang het antwoord blijft staan bij hands-free |
| `AUTO_LEADERBOARD_SECONDS` | `7` | Hoe lang de tussenstand blijft staan bij hands-free |

De punten staan in [`app/quiz/scoring.py`](app/quiz/scoring.py), de startvragen in
[`app/quiz/questions.py`](app/quiz/questions.py).

---

## Deployen op Dokploy (Nixpacks)

1. Nieuwe **Application** aanmaken en deze repository koppelen.
2. Build type: **Nixpacks** (wordt herkend via `nixpacks.toml`).
3. Environment variable zetten: `HOST_SECRET=<iets-eigens>`.
4. Deployen. De poort komt uit `PORT`, er staat nergens een localhost hardcoded.

Het startcommando staat in `nixpacks.toml` (en identiek in `Procfile`):

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=* --ws websockets --timeout-keep-alive 65
```

`--proxy-headers` zorgt dat de app achter de reverse proxy het juiste schema ziet;
de frontend kiest zelf `ws://` of `wss://` op basis van de pagina-URL, dus achter
HTTPS werkt het zonder verdere configuratie. `/healthz` kan je als health check
instellen.

> **Let op:** de hele quiz zit in het geheugen van één proces. Draai dus **één**
> instantie, zonder extra workers. Meer workers betekent meerdere quizzen die
> elkaar niet zien. Voor een kamp met 30 gsm'en is één proces ruim voldoende.
> Een herstart wist de lopende quiz — spelers moeten dan opnieuw hun naam ingeven.

> **Wil je dat aangepaste vragen een herdeploy overleven?** Hang dan een volume
> aan de map uit `QUESTIONS_FILE` (standaard `data/`). Zonder volume start de app
> na een deploy gewoon weer met de ingebouwde vragen — je verliest niets anders.

---

## Zo werkt het

### Toestandsmachine (server beslist, altijd)

```
LOBBY ──start──▶ QUESTION ──tijd op / onthullen──▶ ANSWER_REVEAL
                    ▲                                    │
                    │                                volgende
                    │                                    ▼
                    └───────volgende──────────────  LEADERBOARD
                                                         │
                                   (na de laatste vraag) ▼
                                                     FINISHED
```

De client bepaalt nooit wanneer een vraag begint of eindigt, en berekent nooit
punten. Hij tekent enkel wat de server stuurt.

### WebSocket-protocol

Twee endpoints: `/ws/play` voor spelers, `/ws/host?secret=…&view=…` voor de
leiding. `view` is `present` of `admin` en is puur informatief — beide krijgen
dezelfde snapshot en mogen dezelfde commando's sturen. Spelersockets kennen de
hostcommando's letterlijk niet: die code staat in een andere handler.

**Client → server**

| Speler | Host |
|---|---|
| `join` · `submit_answer` · `state` · `ping` | `host_start` · `host_reveal` · `host_next` · `host_pause` · `host_resume` · `host_restart` · `host_finish` · `host_kick` · `host_clear_absent` · `host_clear_all` · `host_questions` · `host_questions_set` · `host_questions_reset` · `host_options` |

**Server → client**

* `state` — **volledige** momentopname, de ruggengraat van alles
* `tick` — enkel de timer, elke seconde
* `questions` — de vragenlijst, enkel op aanvraag van de editor
* `event` — `player_joined`, `player_kicked`, `lobby_cleared`, `quiz_started`,
  `question_started`, `answer_received`, `answer_revealed`, `leaderboard_updated`,
  `questions_changed`, `quiz_finished`, `quiz_reset`
* `joined`, `hello`, `error`, `pong`

Die volledige snapshot is bewust de kern: er is geen toestand die de client zelf
moet bijhouden of opnieuw opvragen. Wie binnenkomt — nieuw of na een reconnect —
krijgt in één bericht het hele plaatje. De `event`-berichten dienen enkel om
animaties af te vuren.

Een speler krijgt het juiste antwoord pas te zien in de onthulfase; tijdens de
vraag zit het simpelweg niet in zijn snapshot. Dat geldt ook voor `/admin`: het
"nu op de beamer"-blok toont het antwoord pas nadat het onthuld is.

De vragenlijst zit **niet** in de gewone snapshot. Die gaat bij elke verandering
naar elk scherm, en dertig vragen meesturen op elke tik is zonde van de
bandbreedte van dertig gsm's — de editor vraagt ze apart op.

### Reconnect

Elke speler krijgt een server-side `player_id` (uuid) die in `localStorage` blijft
staan. Bij het opnieuw verbinden stuurt de client die id mee en koppelt de server
hem terug aan de bestaande speler — met score, streak en zijn al gegeven antwoord.
Een dichtgeklapte gsm, een refresh of een korte wifi-dip kost dus niets.

Beamer en bediening werken hetzelfde: de hostcode blijft in `localStorage`, dus na
een refresh sta je meteen terug in de sessie.

### Punten

```
juist + supersnel    1000
juist + rustig aan    500
fout                    0
```

500 basispunten voor een juist antwoord, plus tot 500 snelheidsbonus. Traag maar
juist verslaat dus altijd snel maar fout. Een streak geeft 25 punten per stap,
afgetopt op 100 — leuk, maar nooit beslissend. Bij een dubbele-puntenvraag gaat
alles maal twee.

De snelheidsbonus loopt af over een **apart venster** (`speed_window_seconds`,
standaard 20 seconden), niet over de volledige bedenktijd. Anders zou een vraag
van 90 seconden iedereen die binnen tien seconden antwoordt bijna hetzelfde geven,
en meet de bonus niets meer. Zo kan een vraag ruim open blijven staan zonder dat
er iemand moet haasten.

---

## Vragen aanpassen

### Op `/admin`, tijdens het kamp

Tabblad **Vragen**: klik een vraag open, pas ze aan, klik **Bewaren**. Je kan
vragen toevoegen, dupliceren, verslepen met ↑/↓ en verwijderen. Wat je bewaart
komt in `QUESTIONS_FILE` terecht en overleeft een herstart. **Terug naar de
standaardvragen** gooit alle aanpassingen weg.

Aanpassen kan enkel **vanuit de lobby**. Midden in een quiz zou de nummering
verschuiven onder de voeten van de spelers, dus de server weigert dat.

### In code

De startlijst staat in [`app/quiz/questions.py`](app/quiz/questions.py).

```python
Question(
    id="q31-eigen-vraag",
    category=CAT_KAMP,
    text="Wie heeft de dikste sjorring gemaakt?",
    options=("Ploeg A", "Ploeg B", "Ploeg C", "De leiding"),
    correct_index=0,
    time_limit=90,
    explanation="Kort woordje uitleg dat na de onthulling verschijnt.",
)
```

Ondersteunde types:

* `multiple_choice` — vier antwoorden (standaard)
* `image` — idem, met `image="/static/img/…"` of `visual="🌳🔥"` (emoji werken
  altijd, ook zonder internet)
* `estimate` — speler tikt een getal in; punten naar nauwkeurigheid, met
  `correct_value`, `unit`, `tolerance` en optioneel `max_error`

**Dubbele punten** is geen apart type maar `points_multiplier=2.0`, zodat je het
op eender welk vraagtype kan plakken.

Een eigen afbeelding? Zet ze in `app/static/img/` en verwijs ernaar met
`image="/static/img/jouwbestand.jpg"`.

De vragenlijst wordt bij het opstarten gevalideerd: een vraag met drie antwoorden
of een schattingsvraag zonder waarde laat de app meteen falen in plaats van
halverwege het kamp.

---

## Testen

```bash
.venv/Scripts/python.exe -m unittest discover -s tests
```

61 tests, enkel stdlib. Ze dekken naamopschoning en dubbele namen, reconnect,
de volledige toestandsmachine, tweede antwoorden, gelijktijdige antwoorden, te
late antwoorden, pauzeren, reset, streaks, schattingspunten, spelers verwijderen,
de vrageneditor (inclusief bewaren en terugzetten), het bonusvenster, en de
garantie dat spelers het juiste antwoord niet te vroeg zien.

Voor het echte werk (met sockets, reconnects en 25 spelers) is er
`tools/simulate.py` hierboven.

---

## Projectstructuur

```text
app/
├── main.py              routes + de twee WebSocket-endpoints
├── config.py            alle instellingen, uit environment variables
├── quiz/
│   ├── game.py          toestandsmachine, timer, scores, snapshots
│   ├── questions.py     de startvragen + serialisatie
│   ├── store.py         aangepaste vragen bewaren en terughalen
│   ├── scoring.py       puntenberekening
│   └── hub.py           verbindingen + uitsturen van de toestand
├── templates/           play.html · present.html · admin.html
└── static/
    ├── css/             base · play · present · admin
    ├── js/              ui · net · hostgate · play · present · admin · confetti
    └── img/             favicon + sjorringtekeningen
tests/test_game.py       unit tests
tools/simulate.py        end-to-end test met echte clients
```

---

## Bediening tijdens het kamp

Op `/admin`:

* **de grote knop** (of **spatie**) — volgende stap: starten, onthullen,
  tussenstand, volgende vraag
* **P** — pauzeren en verder gaan
* **Spelers** — iedereen in een lijst, met wie verbonden is en een ✕ om iemand
  te verwijderen. **Afwezigen verwijderen** haalt alle niet-verbonden spelers
  eruit, **Lobby leegmaken** maakt volledig schoon. Allebei enkel in de lobby:
  tijdens een vraag telt een gsm die even op slot ging ook als "niet verbonden".
* **Vragen** — de editor, zie hierboven
* **Instellingen** — hands-free timings en de links naar de andere schermen
* **↻ Opnieuw** — scores op nul, spelers blijven staan, terug naar de lobby
* **Afsluiten** — meteen naar de eindstand

Spatie en P werken ook op `/present`, handig als je met één laptop staat.

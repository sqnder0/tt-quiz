"""De vragen van de Boomhutten Quiz.

Dit bestand is de *startlijst*. Wat de leiding op /admin aanpast wordt bewaard in
`config.QUESTIONS_FILE` en heeft daarna voorrang -- zie `app/quiz/store.py`.
Wie liever in code werkt past `QUESTIONS` hieronder aan en zet de bewaarde lijst
terug op de standaard via de knop "Terugzetten naar de standaardvragen".

Vraagtypes
----------
`multiple_choice`  vier antwoorden, één juist
`image`            idem, maar met een afbeelding erbij (`image=` of `visual=`)
`estimate`         speler tikt een getal in, punten naar nauwkeurigheid

"Dubbele punten" is bewust géén apart type maar een modifier
(`points_multiplier=2.0`), zodat je hem op eender welk vraagtype kan plakken.
Een nieuw type toevoegen = een waarde bij `QuestionType`, een tak in
`Game._grade_answer()` en een blokje in de frontend.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional, Sequence

from .. import config

QuestionType = Literal["multiple_choice", "image", "estimate"]

OPTION_KEYS = ("A", "B", "C", "D")

# Categorieën — puur cosmetisch, tonen als badge op de vraag.
CAT_BOOMHUT = "🌳 Boomhutten"
CAT_HOUT = "🪵 Hout & gereedschap"
CAT_NATUUR = "🌲 Natuur"
CAT_DIEREN = "🐾 Dieren"
CAT_KAMP = "🏕️ Kamp"
CAT_BUITEN = "🔥 Buitenleven"
CAT_KENNIS = "🧠 Algemene kennis"
CAT_FUN = "😂 Kamp & fun"
CAT_FINALE = "🏆 Finale"


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    text: str
    type: QuestionType = "multiple_choice"

    # --- meerkeuze / afbeelding ---
    options: Sequence[str] = field(default_factory=tuple)
    correct_index: int = 0

    # --- schatting ---
    correct_value: Optional[float] = None
    unit: str = ""
    tolerance: float = 0.0
    max_error: Optional[float] = None
    """Vanaf deze afwijking krijg je 0 punten. Default: 6x de tolerantie."""

    # --- algemeen ---
    time_limit: int = config.DEFAULT_TIME_LIMIT
    points_multiplier: float = 1.0
    image: Optional[str] = None
    """Pad onder /static, bv. "/static/img/sjorring-driepoot.svg"."""
    visual: Optional[str] = None
    """Alternatief voor `image`: een paar grote emoji. Werkt altijd, ook offline."""
    explanation: Optional[str] = None

    def __post_init__(self) -> None:
        if self.type in ("multiple_choice", "image"):
            if len(self.options) != 4:
                raise ValueError(f"Vraag {self.id!r}: verwacht precies 4 antwoorden, kreeg {len(self.options)}")
            if not 0 <= self.correct_index < 4:
                raise ValueError(f"Vraag {self.id!r}: correct_index moet 0..3 zijn")
        elif self.type == "estimate":
            if self.correct_value is None:
                raise ValueError(f"Vraag {self.id!r}: schattingsvraag heeft correct_value nodig")
            if self.tolerance <= 0:
                raise ValueError(f"Vraag {self.id!r}: schattingsvraag heeft een tolerantie > 0 nodig")
        else:  # pragma: no cover - beschermt tegen typo's in de lijst
            raise ValueError(f"Vraag {self.id!r}: onbekend type {self.type!r}")
        # Enkel het absolute minimum hier: de grenzen uit config zijn een
        # editorregel (zie from_dict), geen eigenschap van het datamodel.
        if self.time_limit <= 0:
            raise ValueError(f"Vraag {self.id!r}: time_limit moet positief zijn")

    @property
    def is_double(self) -> bool:
        return self.points_multiplier >= 2.0

    @property
    def effective_max_error(self) -> float:
        if self.max_error is not None:
            return self.max_error
        return self.tolerance * 6.0

    @property
    def correct_answer_text(self) -> str:
        if self.type == "estimate":
            value = self.correct_value
            assert value is not None
            pretty = f"{value:g}"
            return f"{pretty} {self.unit}".strip()
        return self.options[self.correct_index]


# ---------------------------------------------------------------------------
# De quiz. Volgorde = speelvolgorde. Bewust afwisselend qua categorie en tempo.
# ---------------------------------------------------------------------------

QUESTIONS: tuple[Question, ...] = (
    Question(
        id="q01-regen",
        category=CAT_FUN,
        text="Wat gebeurt er op kamp gegarandeerd zodra je je tent perfect hebt opgezet?",
        options=(
            "Het begint te regenen",
            "De zon breekt door",
            "Er komt een kudde koeien langs",
            "De leiding roept 'corvee!'",
        ),
        correct_index=0,
        time_limit=90,
        visual="⛺🌧️",
        explanation="Wet van het kamp. Daarom zit je nu ook binnen aan deze quiz.",
    ),
    Question(
        id="q02-bever",
        category=CAT_DIEREN,
        text="Welk dier bouwt zelf dammen en hutten van hout?",
        options=("De bever", "De das", "De vos", "De eekhoorn"),
        correct_index=0,
        time_limit=90,
        visual="🦫",
        explanation="De bever is de enige echte concurrent van onze boomhuttenploeg.",
    ),
    Question(
        id="q03-mastworp",
        category=CAT_HOUT,
        text="Met welke knoop begin je een sjorring aan een paal?",
        options=("Mastworp", "Platte knoop", "Schootsteek", "Achtknoop"),
        correct_index=0,
        time_limit=90,
        explanation=(
            "De mastworp (klemknoop rond de paal) is de klassieke start. "
            "Een timmersteek mag ook, maar die zat niet tussen de antwoorden."
        ),
    ),
    Question(
        id="q04-jaarringen",
        category=CAT_NATUUR,
        text="Waaraan lees je af hoe oud een omgezaagde boom was?",
        options=("Aan de jaarringen", "Aan de dikte van de bast", "Aan het aantal takken", "Aan de kleur van het hout"),
        correct_index=0,
        time_limit=90,
        explanation="Eén ring per jaar: brede ringen = goed jaar, smalle ringen = droog of donker jaar.",
    ),
    Question(
        id="q05-platte-sjorring",
        category=CAT_HOUT,
        text="Hoe heet de sjorring waarmee je twee palen verbindt die haaks op elkaar liggen?",
        options=("Platte sjorring", "Kruissjorring", "Driepootsjorring", "Schoorsjorring"),
        correct_index=0,
        time_limit=90,
        explanation=(
            "Platte sjorring = 90°. Een kruissjorring gebruik je net als de palen schuin kruisen "
            "en tegen elkaar weg willen glijden."
        ),
    ),
    Question(
        id="q06-teekpoten",
        category=CAT_DIEREN,
        text="Hoeveel pootjes heeft een teek?",
        options=("8", "6", "4", "10"),
        correct_index=0,
        time_limit=90,
        visual="🕷️",
        explanation="Een teek is geen insect maar een spinachtige. Vandaar 8 poten.",
    ),
    Question(
        id="q07-driepoot",
        category=CAT_HOUT,
        type="image",
        text="Welke sjorring zie je hier?",
        options=("Driepootsjorring", "Platte sjorring", "Kruissjorring", "Achtknoop"),
        correct_index=0,
        time_limit=90,
        image="/static/img/sjorring-driepoot.svg",
        explanation="Drie palen naast elkaar sjorren en dan openspreiden: de basis van elke uitkijktoren.",
    ),
    Question(
        id="q08-vuur-starten",
        category=CAT_BUITEN,
        text="Waarmee start je een kampvuur?",
        options=(
            "Dun, kurkdroog sprokkelhout en tondel",
            "Meteen een dikke boomstam",
            "Vers, groen hout uit de haag",
            "Een scheut benzine",
        ),
        correct_index=0,
        time_limit=90,
        visual="🔥",
        explanation="Klein beginnen en opbouwen. Brandbare vloeistoffen: nooit. Nooit.",
    ),
    Question(
        id="q09-twee-bomen",
        category=CAT_BOOMHUT,
        text="Waarom zet je de vloer van een boomhut niet stijf vast aan twee verschillende bomen?",
        options=(
            "Omdat bomen apart van elkaar bewegen in de wind",
            "Omdat het te veel touw kost",
            "Omdat de vloer dan scheef hangt",
            "Omdat er dan te weinig schaduw is",
        ),
        correct_index=0,
        time_limit=90,
        explanation=(
            "Twee bomen wiegen elk hun eigen kant op. Een starre verbinding wringt zichzelf kapot — "
            "daarom werk je met één draagboom of met bewegende ophangpunten."
        ),
    ),
    Question(
        id="q10-esdoorn",
        category=CAT_NATUUR,
        text="Van welke boom vallen de 'helikoptertjes' die ronddraaiend naar beneden zweven?",
        options=("De esdoorn", "De eik", "De berk", "De wilg"),
        correct_index=0,
        time_limit=90,
        visual="🍁",
        explanation="Gevleugelde zaadjes. Je plakte ze vroeger op je neus.",
    ),
    Question(
        id="q11-bijl",
        category=CAT_HOUT,
        text="Hoe geef je een bijl veilig door aan iemand anders?",
        options=(
            "Je houdt de kop vast en geeft het handvat vooruit",
            "Je gooit ze voorzichtig",
            "Je geeft ze met de scherpe kant vooruit",
            "Je legt ze op de grond en wijst ernaar",
        ),
        correct_index=0,
        time_limit=90,
        visual="🪓",
        explanation="De ander pakt het handvat, jij laat pas los als hij 'ik heb ze' zegt.",
    ),
    Question(
        id="q12-platte-knoop",
        category=CAT_KAMP,
        text="Welke knoop gebruik je om twee touwen van dezelfde dikte aan elkaar te knopen?",
        options=("Platte knoop", "Schootsteek", "Paalsteek", "Mastworp"),
        correct_index=0,
        time_limit=90,
        explanation="Links over rechts, dan rechts over links. Doe je het twee keer hetzelfde, dan krijg je een oma-knoop die schuift.",
    ),
    Question(
        id="q13-vliegenzwam",
        category=CAT_NATUUR,
        text="Rode hoed met witte stippen: hoe heet die paddenstoel, en wat doe je ermee?",
        options=(
            "Vliegenzwam — giftig, laten staan",
            "Champignon — lekker in de soep",
            "Cantharel — bakken in de boter",
            "Truffel — verkopen aan de kok",
        ),
        correct_index=0,
        time_limit=90,
        visual="🍄",
        explanation="Mooi om naar te kijken, giftig om te eten. Handen wassen na het aanraken.",
    ),
    Question(
        id="q14-schoor",
        category=CAT_BOOMHUT,
        type="image",
        text="Dit vierkante frame zakt scheef. Wat voeg je toe zodat het stevig blijft staan?",
        options=(
            "Een schuine schoorbalk (diagonaal)",
            "Een extra horizontale balk",
            "Dikkere palen",
            "Nog een laag touw",
        ),
        correct_index=0,
        time_limit=90,
        image="/static/img/schoor-driehoek.svg",
        explanation=(
            "Een vierkant kan vervormen, een driehoek niet. Eén diagonaal maakt van je frame twee "
            "driehoeken — dát is waarom elke degelijke toren schoren heeft."
        ),
    ),
    Question(
        id="q15-touw-schatting",
        category=CAT_HOUT,
        type="estimate",
        text="Hoeveel meter sjortouw heb je ongeveer nodig voor één degelijke platte sjorring?",
        correct_value=4,
        unit="meter",
        tolerance=1,
        max_error=4,
        time_limit=90,
        explanation=(
            "Vuistregel: zo'n 4 meter voor gewone kamppalen. Windingen én woelingen moeten erop passen, "
            "dus liever iets te lang dan te kort."
        ),
    ),
    Question(
        id="q16-teek-verwijderen",
        category=CAT_DIEREN,
        text="Je vindt 's avonds een teek op je been. Wat doe je?",
        options=(
            "Met een tekentang recht en in één beweging uittrekken",
            "Er alcohol op gieten en wachten",
            "Ze eerst verdoven met een aansteker",
            "Laten zitten tot ze vanzelf loslaat",
        ),
        correct_index=0,
        time_limit=90,
        explanation=(
            "Zo dicht mogelijk bij de huid vastnemen en recht uittrekken. Nooit branden of insmeren: "
            "dan braakt de teek net terug. Ontsmet nadien en noteer de datum."
        ),
    ),
    Question(
        id="q17-nat-hout",
        category=CAT_BUITEN,
        text="Waarom gooi je geen vers, groen hout op het kampvuur?",
        options=(
            "Het rookt enorm en brandt bijna niet",
            "Het brandt veel te heet",
            "Het maakt het vuur blauw",
            "Het is te zwaar om te dragen",
        ),
        correct_index=0,
        time_limit=90,
        explanation="Vers hout zit vol vocht. Dat moet eerst verdampen — vandaar de rookgordijnen en de tranen.",
    ),
    Question(
        id="q18-kruissjorring",
        category=CAT_HOUT,
        type="image",
        text="En welke sjorring is dit?",
        options=("Kruissjorring", "Platte sjorring", "Driepootsjorring", "Mastworp"),
        correct_index=0,
        time_limit=90,
        image="/static/img/sjorring-kruis.svg",
        explanation="Palen die schuin kruisen en tegen elkaar weg duwen: dan sjor je diagonaal, over de kruising heen.",
    ),
    Question(
        id="q19-tentplek",
        category=CAT_KAMP,
        text="Het gaat hard regenen. Waar zet je je tent het best?",
        options=(
            "Op een lichte verhoging, niet in een kuil",
            "Onderaan de helling, uit de wind",
            "In de laagste kuil van het veld",
            "Pal onder een grote dode tak",
        ),
        correct_index=0,
        time_limit=90,
        explanation="Water zoekt het laagste punt. En een dode tak boven je tent heet niet voor niets een 'widowmaker'.",
    ),
    Question(
        id="q20-lork",
        category=CAT_NATUUR,
        text="Welke naaldboom verliest in de winter al zijn naalden?",
        options=("De lork (lariks)", "De spar", "De grove den", "De taxus"),
        correct_index=0,
        time_limit=90,
        explanation="De lork is onze enige inheemse naaldboom die kaal de winter in gaat. Vandaar dat goudgele bos in november.",
    ),
    Question(
        id="q21-paalsteek",
        category=CAT_KAMP,
        text="Welke knoop maakt een lus die niet dichttrekt, hoe hard je er ook aan sleurt?",
        options=("De paalsteek", "De schuifknoop", "De mastworp", "De platte knoop"),
        correct_index=0,
        time_limit=90,
        explanation="Het konijn komt uit het hol, rond de boom, en terug het hol in. De reddingsknoop bij uitstek.",
    ),
    Question(
        id="q22-grove-den",
        category=CAT_NATUUR,
        text="Welke boomsoort komt het meest voor in de Vlaamse bossen?",
        options=("De grove den", "De beuk", "De berk", "De olijfboom"),
        correct_index=0,
        time_limit=90,
        explanation=(
            "Volgens de Vlaamse Bosinventaris staat de grove den op één, vooral door de grote "
            "dennenbossen in de Kempen. De zomereik volgt kort daarachter."
        ),
    ),
    Question(
        id="q23-emmer",
        category=CAT_KAMP,
        type="estimate",
        text="Hoeveel kilogram weegt een volle emmer van 10 liter water?",
        correct_value=10,
        unit="kg",
        tolerance=1,
        max_error=6,
        time_limit=90,
        visual="🪣",
        explanation="Eén liter water = één kilogram. Vandaar dat waterkorvee altijd zo lang duurt.",
    ),
    Question(
        id="q24-mos",
        category=CAT_NATUUR,
        text="Aan welke kant van een boomstam groeit bij ons meestal het meeste mos?",
        options=("De noordkant", "De zuidkant", "De oostkant", "Overal even veel"),
        correct_index=0,
        time_limit=90,
        visual="🧭",
        explanation=(
            "De noordkant krijgt het minste zon en blijft dus vochtiger. Handig als ruwe kompascheck, "
            "maar niet waterdicht: wind en regen spelen ook mee."
        ),
    ),
    Question(
        id="q25-canberra",
        category=CAT_KENNIS,
        text="Wat is de hoofdstad van Australië?",
        options=("Canberra", "Sydney", "Melbourne", "Perth"),
        correct_index=0,
        time_limit=90,
        visual="🇦🇺",
        explanation="Sydney en Melbourne konden het niet eens worden, dus bouwden ze er een hoofdstad tussenin.",
    ),
    Question(
        id="q26-vuur-blussen",
        category=CAT_BUITEN,
        text="Het kamp gaat slapen. Hoe blus je het vuur echt veilig?",
        options=(
            "Water erover, omroeren, tot alles koud aanvoelt",
            "Zand erover en meteen gaan slapen",
            "Laten uitdoven, dat gaat vanzelf",
            "Er een emmer aarde op en de rest morgen",
        ),
        correct_index=0,
        time_limit=90,
        explanation="Onder de as smeult het uren door. Water, roeren, nog eens water — tot je je hand erboven kan houden.",
    ),
    Question(
        id="q27-das",
        category=CAT_DIEREN,
        text="Welk dier met een zwart-wit gestreepte kop woont in een ondergrondse burcht in onze bossen?",
        options=("De das", "De marter", "De egel", "De bunzing"),
        correct_index=0,
        time_limit=90,
        explanation="Dassenburchten worden generaties lang doorgegeven — sommige zijn ouder dan je grootouders.",
    ),
    Question(
        id="q28-slaapzak",
        category=CAT_FUN,
        text="Wat zit er op de laatste kampdag gegarandeerd in je slaapzak?",
        options=("Zand", "Een propere sok", "Je verloren zaklamp", "Niets, hij is smetteloos"),
        correct_index=0,
        time_limit=90,
        visual="😴",
        explanation="Het zand van dit kamp vind je thuis nog terug met Kerstmis.",
    ),
    Question(
        id="q29-schootsteek",
        category=CAT_FINALE,
        text="DUBBELE PUNTEN — Welke knoop verbindt twee touwen van ONGELIJKE dikte?",
        options=("De schootsteek", "De platte knoop", "De paalsteek", "De mastworp"),
        correct_index=0,
        time_limit=90,
        points_multiplier=2.0,
        explanation=(
            "Een platte knoop schiet los zodra de touwen verschillend dik zijn. De schootsteek klemt "
            "het dunne touw net vast rond de lus van het dikke."
        ),
    ),
    Question(
        id="q30-woelingen",
        category=CAT_FINALE,
        text="DUBBELE PUNTEN — Je hebt de windingen van je platte sjorring gelegd. Wat doe je daarna?",
        options=(
            "Woelingen maken tussen de palen en afwerken met een mastworp",
            "Meteen afknopen met een platte knoop",
            "De palen nog een kwartslag draaien",
            "Het touw natmaken zodat het krimpt",
        ),
        correct_index=0,
        time_limit=90,
        points_multiplier=2.0,
        explanation=(
            "De woelingen (windingen tússen de palen door) trekken alles muurvast. Zonder woelingen "
            "blijft je sjorring altijd los aanvoelen, hoe hard je ook getrokken hebt."
        ),
    ),
)


def question_count() -> int:
    return len(QUESTIONS)


def validate_questions(questions: Sequence[Question] = QUESTIONS) -> None:
    """Faalt luid bij het opstarten als er iets mis is met de vragenlijst."""
    if not questions:
        raise ValueError("De quiz bevat geen vragen.")
    seen: set[str] = set()
    for q in questions:
        if q.id in seen:
            raise ValueError(f"Dubbele vraag-id: {q.id!r}")
        seen.add(q.id)


# ---------------------------------------------------------------------------
# Serialisatie — voor de vrageneditor op /admin en het bewaarbestand
# ---------------------------------------------------------------------------

CATEGORIES: tuple[str, ...] = (
    CAT_BOOMHUT, CAT_HOUT, CAT_NATUUR, CAT_DIEREN,
    CAT_KAMP, CAT_BUITEN, CAT_KENNIS, CAT_FUN, CAT_FINALE,
)


def to_dict(question: Question) -> dict[str, Any]:
    data = asdict(question)
    data["options"] = list(question.options)
    return data


def _as_int(value: Any, field_name: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} moet een getal zijn.")


def _as_float(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} moet een getal zijn.")
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{field_name} moet een gewoon getal zijn.")
    return number


def _clean(value: Any) -> Optional[str]:
    """Lege tekstvelden uit de editor komen binnen als "" en moeten None worden."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def from_dict(data: dict[str, Any]) -> Question:
    """Bouw een Question uit editor- of bestandsinvoer.

    Alles wordt hier gecoerceerd en gecontroleerd, zodat een typfout in de editor
    een nette foutmelding oplevert in plaats van een halfstuk vraag op de beamer.
    """
    if not isinstance(data, dict):
        raise ValueError("Een vraag moet een object zijn.")

    qid = str(data.get("id") or "").strip()
    if not qid:
        raise ValueError("Een vraag heeft een id nodig.")

    qtype = str(data.get("type") or "multiple_choice").strip()
    if qtype not in ("multiple_choice", "image", "estimate"):
        raise ValueError(f"Onbekend vraagtype: {qtype!r}")

    text = str(data.get("text") or "").strip()
    if not text:
        raise ValueError("Een vraag heeft een vraagtekst nodig.")

    options = tuple(str(o).strip() for o in (data.get("options") or ()))
    if qtype != "estimate":
        if len(options) != 4 or any(not o for o in options):
            raise ValueError("Vul alle vier de antwoorden in.")

    raw_multiplier = data.get("points_multiplier", 1.0)
    multiplier = _as_float(raw_multiplier, "De puntenvermenigvuldiger")
    if not 0.5 <= multiplier <= 5.0:
        raise ValueError("De puntenvermenigvuldiger moet tussen 0,5 en 5 liggen.")

    time_limit = _as_int(data.get("time_limit", config.DEFAULT_TIME_LIMIT), "De bedenktijd")
    time_limit = max(config.MIN_TIME_LIMIT, min(config.MAX_TIME_LIMIT, time_limit))

    kwargs: dict[str, Any] = {
        "id": qid,
        "category": str(data.get("category") or CAT_KAMP).strip() or CAT_KAMP,
        "text": text,
        "type": qtype,  # type: ignore[arg-type]
        "time_limit": time_limit,
        "points_multiplier": multiplier,
        "image": _clean(data.get("image")),
        "visual": _clean(data.get("visual")),
        "explanation": _clean(data.get("explanation")),
    }

    if qtype == "estimate":
        kwargs["correct_value"] = _as_float(data.get("correct_value"), "De juiste waarde")
        tolerance = _as_float(data.get("tolerance", 0) or 0, "De tolerantie")
        if tolerance <= 0:
            raise ValueError("Een schattingsvraag heeft een tolerantie groter dan 0 nodig.")
        kwargs["tolerance"] = tolerance
        kwargs["unit"] = str(data.get("unit") or "").strip()
        max_error = data.get("max_error")
        if max_error not in (None, ""):
            value = _as_float(max_error, "De maximale afwijking")
            if value <= tolerance:
                raise ValueError("De maximale afwijking moet groter zijn dan de tolerantie.")
            kwargs["max_error"] = value
    else:
        correct_index = _as_int(data.get("correct_index", 0), "Het juiste antwoord")
        if not 0 <= correct_index < 4:
            raise ValueError("Duid aan welk van de vier antwoorden juist is.")
        kwargs["options"] = options
        kwargs["correct_index"] = correct_index

    return Question(**kwargs)


validate_questions()

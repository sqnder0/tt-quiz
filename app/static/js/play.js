/* Deelnemersscherm.
   De server stuurt bij elke verandering een volledige snapshot; deze file doet
   niets anders dan die snapshot tekenen. Geen eigen spellogica, geen eigen timer
   die punten bepaalt -- enkel de countdown vloeiend laten aflopen tussen de ticks. */

(function () {
  "use strict";

  var STORE_TOKEN = "jhquiz.reconnect_token";
  var STORE_ID = "jhquiz.player_id";
  var STORE_NAME = "jhquiz.name";
  var OPT_CLASS = ["opt-a", "opt-b", "opt-c", "opt-d"];

  var $ = QuizUI.$;
  var fmt = QuizUI.fmt;
  var toast = QuizUI.toast;

  var el = {
    conn: $("conn"),
    connText: $("connText"),
    screens: {
      join: $("screen-join"),
      lobby: $("screen-lobby"),
      question: $("screen-question"),
      waiting: $("screen-waiting"),
      result: $("screen-result"),
      standing: $("screen-standing"),
      done: $("screen-done"),
    },
    joinForm: $("joinForm"),
    nameInput: $("nameInput"),
    joinButton: $("joinButton"),
    joinError: $("joinError"),
    joinTitle: $("joinTitle"),
    joinSub: $("joinSub"),
    lobbyName: $("lobbyName"),
    lobbyCount: $("lobbyCount"),
    lobbyCountLabel: $("lobbyCountLabel"),
    qNumber: $("qNumber"),
    qCategory: $("qCategory"),
    qDouble: $("qDouble"),
    qTimer: $("qTimer"),
    qImage: $("qImage"),
    qVisual: $("qVisual"),
    qText: $("qText"),
    answers: $("answers"),
    estimate: $("estimate"),
    estimateUnit: $("estimateUnit"),
    estimateInput: $("estimateInput"),
    estimateSubmit: $("estimateSubmit"),
    estimateMinus: $("estimateMinus"),
    estimatePlus: $("estimatePlus"),
    waitingChosen: $("waitingChosen"),
    waitingSub: $("waitingSub"),
    resultIcon: $("resultIcon"),
    resultTitle: $("resultTitle"),
    resultPoints: $("resultPoints"),
    resultCorrect: $("resultCorrect"),
    resultExplain: $("resultExplain"),
    resultMeta: $("resultMeta"),
    standRank: $("standRank"),
    standOf: $("standOf"),
    standScore: $("standScore"),
    standDelta: $("standDelta"),
    standBoard: $("standBoard"),
    doneMedal: $("doneMedal"),
    doneTitle: $("doneTitle"),
    doneRank: $("doneRank"),
    doneOf: $("doneOf"),
    doneScore: $("doneScore"),
    doneStats: $("doneStats"),
    doneBoard: $("doneBoard"),
  };

  var state = null;
  var playerId = null;
  var joining = false;
  var removed = false;
  var lastQuestionId = null;
  var pendingChoice = null;
  var timer = { remaining: 0, total: 0, running: false, at: 0 };
  var socket;

  // --- opslag (localStorage kan geblokkeerd zijn in privémodus) ------------

  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch (err) {
      /* geen opslag beschikbaar: dan maar zonder reconnect-id */
    }
    return null;
  }

  function show(name) {
    Object.keys(el.screens).forEach(function (key) {
      el.screens[key].hidden = key !== name;
    });
  }

  // --- verbinding ---------------------------------------------------------

  socket = new QuizSocket({
    path: "/ws/play",
    onStatus: QuizUI.statusBinder(el.conn, el.connText),
    onOpen: function () {
      var savedToken = store(STORE_TOKEN);
      var savedId = store(STORE_ID);
      var savedName = store(STORE_NAME);
      if (savedToken || savedId || savedName) {
        joining = true;
        socket.send({
          t: "join",
          name: savedName || "",
          reconnect_token: savedToken || null,
          player_id: savedToken ? null : savedId || null,
        });
      }
    },
    onMessage: handle,
  });
  socket.connect();

  function handle(msg) {
    if (msg.t === "state") {
      state = msg;
      render();
    } else if (msg.t === "tick") {
      applyTimer(msg.timer);
    } else if (msg.t === "joined") {
      joining = false;
      removed = false;
      playerId = msg.player_id;
      store(STORE_TOKEN, msg.reconnect_token);
      store(STORE_ID, msg.player_id);
      store(STORE_NAME, msg.name);
      el.nameInput.value = msg.name;
      // De server stuurt direct na `joined` een snapshot met `you`. De eerdere
      // snapshot hoorde nog bij de anonieme verbinding en mag niet tekenen.
    } else if (msg.t === "error") {
      onError(msg);
    } else if (msg.t === "event") {
      onEvent(msg);
    }
  }

  function onError(msg) {
    if (msg.code === "join_failed") {
      joining = false;
      el.joinError.textContent = msg.message;
      el.joinButton.disabled = false;
      show("join");
      return;
    }
    if (msg.code === "not_joined") {
      store(STORE_TOKEN, null);
      store(STORE_ID, null);
      playerId = null;
      show("join");
      return;
    }
    if (msg.code === "answer_rejected") {
      pendingChoice = null;
      if (el.answers) el.answers.dataset.locked = "false";
      el.estimateSubmit.disabled = false;
    }
    toast(msg.message || "Er ging iets mis.", "bad");
  }

  function onEvent(msg) {
    if (msg.name === "player_kicked" && msg.data && msg.data.id === playerId) {
      removed = true;
    }
    if (msg.name === "lobby_cleared") {
      removed = true;
    }
    if (msg.name === "quiz_reset") {
      lastQuestionId = null;
      pendingChoice = null;
      toast("De quiz start opnieuw!", "ok");
    }
    if (msg.name === "question_started" && navigator.vibrate) {
      navigator.vibrate(40);
    }
  }

  // --- naam ingeven -------------------------------------------------------

  el.joinForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var name = el.nameInput.value.trim();
    if (!name) {
      el.joinError.textContent = "Vul eerst je naam in.";
      return;
    }
    el.joinError.textContent = "";
    el.joinButton.disabled = true;
    joining = true;
    store(STORE_NAME, name);
    var savedToken = store(STORE_TOKEN);
    if (
      !socket.send({
        t: "join",
        name: name,
        reconnect_token: savedToken,
        player_id: savedToken ? null : store(STORE_ID),
      })
    ) {
      el.joinButton.disabled = false;
      joining = false;
      el.joinError.textContent =
        "Even geen verbinding. Probeer het zo nog eens.";
    }
    window.setTimeout(function () {
      el.joinButton.disabled = false;
    }, 2500);
  });

  // --- antwoorden ---------------------------------------------------------

  function submitChoice(index) {
    if (pendingChoice !== null) return;
    pendingChoice = index;
    el.answers.dataset.locked = "true";
    var buttons = el.answers.querySelectorAll(".answer");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].classList.toggle("is-chosen", i === index);
    }
    if (navigator.vibrate) navigator.vibrate(25);
    socket.send({ t: "submit_answer", choice: index });
  }

  function submitEstimate() {
    var raw = el.estimateInput.value.replace(",", ".").trim();
    if (raw === "" || isNaN(parseFloat(raw))) {
      toast("Geef eerst een getal in.", "bad");
      return;
    }
    el.estimateSubmit.disabled = true;
    socket.send({ t: "submit_answer", value: parseFloat(raw) });
  }

  el.estimateSubmit.addEventListener("click", submitEstimate);
  el.estimateInput.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      event.preventDefault();
      submitEstimate();
    }
  });
  el.estimateMinus.addEventListener("click", function () {
    stepEstimate(-1);
  });
  el.estimatePlus.addEventListener("click", function () {
    stepEstimate(1);
  });

  function stepEstimate(direction) {
    var current = parseFloat(el.estimateInput.value.replace(",", "."));
    if (isNaN(current)) current = 0;
    el.estimateInput.value = String(Math.max(0, current + direction));
  }

  // --- timer --------------------------------------------------------------

  function applyTimer(payload) {
    if (!payload) {
      timer.running = false;
      return;
    }
    timer.remaining = payload.remaining_ms;
    timer.total = payload.total_ms;
    timer.running = payload.running;
    timer.at =
      window.performance && performance.now ? performance.now() : Date.now();
    paintTimer();
  }

  function paintTimer() {
    if (el.screens.question.hidden) return;
    var now =
      window.performance && performance.now ? performance.now() : Date.now();
    var left = timer.remaining - (timer.running ? now - timer.at : 0);
    var seconds = Math.max(0, Math.ceil(left / 1000));
    el.qTimer.textContent = timer.running ? seconds : "⏸";
    el.qTimer.classList.toggle("is-low", timer.running && seconds <= 5);
  }

  (function loop() {
    paintTimer();
    window.requestAnimationFrame(loop);
  })();

  // --- tekenen ------------------------------------------------------------

  function render() {
    if (!state) return;
    if (state.quiz) {
      el.joinTitle.textContent = state.quiz.title;
      el.joinSub.textContent = state.quiz.subtitle;
      document.title = state.quiz.title;
    }

    var you = state.you;
    if (!you) {
      // Een snapshot zonder `you` kan tijdens een reconnect verouderd zijn.
      // Alleen een expliciet verwijder-event mag de reconnect-gegevens wissen.
      if (removed && playerId) {
        store(STORE_TOKEN, null);
        store(STORE_ID, null);
        playerId = null;
        removed = false;
        toast("De leiding heeft je uit de quiz gehaald.", "bad");
        show("join");
      } else if (!playerId && !joining) {
        show("join");
      }
      return;
    }
    playerId = you.id;
    applyTimer(state.timer);

    switch (state.phase) {
      case "LOBBY":
        renderLobby(you);
        break;
      case "QUESTION":
        renderQuestion(you);
        break;
      case "ANSWER_REVEAL":
        renderResult(you);
        break;
      case "LEADERBOARD":
        renderStanding(you);
        break;
      case "FINISHED":
        renderDone(you);
        break;
      default:
        show("lobby");
    }
  }

  function renderLobby(you) {
    el.lobbyName.textContent = you.name;
    var count = state.counts.players;
    el.lobbyCount.textContent = count;
    el.lobbyCountLabel.textContent =
      count === 1 ? "speler doet mee" : "spelers doen mee";
    lastQuestionId = null;
    pendingChoice = null;
    show("lobby");
  }

  function renderQuestion(you) {
    var q = state.question;
    if (!q) return;

    if (q.id !== lastQuestionId) {
      lastQuestionId = q.id;
      pendingChoice = null;
      el.estimateInput.value = "";
      el.estimateSubmit.disabled = false;
      buildQuestion(q);
    }

    if (you.answered) {
      pendingChoice = you.choice;
      renderWaiting(you, q);
      return;
    }

    el.answers.dataset.locked = pendingChoice !== null ? "true" : "false";
    show("question");
    paintTimer();
  }

  function buildQuestion(q) {
    el.qNumber.textContent = "Vraag " + q.number + "/" + q.total;
    el.qCategory.textContent = q.category;
    el.qDouble.hidden = !q.double;
    el.qText.textContent = q.text;

    el.qImage.hidden = !q.image;
    if (q.image) el.qImage.src = q.image;
    el.qVisual.hidden = !q.visual;
    el.qVisual.textContent = q.visual || "";

    var isEstimate = q.type === "estimate";
    el.estimate.hidden = !isEstimate;
    el.answers.hidden = isEstimate;
    el.estimateUnit.textContent = isEstimate
      ? "Jouw schatting in " + (q.unit || "eenheden")
      : "";

    el.answers.innerHTML = "";
    if (isEstimate) return;

    (q.options || []).forEach(function (option, index) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "answer " + OPT_CLASS[index];
      button.innerHTML =
        '<span class="answer__key"><span class="shape"></span><span></span></span>';
      button.querySelector(".answer__key span:last-child").textContent =
        option.key;
      var text = document.createElement("span");
      text.textContent = option.text;
      button.appendChild(text);
      button.addEventListener("click", function () {
        submitChoice(index);
      });
      el.answers.appendChild(button);
    });
  }

  function renderWaiting(you, q) {
    var chosen = el.waitingChosen;
    if (q.type === "estimate") {
      chosen.className = "waiting__chosen";
      chosen.textContent =
        "Jouw schatting: " + you.value + " " + (q.unit || "");
    } else {
      var option = (q.options || [])[you.choice];
      chosen.className = "waiting__chosen " + (OPT_CLASS[you.choice] || "");
      chosen.textContent = option
        ? option.key + " — " + option.text
        : "Antwoord verstuurd";
    }
    var answers = state.answers || {};
    el.waitingSub.textContent = answers.expected
      ? answers.received +
        " van de " +
        answers.expected +
        (answers.received === 1
          ? " spelers heeft geantwoord"
          : " spelers hebben geantwoord")
      : "Wacht op het volgende scherm…";
    show("waiting");
  }

  function renderResult(you) {
    var q = state.question || {};
    var reveal = state.reveal || {};
    var correct = you.last_correct;
    var points = you.last_points || 0;

    el.screens.result.className =
      "screen result " + (correct ? "result--ok" : "result--bad");
    if (correct) {
      el.resultIcon.textContent = points >= 900 ? "🚀" : "✅";
      el.resultTitle.textContent =
        you.streak >= 3 ? "Juist! " + you.streak + " op rij!" : "Juist!";
    } else if (you.last_correct === null && !you.answered) {
      el.resultIcon.textContent = "😴";
      el.resultTitle.textContent = "Geen antwoord";
    } else {
      el.resultIcon.textContent = "❌";
      el.resultTitle.textContent = "Helaas!";
    }

    el.resultPoints.textContent = points > 0 ? "+" + fmt(points) : "+0";
    el.resultCorrect.innerHTML = "";
    var label = document.createElement("span");
    label.textContent = "Juiste antwoord: ";
    var answer = document.createElement("b");
    answer.textContent = reveal.correct_text || q.correct_text || "";
    el.resultCorrect.appendChild(label);
    el.resultCorrect.appendChild(answer);

    el.resultExplain.textContent = reveal.explanation || "";
    el.resultExplain.hidden = !reveal.explanation;

    el.resultMeta.innerHTML = "";
    addPill(el.resultMeta, "Totaal: " + fmt(you.score));
    addPill(el.resultMeta, "Plaats " + you.rank + "/" + you.total_players);
    if (you.streak >= 2) addPill(el.resultMeta, "🔥 " + you.streak + " op rij");
    if (
      q.type === "estimate" &&
      reveal.correct_value !== undefined &&
      you.value !== null
    ) {
      addPill(el.resultMeta, "Jij: " + you.value + " " + (reveal.unit || ""));
    }
    show("result");
  }

  function addPill(parent, text) {
    var pill = document.createElement("span");
    pill.className = "pill";
    pill.textContent = text;
    parent.appendChild(pill);
  }

  function renderStanding(you) {
    el.standRank.textContent = "#" + you.rank;
    el.standOf.textContent = "van de " + you.total_players + " spelers";
    el.standScore.textContent = fmt(you.score) + " punten";
    var move = you.rank_change;
    el.standDelta.textContent =
      move > 0
        ? "▲ " + move + " plaats" + (move > 1 ? "en" : "") + " gestegen"
        : move < 0
          ? "▼ " +
            Math.abs(move) +
            " plaats" +
            (move < -1 ? "en" : "") +
            " gezakt"
          : "Positie behouden";
    renderMiniBoard(el.standBoard, state.leaderboard || [], you.id);
    show("standing");
  }

  function renderDone(you) {
    var medals = { 1: "🥇", 2: "🥈", 3: "🥉" };
    el.doneMedal.textContent = medals[you.rank] || "🏅";
    el.doneTitle.textContent = you.rank === 1 ? "Jij wint! 🎉" : "Quiz gedaan!";
    el.doneRank.textContent = "#" + you.rank;
    el.doneOf.textContent = "van de " + you.total_players + " spelers";
    el.doneScore.textContent = fmt(you.score) + " punten";
    el.doneStats.innerHTML = "";
    addPill(el.doneStats, "✅ " + you.correct_count + " juist");
    if (you.best_streak >= 2)
      addPill(el.doneStats, "🔥 beste reeks: " + you.best_streak);
    renderMiniBoard(el.doneBoard, (state.standings || []).slice(0, 5), you.id);
    show("done");
  }

  function renderMiniBoard(container, rows, youId) {
    container.innerHTML = "";
    var medals = { 1: "🥇", 2: "🥈", 3: "🥉" };
    rows.forEach(function (row) {
      var line = document.createElement("div");
      line.className = "mini-board__row" + (row.id === youId ? " is-you" : "");
      var pos = document.createElement("span");
      pos.className = "mini-board__pos";
      pos.textContent = medals[row.rank] || row.rank + ".";
      var name = document.createElement("span");
      name.className = "mini-board__name";
      name.textContent = row.name;
      var score = document.createElement("span");
      score.className = "mini-board__score";
      score.textContent = fmt(row.score);
      line.appendChild(pos);
      line.appendChild(name);
      line.appendChild(score);
      container.appendChild(line);
    });
  }

  // Naam alvast invullen bij een terugkerende speler.
  var remembered = store(STORE_NAME);
  if (remembered) el.nameInput.value = remembered;
  el.nameInput.focus();
})();

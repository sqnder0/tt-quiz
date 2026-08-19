/* Presentatorscherm. Toont de servertoestand en stuurt hostcommando's terug.
   Beslist zelf niets over punten of timing -- dat doet de server. */

(function () {
  "use strict";

  var STORE_SECRET = "jhquiz.host_secret";
  var OPT_CLASS = ["opt-a", "opt-b", "opt-c", "opt-d"];
  var RING = 2 * Math.PI * 52;

  var $ = function (id) { return document.getElementById(id); };

  var el = {
    conn: $("conn"), connText: $("connText"),
    gate: $("gate"), gateForm: $("gateForm"), gateInput: $("gateInput"), gateError: $("gateError"),
    stage: $("stage"), controls: $("controls"),
    quizTitle: $("quizTitle"), chipPhase: $("chipPhase"), chipProgress: $("chipProgress"),
    chipAnswers: $("chipAnswers"), chipPlayers: $("chipPlayers"),
    viewLobby: $("viewLobby"), viewQuestion: $("viewQuestion"), viewBoard: $("viewBoard"), viewFinale: $("viewFinale"),
    lobbyTitle: $("lobbyTitle"), lobbyCount: $("lobbyCount"), lobbyCountLabel: $("lobbyCountLabel"),
    joinUrl: $("joinUrl"), names: $("names"),
    qNumber: $("qNumber"), qCategory: $("qCategory"), qDouble: $("qDouble"),
    timer: $("timer"), timerValue: $("timerValue"), timerNum: $("timerNum"),
    qMedia: $("qMedia"), qImage: $("qImage"), qVisual: $("qVisual"), qText: $("qText"), grid: $("grid"),
    estimateHost: $("estimateHost"), estimateBox: $("estimateBox"),
    estimateAnswer: $("estimateAnswer"), estimateClosest: $("estimateClosest"),
    revealBar: $("revealBar"), revealCorrect: $("revealCorrect"), revealCount: $("revealCount"),
    revealTotal: $("revealTotal"), revealNoAnswer: $("revealNoAnswer"), revealExplain: $("revealExplain"),
    boardTitle: $("boardTitle"), boardList: $("boardList"),
    finaleTitle: $("finaleTitle"), podium: $("podium"),
    showStandings: $("showStandings"), finalList: $("finalList"),
    btnPrimary: $("btnPrimary"), btnPause: $("btnPause"),
    btnRestart: $("btnRestart"), btnFinish: $("btnFinish"), optAuto: $("optAuto"),
    drawer: $("drawer"), drawerList: $("drawerList"), drawerCount: $("drawerCount"),
    drawerClose: $("drawerClose"), btnClearAbsent: $("btnClearAbsent")
  };

  var state = null;
  var socket = null;
  var secret = "";
  var timer = { remaining: 0, total: 1, running: false, at: 0 };
  var lastQuestionId = null;
  var revealPainted = null;
  var finalePlayed = false;
  var nameNodes = {};
  var confetti = new Confetti($("confetti"));

  function fmt(n) {
    return String(Math.round(n || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch (err) { /* privémodus: dan typt de leiding de code gewoon opnieuw */ }
    return null;
  }

  // --- toegangspoort ------------------------------------------------------

  function openGate(message) {
    el.gate.hidden = false;
    el.stage.hidden = true;
    el.controls.hidden = true;
    el.gateError.textContent = message || "";
    el.gateInput.focus();
  }

  function tryStart(candidate) {
    return fetch("/api/host/check?secret=" + encodeURIComponent(candidate))
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data.ok) return false;
        secret = candidate;
        store(STORE_SECRET, candidate);
        el.gate.hidden = true;
        el.stage.hidden = false;
        el.controls.hidden = false;
        connect();
        return true;
      });
  }

  el.gateForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var candidate = el.gateInput.value.trim();
    if (!candidate) return;
    el.gateError.textContent = "";
    tryStart(candidate).then(function (ok) {
      if (!ok) el.gateError.textContent = "Die code klopt niet.";
    }).catch(function () {
      el.gateError.textContent = "Geen verbinding met de server.";
    });
  });

  // --- verbinding ---------------------------------------------------------

  function setStatus(status) {
    var labels = {
      connecting: "Verbinden…", reconnecting: "Opnieuw verbinden…",
      online: "Verbonden", offline: "Geen verbinding"
    };
    el.conn.dataset.status = status === "reconnecting" ? "connecting" : status;
    el.connText.textContent = labels[status] || status;
  }

  function connect() {
    socket = new QuizSocket({
      path: "/ws/host",
      query: "secret=" + encodeURIComponent(secret),
      onStatus: setStatus,
      onMessage: handle,
      onFatal: function (code, message) {
        if (code === 4403) store(STORE_SECRET, null);
        openGate(message);
      }
    });
    socket.connect();
  }

  function send(type, extra) {
    var payload = { t: type };
    if (extra) Object.keys(extra).forEach(function (k) { payload[k] = extra[k]; });
    socket.send(payload);
  }

  function handle(msg) {
    if (msg.t === "state") {
      state = msg;
      render();
    } else if (msg.t === "tick") {
      applyTimer(msg.timer);
      if (msg.answers) paintAnswerCount(msg.answers);
    } else if (msg.t === "error") {
      if (msg.code !== "host_superseded") console.warn(msg.message);
    }
  }

  // --- knoppen ------------------------------------------------------------

  var PRIMARY = {
    LOBBY: { label: "▶ Start de quiz", action: "host_start" },
    QUESTION: { label: "Toon het antwoord", action: "host_reveal" },
    ANSWER_REVEAL: { label: "Tussenstand →", action: "host_next" },
    LEADERBOARD: { label: "Volgende vraag →", action: "host_next" },
    FINISHED: { label: "↻ Nog een keer spelen", action: "host_restart" }
  };

  function primaryAction() {
    if (!state) return;
    var entry = PRIMARY[state.phase];
    if (entry) send(entry.action);
  }

  el.btnPrimary.addEventListener("click", primaryAction);
  el.btnPause.addEventListener("click", function () {
    if (!state || !state.timer) return;
    send(state.timer.paused ? "host_resume" : "host_pause");
  });
  el.btnRestart.addEventListener("click", function () {
    if (window.confirm("Scores wissen en terug naar de lobby? De spelers blijven binnen.")) {
      send("host_restart");
    }
  });
  el.btnFinish.addEventListener("click", function () {
    if (window.confirm("De quiz nu afsluiten en meteen naar de eindstand gaan?")) {
      send("host_finish");
    }
  });
  el.optAuto.addEventListener("change", function () {
    send("host_options", { auto_advance: el.optAuto.checked });
  });

  document.addEventListener("keydown", function (event) {
    if (el.gate.hidden === false) return;
    var tag = (event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    if (event.code === "Space" || event.code === "Enter") {
      event.preventDefault();
      primaryAction();
    } else if (event.key === "p" || event.key === "P") {
      el.btnPause.click();
    }
  });

  el.chipPlayers.addEventListener("click", function () { el.drawer.classList.toggle("is-open"); });
  el.drawerClose.addEventListener("click", function () { el.drawer.classList.remove("is-open"); });
  el.btnClearAbsent.addEventListener("click", function () {
    if (window.confirm("Alle spelers verwijderen die nu niet verbonden zijn?")) {
      send("host_clear_absent");
    }
  });
  el.showStandings.addEventListener("click", function () {
    el.finalList.hidden = !el.finalList.hidden;
    el.showStandings.textContent = el.finalList.hidden
      ? "Toon volledig eindklassement" : "Verberg eindklassement";
  });

  // --- timer --------------------------------------------------------------

  function applyTimer(payload) {
    if (!payload) { timer.running = false; return; }
    timer.remaining = payload.remaining_ms;
    timer.total = payload.total_ms || 1;
    timer.running = payload.running;
    timer.at = (window.performance && performance.now) ? performance.now() : Date.now();
    el.timer.classList.toggle("is-paused", !!payload.paused);
    paintTimer();
  }

  function paintTimer() {
    if (el.viewQuestion.hidden || !state || state.phase !== "QUESTION") return;
    var now = (window.performance && performance.now) ? performance.now() : Date.now();
    var left = timer.remaining - (timer.running ? now - timer.at : 0);
    left = Math.max(0, left);
    var seconds = Math.ceil(left / 1000);
    el.timerNum.textContent = timer.running ? seconds : "⏸";
    var fraction = Math.max(0, Math.min(1, left / timer.total));
    el.timerValue.style.strokeDashoffset = String(RING * (1 - fraction));
    el.timer.classList.toggle("is-low", seconds <= 10 && seconds > 5);
    el.timer.classList.toggle("is-critical", seconds <= 5);
  }

  (function loop() {
    paintTimer();
    window.requestAnimationFrame(loop);
  })();

  // --- tekenen ------------------------------------------------------------

  var PHASE_LABEL = {
    LOBBY: "Lobby", QUESTION: "Vraag bezig", ANSWER_REVEAL: "Antwoord",
    LEADERBOARD: "Tussenstand", FINISHED: "Einde"
  };

  function render() {
    if (!state) return;
    el.quizTitle.textContent = state.quiz.title;
    document.title = "Presentator — " + state.quiz.title;
    el.chipPhase.textContent = PHASE_LABEL[state.phase] || state.phase;
    el.chipPlayers.innerHTML = "👥 <b>" + state.counts.players + "</b> spelers";
    el.optAuto.checked = !!(state.options && state.options.auto_advance);

    renderPlayers(state.players || []);
    updateControls();

    var progress = state.progress;
    el.chipProgress.hidden = !progress;
    if (progress) el.chipProgress.textContent = "Vraag " + progress.number + "/" + progress.total;
    el.chipAnswers.hidden = state.phase !== "QUESTION";
    if (state.answers) paintAnswerCount(state.answers);

    el.viewLobby.hidden = state.phase !== "LOBBY";
    el.viewQuestion.hidden = !(state.phase === "QUESTION" || state.phase === "ANSWER_REVEAL");
    el.viewBoard.hidden = state.phase !== "LEADERBOARD";
    el.viewFinale.hidden = state.phase !== "FINISHED";

    if (state.phase !== "FINISHED") {
      finalePlayed = false;
      confetti.stop();
    }

    switch (state.phase) {
      case "LOBBY": renderLobby(); break;
      case "QUESTION": renderQuestion(); break;
      case "ANSWER_REVEAL": renderQuestion(); renderReveal(); break;
      case "LEADERBOARD": renderBoard(); break;
      case "FINISHED": renderFinale(); break;
    }
    applyTimer(state.timer);
  }

  function paintAnswerCount(answers) {
    el.chipAnswers.textContent = (answers.received || 0) + " / " + (answers.expected || 0) + " geantwoord";
  }

  function updateControls() {
    var entry = PRIMARY[state.phase];
    if (entry) {
      var label = entry.label;
      if (state.phase === "ANSWER_REVEAL" && state.progress &&
          state.progress.number === state.progress.total) {
        label = "🏆 Naar de finale →";
      }
      el.btnPrimary.textContent = label;
      el.btnPrimary.hidden = false;
      el.btnPrimary.disabled = state.phase === "LOBBY" && state.counts.players === 0;
    } else {
      el.btnPrimary.hidden = true;
    }
    el.btnPause.hidden = state.phase !== "QUESTION";
    if (state.timer) el.btnPause.textContent = state.timer.paused ? "▶ Verder" : "⏸ Pauze";
  }

  function renderLobby() {
    var count = state.counts.players;
    el.lobbyCount.textContent = count;
    el.lobbyCountLabel.textContent = count === 1 ? "speler wacht" : "spelers wachten";
    el.lobbyTitle.textContent = count === 0
      ? "Scan mee en doe mee!"
      : "Klaar voor de Boomhutten Quiz?";
    el.joinUrl.textContent = window.location.host + "/";
    lastQuestionId = null;
    revealPainted = null;
  }

  // Namen apart bijhouden zodat enkel nieuwkomers animeren.
  function renderPlayers(players) {
    var seen = {};
    players.forEach(function (player) {
      seen[player.id] = true;
      var node = nameNodes[player.id];
      if (!node) {
        node = document.createElement("span");
        node.className = "name-tag";
        node.textContent = player.name;
        nameNodes[player.id] = node;
        el.names.appendChild(node);
      } else if (node.textContent !== player.name) {
        node.textContent = player.name;
      }
      node.dataset.connected = player.connected ? "true" : "false";
    });
    Object.keys(nameNodes).forEach(function (id) {
      if (!seen[id]) {
        nameNodes[id].remove();
        delete nameNodes[id];
      }
    });

    var absent = players.filter(function (p) { return !p.connected; }).length;
    // Enkel in de lobby: tijdens een vraag is "niet verbonden" vaak gewoon
    // een gsm die even op slot ging.
    el.btnClearAbsent.hidden = !(state.phase === "LOBBY" && absent > 0);
    el.btnClearAbsent.textContent = "Afwezigen verwijderen (" + absent + ")";

    el.drawerCount.textContent = players.length;
    el.drawerList.innerHTML = "";
    players.slice().sort(function (a, b) { return b.score - a.score; }).forEach(function (player) {
      var row = document.createElement("div");
      row.className = "drawer__row";
      row.dataset.connected = player.connected ? "true" : "false";
      var name = document.createElement("span");
      name.className = "drawer__name";
      name.textContent = (player.connected ? "🟢 " : "⚪ ") + player.name;
      var score = document.createElement("span");
      score.className = "drawer__score";
      score.textContent = fmt(player.score);
      var kick = document.createElement("button");
      kick.className = "drawer__kick";
      kick.type = "button";
      kick.textContent = "verwijder";
      kick.addEventListener("click", function () {
        if (window.confirm("“" + player.name + "” uit de quiz halen?")) {
          send("host_kick", { player_id: player.id });
        }
      });
      row.appendChild(name);
      row.appendChild(score);
      row.appendChild(kick);
      el.drawerList.appendChild(row);
    });
  }

  function renderQuestion() {
    var q = state.question;
    if (!q) return;
    if (q.id !== lastQuestionId) {
      lastQuestionId = q.id;
      revealPainted = null;
      buildQuestion(q);
    }
  }

  function buildQuestion(q) {
    el.qNumber.textContent = "Vraag " + q.number + "/" + q.total;
    el.qCategory.textContent = q.category;
    el.qDouble.hidden = !q.double;
    el.qText.textContent = q.text;

    var hasMedia = !!(q.image || q.visual);
    el.qMedia.hidden = !hasMedia;
    el.qImage.hidden = !q.image;
    if (q.image) el.qImage.src = q.image;
    el.qVisual.hidden = !q.visual;
    el.qVisual.textContent = q.visual || "";

    var isEstimate = q.type === "estimate";
    el.grid.hidden = isEstimate;
    el.estimateHost.hidden = !isEstimate;
    el.estimateAnswer.hidden = true;
    el.estimateBox.hidden = false;
    el.estimateClosest.innerHTML = "";
    el.revealBar.hidden = true;
    el.grid.dataset.reveal = "false";
    el.grid.innerHTML = "";
    if (isEstimate) return;

    (q.options || []).forEach(function (option, index) {
      var card = document.createElement("div");
      card.className = "opt " + OPT_CLASS[index];
      card.dataset.index = String(index);
      card.innerHTML =
        '<div class="opt__bar"></div>' +
        '<div class="opt__key"><span class="shape"></span><span class="opt__letter"></span></div>' +
        '<div class="opt__text"></div>' +
        '<div class="opt__stat" hidden><span class="opt__pct"></span><span class="opt__count"></span></div>';
      card.querySelector(".opt__letter").textContent = option.key;
      card.querySelector(".opt__text").textContent = option.text;
      el.grid.appendChild(card);
    });
  }

  function renderReveal() {
    var reveal = state.reveal;
    var q = state.question;
    if (!reveal || !q) return;
    if (revealPainted === q.id) return;
    revealPainted = q.id;

    el.revealBar.hidden = false;
    el.revealCorrect.textContent = reveal.correct_text;
    el.revealCount.textContent = reveal.num_correct;
    el.revealTotal.textContent = reveal.num_answered;
    el.revealNoAnswer.textContent = reveal.no_answer > 0
      ? "😴 " + reveal.no_answer + " zonder antwoord" : "";
    el.revealExplain.textContent = reveal.explanation || "";
    el.revealExplain.hidden = !reveal.explanation;

    if (reveal.type === "estimate") {
      el.estimateBox.hidden = true;
      el.estimateAnswer.hidden = false;
      el.estimateAnswer.textContent = reveal.correct_value + " " + (reveal.unit || "");
      el.estimateClosest.innerHTML = "";
      if (reveal.average !== null && reveal.average !== undefined) {
        addChip(el.estimateClosest, "Gemiddelde gok: " + reveal.average + " " + (reveal.unit || ""));
      }
      (reveal.closest || []).forEach(function (entry, index) {
        var medal = ["🥇", "🥈", "🥉"][index] || "•";
        addChip(el.estimateClosest, medal + " " + entry.name + ": " + entry.value);
      });
      return;
    }

    el.grid.dataset.reveal = "true";
    var cards = el.grid.querySelectorAll(".opt");
    (reveal.counts || []).forEach(function (entry, index) {
      var card = cards[index];
      if (!card) return;
      card.classList.toggle("is-correct", entry.correct);
      var stat = card.querySelector(".opt__stat");
      stat.hidden = false;
      card.querySelector(".opt__pct").textContent = entry.pct + "%";
      card.querySelector(".opt__count").textContent =
        entry.count === 1 ? "1 speler" : entry.count + " spelers";
      // Even wachten zodat de balken zichtbaar oplopen.
      window.setTimeout(function () {
        card.querySelector(".opt__bar").style.width = entry.pct + "%";
      }, 60);
    });
  }

  function addChip(parent, text) {
    var chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = text;
    parent.appendChild(chip);
  }

  function renderBoard() {
    var rows = state.leaderboard || [];
    el.boardTitle.textContent = state.progress
      ? "🏆 Tussenstand na vraag " + state.progress.number
      : "🏆 Tussenstand";
    fillBoard(el.boardList, rows.slice(0, 10), true);
  }

  function fillBoard(container, rows, withDelta) {
    container.innerHTML = "";
    var medals = { 1: "🥇", 2: "🥈", 3: "🥉" };
    rows.forEach(function (row) {
      var line = document.createElement("div");
      line.className = "row";
      line.dataset.connected = row.connected ? "true" : "false";

      var pos = document.createElement("span");
      pos.className = "row__pos";
      pos.textContent = medals[row.rank] || row.rank + ".";

      var name = document.createElement("span");
      name.className = "row__name";
      name.textContent = row.name;

      line.appendChild(pos);
      line.appendChild(name);

      if (row.streak >= 3) {
        var streak = document.createElement("span");
        streak.className = "row__streak";
        streak.textContent = "🔥" + row.streak;
        line.appendChild(streak);
      }

      if (withDelta) {
        var move = document.createElement("span");
        var change = row.rank_change || 0;
        move.className = "row__move " + (change > 0 ? "row__move--up" : change < 0 ? "row__move--down" : "row__move--same");
        move.textContent = change > 0 ? "▲" + change : change < 0 ? "▼" + Math.abs(change) : "–";
        line.appendChild(move);

        var delta = document.createElement("span");
        delta.className = "row__delta";
        delta.textContent = row.delta > 0 ? "+" + fmt(row.delta) : "";
        line.appendChild(delta);
      }

      var score = document.createElement("span");
      score.className = "row__score";
      score.textContent = fmt(row.score);
      line.appendChild(score);

      container.appendChild(line);
    });
  }

  function renderFinale() {
    var podium = state.podium || [];
    fillBoard(el.finalList, state.standings || [], false);

    if (finalePlayed) return;
    finalePlayed = true;

    el.podium.innerHTML = "";
    // Visuele volgorde: 2 - 1 - 3, maar ze verschijnen van brons naar goud.
    var layout = [podium[1], podium[0], podium[2]];
    var spots = [];
    layout.forEach(function (entry) {
      if (!entry) return;
      var spot = document.createElement("div");
      spot.className = "podium__spot";
      spot.dataset.place = String(entry.rank);
      spot.innerHTML =
        '<div class="podium__medal"></div>' +
        '<div class="podium__name"></div>' +
        '<div class="podium__score"></div>' +
        '<div class="podium__block"></div>';
      spot.querySelector(".podium__medal").textContent = { 1: "🥇", 2: "🥈", 3: "🥉" }[entry.rank];
      spot.querySelector(".podium__name").textContent = entry.name;
      spot.querySelector(".podium__score").textContent = fmt(entry.score) + " punten";
      spot.querySelector(".podium__block").textContent = entry.rank;
      el.podium.appendChild(spot);
      spots[entry.rank] = spot;
    });

    var titles = { 3: "🥉 Derde plaats…", 2: "🥈 Tweede plaats…", 1: "🥇 EN DE WINNAAR IS…" };
    [3, 2, 1].forEach(function (place, index) {
      window.setTimeout(function () {
        if (!state || state.phase !== "FINISHED") return;
        el.finaleTitle.textContent = titles[place];
        if (spots[place]) spots[place].classList.add("is-in");
        if (place === 1) {
          confetti.burst(180, 7000);
          window.setTimeout(function () {
            if (state && state.phase === "FINISHED") {
              el.finaleTitle.textContent = "🎉 Proficiat!";
            }
          }, 3500);
        }
      }, 900 + index * 1900);
    });
  }

  // --- opstarten ----------------------------------------------------------

  var saved = store(STORE_SECRET);
  if (saved) {
    tryStart(saved).then(function (ok) {
      if (!ok) { store(STORE_SECRET, null); openGate(); }
    }).catch(function () { openGate("Geen verbinding met de server."); });
  } else {
    openGate();
  }
})();

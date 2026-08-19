/* Beamerscherm. Tekent de servertoestand, beslist zelf niets.

   Bedienen gebeurt normaal op /admin, maar spatie en P werken hier ook -- handig
   als de leiding met één laptop staat. */

(function () {
  "use strict";

  var $ = QuizUI.$;
  var fmt = QuizUI.fmt;
  var OPT_CLASS = ["opt-a", "opt-b", "opt-c", "opt-d"];
  var RING = 2 * Math.PI * 52;
  var MAX_NAMES = 60;

  var el = {
    conn: $("conn"), connText: $("connText"),
    stage: $("stage"), hint: $("hint"),
    quizTitle: $("quizTitle"), chipPhase: $("chipPhase"), chipProgress: $("chipProgress"),
    chipAnswers: $("chipAnswers"), chipPlayers: $("chipPlayers"),
    viewLobby: $("viewLobby"), viewQuestion: $("viewQuestion"),
    viewBoard: $("viewBoard"), viewFinale: $("viewFinale"),
    lobbyCount: $("lobbyCount"), lobbyCountLabel: $("lobbyCountLabel"),
    joinUrl: $("joinUrl"), names: $("names"),
    qNumber: $("qNumber"), qCategory: $("qCategory"), qDouble: $("qDouble"),
    timer: $("timer"), timerValue: $("timerValue"), timerNum: $("timerNum"),
    qMedia: $("qMedia"), qImage: $("qImage"), qVisual: $("qVisual"), qText: $("qText"),
    grid: $("grid"),
    estimateHost: $("estimateHost"), estimateBox: $("estimateBox"),
    estimateAnswer: $("estimateAnswer"), estimateClosest: $("estimateClosest"),
    revealBar: $("revealBar"), revealCorrect: $("revealCorrect"), revealCount: $("revealCount"),
    revealTotal: $("revealTotal"), revealNoAnswer: $("revealNoAnswer"), revealExplain: $("revealExplain"),
    boardTitle: $("boardTitle"), boardList: $("boardList"),
    finaleTitle: $("finaleTitle"), podium: $("podium"), finalList: $("finalList")
  };

  var state = null;
  var timer = { remaining: 0, total: 1, running: false, at: 0 };
  var lastQuestionId = null;
  var revealPainted = null;
  var finalePlayed = false;
  var nameNodes = {};
  var confetti = new Confetti($("confetti"));

  var gate = new HostGate({
    view: "present",
    reveal: [el.stage, el.hint],
    onStatus: QuizUI.statusBinder(el.conn, el.connText),
    onMessage: handle
  });
  gate.start();

  function handle(msg) {
    if (msg.t === "state") {
      state = msg;
      render();
    } else if (msg.t === "tick") {
      applyTimer(msg.timer);
      if (msg.answers) paintAnswerCount(msg.answers);
    }
  }

  // --- sneltoetsen --------------------------------------------------------

  var PRIMARY = {
    LOBBY: "host_start",
    QUESTION: "host_reveal",
    ANSWER_REVEAL: "host_next",
    LEADERBOARD: "host_next",
    FINISHED: "host_restart"
  };

  document.addEventListener("keydown", function (event) {
    if (!state || $("gate").hidden === false) return;
    var tag = (event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    if (event.code === "Space" || event.code === "Enter") {
      event.preventDefault();
      var action = PRIMARY[state.phase];
      if (action) gate.send(action);
    } else if (event.key === "p" || event.key === "P") {
      if (state.timer) gate.send(state.timer.paused ? "host_resume" : "host_pause");
    }
  });

  // --- timer --------------------------------------------------------------

  function applyTimer(payload) {
    if (!payload) {
      timer.running = false;
      return;
    }
    timer.remaining = payload.remaining_ms;
    timer.total = payload.total_ms || 1;
    timer.running = payload.running;
    timer.at = now();
    paintTimer();
  }

  function now() {
    return (window.performance && performance.now) ? performance.now() : Date.now();
  }

  function paintTimer() {
    if (el.viewQuestion.hidden) return;
    var left = timer.remaining - (timer.running ? now() - timer.at : 0);
    var seconds = Math.max(0, Math.ceil(left / 1000));
    var fraction = Math.max(0, Math.min(1, left / (timer.total || 1)));

    el.timerNum.textContent = timer.running ? seconds : "⏸";
    el.timerValue.style.strokeDashoffset = String(RING * (1 - fraction));
    el.timer.classList.toggle("is-low", timer.running && seconds <= 5);
    el.timer.classList.toggle("is-paused", !timer.running);
  }

  (function loop() {
    paintTimer();
    window.requestAnimationFrame(loop);
  })();

  // --- tekenen ------------------------------------------------------------

  var PHASE_LABEL = {
    LOBBY: "Lobby", QUESTION: "Vraag", ANSWER_REVEAL: "Antwoord",
    LEADERBOARD: "Tussenstand", FINISHED: "Einde"
  };

  function show(view) {
    el.viewLobby.hidden = view !== "lobby";
    el.viewQuestion.hidden = view !== "question";
    el.viewBoard.hidden = view !== "board";
    el.viewFinale.hidden = view !== "finale";
  }

  function render() {
    if (!state) return;

    el.quizTitle.textContent = state.quiz.title;
    document.title = "Beamer — " + state.quiz.title;
    el.chipPhase.textContent = PHASE_LABEL[state.phase] || state.phase;

    el.chipProgress.hidden = !state.progress;
    if (state.progress) {
      el.chipProgress.textContent = "Vraag " + state.progress.number + "/" + state.progress.total;
    }

    el.chipPlayers.innerHTML = "";
    el.chipPlayers.appendChild(document.createTextNode("👥 "));
    el.chipPlayers.appendChild(QuizUI.el("b", null, state.counts.connected));

    if (state.phase === "QUESTION" && state.answers) {
      paintAnswerCount(state.answers);
      el.chipAnswers.hidden = false;
    } else {
      el.chipAnswers.hidden = true;
    }

    if (state.phase !== "FINISHED") finalePlayed = false;
    if (state.phase !== "ANSWER_REVEAL") revealPainted = null;

    applyTimer(state.timer);

    switch (state.phase) {
      case "LOBBY": renderLobby(); break;
      case "QUESTION": renderQuestion(); break;
      case "ANSWER_REVEAL": renderReveal(); break;
      case "LEADERBOARD": renderBoard(); break;
      case "FINISHED": renderFinale(); break;
    }
  }

  function paintAnswerCount(answers) {
    var received = answers.received || 0;
    var expected = answers.expected || 0;
    el.chipAnswers.textContent = received + " / " + expected + " geantwoord";
  }

  // --- lobby --------------------------------------------------------------

  function renderLobby() {
    show("lobby");
    lastQuestionId = null;

    var count = state.counts.players;
    el.lobbyCount.textContent = count;
    el.lobbyCountLabel.textContent = count === 1 ? "speler" : "spelers";
    el.joinUrl.textContent = window.location.host;

    var players = state.players || [];
    var seen = {};
    players.slice(0, MAX_NAMES).forEach(function (player) {
      seen[player.id] = true;
      var node = nameNodes[player.id];
      if (!node) {
        node = QuizUI.el("span", "names__item", player.name);
        nameNodes[player.id] = node;
        el.names.appendChild(node);
      } else if (node.textContent !== player.name) {
        node.textContent = player.name;
      }
      node.dataset.off = player.connected ? "false" : "true";
    });
    Object.keys(nameNodes).forEach(function (id) {
      if (!seen[id]) {
        nameNodes[id].remove();
        delete nameNodes[id];
      }
    });

    var overflow = el.names.querySelector(".names__more");
    if (players.length > MAX_NAMES) {
      if (!overflow) {
        overflow = QuizUI.el("span", "names__more");
        el.names.appendChild(overflow);
      }
      overflow.textContent = "+ " + (players.length - MAX_NAMES) + " meer";
    } else if (overflow) {
      overflow.remove();
    }
  }

  // --- vraag --------------------------------------------------------------

  function renderQuestion() {
    var q = state.question;
    if (!q) return;
    show("question");

    if (q.id !== lastQuestionId) {
      lastQuestionId = q.id;
      buildQuestion(q);
    }
    el.grid.dataset.reveal = "false";
    el.revealBar.hidden = true;
    el.estimateAnswer.hidden = true;
    el.estimateClosest.innerHTML = "";
    paintTimer();
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
    el.estimateBox.textContent = "? ? ?";

    el.grid.innerHTML = "";
    if (isEstimate) return;

    (q.options || []).forEach(function (option, index) {
      var card = QuizUI.el("div", "opt " + OPT_CLASS[index]);
      card.style.animationDelay = (index * 0.06) + "s";
      card.appendChild(QuizUI.el("div", "opt__bar"));

      // Vorm én letter: de speler ziet op zijn gsm dezelfde combinatie, en de
      // vorm maakt de koppeling ook zonder kleur duidelijk.
      var key = QuizUI.el("div", "opt__key");
      key.appendChild(QuizUI.el("span", "shape"));
      key.appendChild(QuizUI.el("span", "opt__letter", option.key));
      card.appendChild(key);

      card.appendChild(QuizUI.el("div", "opt__text", option.text));

      var count = QuizUI.el("div", "opt__count");
      count.appendChild(QuizUI.el("span", "opt__pct"));
      count.appendChild(QuizUI.el("span", "opt__num"));
      card.appendChild(count);
      card.appendChild(QuizUI.el("span", "opt__check", "✅"));

      el.grid.appendChild(card);
    });
  }

  // --- onthulling ---------------------------------------------------------

  function renderReveal() {
    var q = state.question;
    var reveal = state.reveal;
    if (!q || !reveal) return;
    show("question");

    if (q.id !== lastQuestionId) {
      lastQuestionId = q.id;
      buildQuestion(q);
    }

    el.revealBar.hidden = false;
    el.revealCorrect.textContent = reveal.correct_text || "";
    el.revealCount.textContent = reveal.num_correct;
    el.revealTotal.textContent = reveal.num_answered;
    el.revealNoAnswer.textContent = reveal.no_answer
      ? "😴 " + reveal.no_answer + " zonder antwoord"
      : "";
    el.revealExplain.textContent = reveal.explanation || "";

    if (revealPainted === q.id) return;
    revealPainted = q.id;

    if (reveal.type === "estimate") {
      el.estimateBox.textContent = "? ? ?";
      el.estimateAnswer.hidden = false;
      el.estimateAnswer.textContent = reveal.correct_text;
      el.estimateClosest.innerHTML = "";
      (reveal.closest || []).forEach(function (item) {
        var node = QuizUI.el("span", "closest__item");
        node.appendChild(QuizUI.el("b", null, item.name));
        node.appendChild(document.createTextNode(" " + item.value + " " + (reveal.unit || "")));
        el.estimateClosest.appendChild(node);
      });
      return;
    }

    el.grid.dataset.reveal = "true";
    var cards = el.grid.querySelectorAll(".opt");
    (reveal.counts || []).forEach(function (entry, index) {
      var card = cards[index];
      if (!card) return;
      card.classList.toggle("is-correct", entry.correct);
      card.querySelector(".opt__bar").style.width = entry.pct + "%";
      card.querySelector(".opt__pct").textContent = entry.pct + "%";
      card.querySelector(".opt__num").textContent = "(" + entry.count + ")";
    });
  }

  // --- tussenstand --------------------------------------------------------

  function renderBoard() {
    show("board");
    var number = state.progress ? state.progress.number : 0;
    el.boardTitle.textContent = "🏆 Tussenstand na vraag " + number;
    paintRows(el.boardList, (state.leaderboard || []).slice(0, 10), true);
  }

  function paintRows(container, rows, showDelta) {
    container.innerHTML = "";
    rows.forEach(function (row, index) {
      var line = QuizUI.el("div", "row" + (row.rank <= 3 ? " row--top" : ""));
      line.style.animationDelay = (index * 0.04) + "s";
      line.appendChild(QuizUI.el("span", "row__pos", QuizUI.medal(row.rank) || row.rank + "."));
      line.appendChild(QuizUI.el("span", "row__name", row.name));

      var move = QuizUI.el("span", "row__move");
      if (row.rank_change > 0) {
        move.textContent = "▲" + row.rank_change;
        move.dataset.dir = "up";
      } else if (row.rank_change < 0) {
        move.textContent = "▼" + Math.abs(row.rank_change);
        move.dataset.dir = "down";
      } else {
        move.textContent = "–";
      }
      line.appendChild(move);

      var score = QuizUI.el("span", "row__score", fmt(row.score));
      if (showDelta && row.delta > 0) {
        var delta = QuizUI.el("span", "row__delta", " +" + fmt(row.delta));
        score.appendChild(delta);
      }
      line.appendChild(score);
      container.appendChild(line);
    });
  }

  // --- finale -------------------------------------------------------------

  function renderFinale() {
    show("finale");
    var podium = state.podium || [];
    var winner = podium[0];
    el.finaleTitle.textContent = winner ? "🎉 " + winner.name + " wint!" : "🎉 Quiz gedaan!";

    el.podium.innerHTML = "";
    [podium[1], podium[0], podium[2]].forEach(function (row) {
      if (!row) return;
      var pod = QuizUI.el("div", "pod pod--" + row.rank);
      pod.appendChild(QuizUI.el("div", "pod__medal", QuizUI.medal(row.rank) || "🏅"));
      pod.appendChild(QuizUI.el("div", "pod__name", row.name));
      pod.appendChild(QuizUI.el("div", "pod__block", fmt(row.score)));
      el.podium.appendChild(pod);
    });

    paintRows(el.finalList, (state.standings || []).slice(3, 11), false);

    if (!finalePlayed && podium.length) {
      finalePlayed = true;
      confetti.burst();
    }
  }
})();

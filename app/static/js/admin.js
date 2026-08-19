/* Bedieningspaneel.

   Drie taken: de quiz sturen, spelers beheren en vragen aanpassen. De vragen
   houdt dit scherm lokaal bij terwijl je typt; pas op "Bewaren" gaat de volledige
   lijst naar de server. Zo blijft het protocol één commando in plaats van een
   handvol toevoeg-, verplaats- en verwijderberichten. */

(function () {
  "use strict";

  var $ = QuizUI.$;
  var fmt = QuizUI.fmt;
  var toast = QuizUI.toast;
  var OPT_CLASS = ["opt-a", "opt-b", "opt-c", "opt-d"];
  var OPT_KEYS = ["A", "B", "C", "D"];

  var el = {
    conn: $("conn"), connText: $("connText"), panel: $("panel"),
    btnPrimary: $("btnPrimary"), btnPause: $("btnPause"),
    btnRestart: $("btnRestart"), btnFinish: $("btnFinish"), optAuto: $("optAuto"),
    chipPhase: $("chipPhase"), chipProgress: $("chipProgress"), chipTimer: $("chipTimer"),
    chipAnswers: $("chipAnswers"), chipPlayers: $("chipPlayers"),
    monitor: $("monitor"), monitorQuestion: $("monitorQuestion"), monitorAnswer: $("monitorAnswer"),
    tabs: $("tabs"), tabPlayerCount: $("tabPlayerCount"), tabQuestionCount: $("tabQuestionCount"),
    playerList: $("playerList"), playerEmpty: $("playerEmpty"), playerNote: $("playerNote"),
    joinUrl: $("joinUrl"),
    btnClearAbsent: $("btnClearAbsent"), btnClearAll: $("btnClearAll"),
    questionList: $("questionList"), qNotice: $("qNotice"),
    btnQSave: $("btnQSave"), btnQAdd: $("btnQAdd"),
    btnQRevert: $("btnQRevert"), btnQReset: $("btnQReset"),
    optReveal: $("optReveal"), optRevealOut: $("optRevealOut"),
    optBoard: $("optBoard"), optBoardOut: $("optBoardOut"),
    urlPresent: $("urlPresent"), urlPlay: $("urlPlay"), btnForget: $("btnForget")
  };

  var state = null;
  var timer = { remaining: 0, running: false, at: 0 };
  var questions = [];
  var serverQuestions = [];
  var meta = { editable: true, categories: [], limits: { min_time: 5, max_time: 90, default_time: 90 } };
  var dirty = false;
  var saving = false;
  var openId = null;
  var settingsTouched = false;

  var gate = new HostGate({
    view: "admin",
    reveal: [el.panel],
    onStatus: QuizUI.statusBinder(el.conn, el.connText),
    onOpen: function () { gate.send("host_questions"); },
    onMessage: handle
  });
  gate.start();

  function handle(msg) {
    if (msg.t === "state") {
      state = msg;
      render();
    } else if (msg.t === "tick") {
      applyTimer(msg.timer);
      if (msg.answers) paintAnswers(msg.answers);
    } else if (msg.t === "questions") {
      onQuestions(msg);
    } else if (msg.t === "event") {
      if (msg.name === "questions_changed" && !dirty) gate.send("host_questions");
    } else if (msg.t === "error") {
      saving = false;
      toast(msg.message || "Er ging iets mis.", "bad");
      updateQuestionBar();
    }
  }

  // --- bediening ----------------------------------------------------------

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
    if (entry) gate.send(entry.action);
  }

  el.btnPrimary.addEventListener("click", primaryAction);
  el.btnPause.addEventListener("click", function () {
    if (!state || !state.timer) return;
    gate.send(state.timer.paused ? "host_resume" : "host_pause");
  });
  el.btnRestart.addEventListener("click", function () {
    if (window.confirm("Scores wissen en terug naar de lobby? De spelers blijven binnen.")) {
      gate.send("host_restart");
    }
  });
  el.btnFinish.addEventListener("click", function () {
    if (window.confirm("De quiz nu afsluiten en meteen naar de eindstand gaan?")) {
      gate.send("host_finish");
    }
  });
  el.optAuto.addEventListener("change", function () {
    gate.send("host_options", { auto_advance: el.optAuto.checked });
  });

  document.addEventListener("keydown", function (event) {
    if (el.panel.hidden) return;
    var tag = (event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (event.code === "Space" || event.code === "Enter") {
      event.preventDefault();
      primaryAction();
    } else if (event.key === "p" || event.key === "P") {
      el.btnPause.click();
    }
  });

  // --- tabbladen ----------------------------------------------------------

  el.tabs.addEventListener("click", function (event) {
    var button = event.target.closest(".tab");
    if (!button) return;
    var name = button.dataset.tab;
    Array.prototype.forEach.call(el.tabs.children, function (tab) {
      tab.classList.toggle("is-active", tab === button);
    });
    ["players", "questions", "settings"].forEach(function (key) {
      $("pane-" + key).classList.toggle("is-active", key === name);
    });
  });

  // --- timer --------------------------------------------------------------

  function now() {
    return (window.performance && performance.now) ? performance.now() : Date.now();
  }

  function applyTimer(payload) {
    if (!payload) {
      timer.running = false;
      el.chipTimer.hidden = true;
      return;
    }
    timer.remaining = payload.remaining_ms;
    timer.running = payload.running;
    timer.at = now();
    el.chipTimer.hidden = false;
    paintTimer();
  }

  function paintTimer() {
    if (el.chipTimer.hidden) return;
    var left = timer.remaining - (timer.running ? now() - timer.at : 0);
    var seconds = Math.max(0, Math.ceil(left / 1000));
    el.chipTimer.textContent = timer.running ? "⏱ " + seconds + "s" : "⏸ gepauzeerd";
  }

  window.setInterval(paintTimer, 250);

  // --- tekenen ------------------------------------------------------------

  var PHASE_LABEL = {
    LOBBY: "Lobby", QUESTION: "Vraag loopt", ANSWER_REVEAL: "Antwoord getoond",
    LEADERBOARD: "Tussenstand", FINISHED: "Einde"
  };

  function render() {
    if (!state) return;
    document.title = "Bediening — " + state.quiz.title;

    var entry = PRIMARY[state.phase];
    el.btnPrimary.textContent = entry ? entry.label : "…";
    el.btnPrimary.disabled = !entry;

    el.btnPause.hidden = state.phase !== "QUESTION";
    if (state.timer) {
      el.btnPause.textContent = state.timer.paused ? "▶ Verder" : "⏸ Pauze";
    }

    el.chipPhase.textContent = PHASE_LABEL[state.phase] || state.phase;
    el.chipProgress.hidden = !state.progress;
    if (state.progress) {
      el.chipProgress.textContent = "Vraag " + state.progress.number + "/" + state.progress.total;
    }
    el.chipPlayers.textContent = "👥 " + state.counts.connected + "/" + state.counts.players;

    if (state.phase === "QUESTION" && state.answers) {
      paintAnswers(state.answers);
      el.chipAnswers.hidden = false;
    } else {
      el.chipAnswers.hidden = true;
    }

    if (state.options && !settingsTouched) {
      el.optAuto.checked = state.options.auto_advance;
      el.optReveal.value = state.options.auto_reveal_seconds;
      el.optRevealOut.textContent = state.options.auto_reveal_seconds + "s";
      el.optBoard.value = state.options.auto_leaderboard_seconds;
      el.optBoardOut.textContent = state.options.auto_leaderboard_seconds + "s";
    }

    applyTimer(state.timer);
    renderMonitor();
    renderPlayers();

    el.joinUrl.textContent = window.location.host;
    el.urlPresent.textContent = window.location.host + "/present";
    el.urlPlay.textContent = window.location.host;

    // Vragen aanpassen kan enkel in de lobby; de server weigert het anders toch.
    var editable = state.phase === "LOBBY";
    if (editable !== meta.editable) {
      meta.editable = editable;
      renderQuestions();
    }
    updateQuestionBar();
  }

  function paintAnswers(answers) {
    el.chipAnswers.textContent = (answers.received || 0) + " / " + (answers.expected || 0) + " geantwoord";
  }

  function renderMonitor() {
    var q = state.question;
    if (!q) {
      el.monitor.hidden = true;
      return;
    }
    el.monitor.hidden = false;
    el.monitorQuestion.textContent = "Vraag " + q.number + ". " + q.text;

    // In de vraagfase stuurt de server het juiste antwoord bewust niet mee --
    // ook niet naar dit scherm. We tonen het pas als het onthuld is.
    var reveal = state.reveal;
    el.monitorAnswer.textContent = reveal ? "✅ " + reveal.correct_text : "";
  }

  // --- spelers ------------------------------------------------------------

  function renderPlayers() {
    var players = state.players || [];
    el.tabPlayerCount.textContent = players.length;
    el.playerEmpty.hidden = players.length > 0;

    var absent = players.filter(function (p) { return !p.connected; }).length;
    el.btnClearAbsent.disabled = state.phase !== "LOBBY" || absent === 0;
    el.btnClearAll.disabled = state.phase !== "LOBBY" || players.length === 0;
    el.playerNote.textContent = state.phase === "LOBBY"
      ? (absent ? absent + " niet verbonden" : "")
      : "Opkuisen kan enkel in de lobby.";

    el.playerList.innerHTML = "";
    players.forEach(function (player) {
      var card = QuizUI.el("div", "pcard");
      card.dataset.off = player.connected ? "false" : "true";
      card.appendChild(QuizUI.el("span", "pcard__dot"));

      var body = QuizUI.el("div", "pcard__body");
      body.appendChild(QuizUI.el("div", "pcard__name", player.name));
      var meta = fmt(player.score) + " punten";
      if (player.streak >= 2) meta += " · 🔥 " + player.streak;
      if (!player.connected) meta += " · weg";
      else if (player.answered) meta += " · ✅ geantwoord";
      body.appendChild(QuizUI.el("div", "pcard__meta", meta));
      card.appendChild(body);

      var kick = QuizUI.el("button", "pcard__kick", "✕");
      kick.type = "button";
      kick.title = "Verwijder " + player.name;
      kick.addEventListener("click", function () {
        if (window.confirm(player.name + " uit de quiz verwijderen?")) {
          gate.send("host_kick", { player_id: player.id });
        }
      });
      card.appendChild(kick);

      el.playerList.appendChild(card);
    });
  }

  el.btnClearAbsent.addEventListener("click", function () {
    if (window.confirm("Alle spelers verwijderen die nu niet verbonden zijn?")) {
      gate.send("host_clear_absent");
    }
  });

  el.btnClearAll.addEventListener("click", function () {
    var count = (state && state.counts.players) || 0;
    if (window.confirm("De hele lobby leegmaken? Alle " + count + " spelers moeten dan opnieuw hun naam ingeven.")) {
      gate.send("host_clear_all");
    }
  });

  // --- instellingen -------------------------------------------------------

  function bindSlider(input, output, key) {
    input.addEventListener("input", function () {
      output.textContent = input.value + "s";
      settingsTouched = true;
    });
    input.addEventListener("change", function () {
      var payload = {};
      payload[key] = parseInt(input.value, 10);
      gate.send("host_options", payload);
      settingsTouched = false;
    });
  }

  bindSlider(el.optReveal, el.optRevealOut, "auto_reveal_seconds");
  bindSlider(el.optBoard, el.optBoardOut, "auto_leaderboard_seconds");

  el.btnForget.addEventListener("click", function () {
    try { window.localStorage.removeItem("jhquiz.host_secret"); } catch (err) { /* niets */ }
    window.location.reload();
  });

  // --- vragen -------------------------------------------------------------

  function onQuestions(msg) {
    serverQuestions = msg.items || [];
    meta.categories = msg.categories || [];
    meta.limits = msg.limits || meta.limits;
    meta.customised = msg.customised;

    // Is dit het antwoord op onze eigen opslag, dan is wat de server nu heeft de
    // waarheid en zijn we niet langer "vuil". Anders houden we lokale wijzigingen
    // die nog niet bewaard zijn, en tekenen we niets opnieuw.
    if (saving) {
      saving = false;
      dirty = false;
      toast("Vragen bewaard.", "ok");
    }
    if (!dirty) {
      questions = clone(serverQuestions);
      renderQuestions();
    }
    updateQuestionBar();
  }

  function clone(items) {
    return JSON.parse(JSON.stringify(items));
  }

  function markDirty() {
    dirty = true;
    updateQuestionBar();
  }

  function updateQuestionBar() {
    el.tabQuestionCount.textContent = questions.length;
    var locked = !meta.editable;
    el.btnQSave.disabled = !dirty || locked;
    el.btnQRevert.disabled = !dirty;
    el.btnQAdd.disabled = locked;
    el.btnQReset.disabled = locked;

    if (locked) {
      el.qNotice.hidden = false;
      el.qNotice.textContent = "Vragen aanpassen kan enkel vanuit de lobby. Klik op “↻ Opnieuw” om terug te gaan.";
    } else if (dirty) {
      el.qNotice.hidden = false;
      el.qNotice.textContent = "Je hebt wijzigingen die nog niet bewaard zijn.";
    } else {
      el.qNotice.hidden = true;
    }
  }

  function newQuestion() {
    return {
      id: "q-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 6),
      category: meta.categories[0] || "🏕️ Kamp",
      text: "",
      type: "multiple_choice",
      options: ["", "", "", ""],
      correct_index: 0,
      time_limit: meta.limits.default_time,
      points_multiplier: 1.0,
      image: null,
      visual: null,
      explanation: null,
      correct_value: null,
      unit: "",
      tolerance: 1,
      max_error: null
    };
  }

  el.btnQAdd.addEventListener("click", function () {
    var question = newQuestion();
    questions.push(question);
    openId = question.id;
    markDirty();
    renderQuestions();
    var node = el.questionList.lastElementChild;
    if (node) node.scrollIntoView({ block: "center", behavior: "smooth" });
  });

  el.btnQSave.addEventListener("click", function () {
    el.btnQSave.disabled = true;
    saving = true;
    if (!gate.send("host_questions_set", { items: questions })) {
      saving = false;
      updateQuestionBar();
      toast("Even geen verbinding. Probeer het zo nog eens.", "bad");
    }
  });

  el.btnQRevert.addEventListener("click", function () {
    if (!window.confirm("Je niet-bewaarde wijzigingen weggooien?")) return;
    questions = clone(serverQuestions);
    dirty = false;
    renderQuestions();
    updateQuestionBar();
  });

  el.btnQReset.addEventListener("click", function () {
    if (!window.confirm("Alle aanpassingen weggooien en terugkeren naar de ingebouwde vragenlijst?")) return;
    dirty = false;
    saving = true;
    gate.send("host_questions_reset");
  });

  function summarise(question) {
    return question.text || "(nog geen vraagtekst)";
  }

  function renderQuestions() {
    el.questionList.innerHTML = "";
    questions.forEach(function (question, index) {
      el.questionList.appendChild(buildRow(question, index));
    });
  }

  function buildRow(question, index) {
    var row = QuizUI.el("div", "qrow");
    var isOpen = question.id === openId;
    if (isOpen) row.classList.add("is-open");

    var head = QuizUI.el("div", "qrow__head");
    head.appendChild(QuizUI.el("span", "qrow__num", index + 1));

    var title = QuizUI.el("button", "qrow__title", summarise(question));
    title.type = "button";
    title.addEventListener("click", function () {
      openId = isOpen ? null : question.id;
      renderQuestions();
    });
    head.appendChild(title);

    var tags = QuizUI.el("div", "qrow__tags");
    if (question.points_multiplier >= 2) tags.appendChild(QuizUI.el("span", "tag tag--double", "2×"));
    tags.appendChild(QuizUI.el("span", "tag", question.time_limit + "s"));
    if (question.type === "estimate") tags.appendChild(QuizUI.el("span", "tag", "schatting"));
    head.appendChild(tags);

    var tools = QuizUI.el("div", "qrow__tools");
    tools.appendChild(tool("↑", "Omhoog", index === 0, function () { move(index, -1); }));
    tools.appendChild(tool("↓", "Omlaag", index === questions.length - 1, function () { move(index, 1); }));
    tools.appendChild(tool("⧉", "Dupliceren", false, function () { duplicate(index); }));
    var del = tool("🗑", "Verwijderen", questions.length <= 1, function () { remove(index); });
    del.classList.add("tool--del");
    tools.appendChild(del);
    head.appendChild(tools);

    row.appendChild(head);
    if (isOpen) row.appendChild(buildForm(question, title));
    return row;
  }

  function tool(label, title, disabled, handler) {
    var button = QuizUI.el("button", "tool", label);
    button.type = "button";
    button.title = title;
    button.disabled = disabled || !meta.editable;
    button.addEventListener("click", handler);
    return button;
  }

  function move(index, direction) {
    var target = index + direction;
    if (target < 0 || target >= questions.length) return;
    var moved = questions.splice(index, 1)[0];
    questions.splice(target, 0, moved);
    markDirty();
    renderQuestions();
  }

  function duplicate(index) {
    var copy = clone([questions[index]])[0];
    copy.id = "q-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 6);
    questions.splice(index + 1, 0, copy);
    openId = copy.id;
    markDirty();
    renderQuestions();
  }

  function remove(index) {
    if (!window.confirm("Vraag " + (index + 1) + " verwijderen?")) return;
    questions.splice(index, 1);
    markDirty();
    renderQuestions();
  }

  // --- formulier ----------------------------------------------------------

  function buildForm(question, titleNode) {
    var form = QuizUI.el("div", "qrow__form");
    var locked = !meta.editable;

    function field(labelText, control) {
      var wrap = QuizUI.el("div", "field");
      var label = QuizUI.el("label", null, labelText);
      wrap.appendChild(label);
      wrap.appendChild(control);
      return wrap;
    }

    function input(type, value, onChange, attrs) {
      var node = document.createElement("input");
      node.type = type;
      node.value = value === null || value === undefined ? "" : value;
      node.disabled = locked;
      Object.keys(attrs || {}).forEach(function (key) { node[key] = attrs[key]; });
      node.addEventListener("input", function () { onChange(node.value); markDirty(); });
      return node;
    }

    // Vraagtekst
    var text = document.createElement("textarea");
    text.value = question.text || "";
    text.rows = 2;
    text.disabled = locked;
    text.addEventListener("input", function () {
      question.text = text.value;
      titleNode.textContent = summarise(question);
      markDirty();
    });
    form.appendChild(field("Vraag", text));

    // Type + categorie
    var typeSelect = document.createElement("select");
    typeSelect.disabled = locked;
    [["multiple_choice", "Meerkeuze"], ["image", "Meerkeuze met afbeelding"], ["estimate", "Schatting"]]
      .forEach(function (pair) {
        var option = QuizUI.el("option", null, pair[1]);
        option.value = pair[0];
        if (question.type === pair[0]) option.selected = true;
        typeSelect.appendChild(option);
      });
    typeSelect.addEventListener("change", function () {
      question.type = typeSelect.value;
      if (question.type === "estimate" && question.correct_value === null) {
        question.correct_value = 0;
        question.tolerance = question.tolerance || 1;
      }
      markDirty();
      renderQuestions();
    });

    var catSelect = document.createElement("select");
    catSelect.disabled = locked;
    var known = meta.categories.slice();
    if (question.category && known.indexOf(question.category) === -1) known.unshift(question.category);
    known.forEach(function (name) {
      var option = QuizUI.el("option", null, name);
      option.value = name;
      if (question.category === name) option.selected = true;
      catSelect.appendChild(option);
    });
    catSelect.addEventListener("change", function () {
      question.category = catSelect.value;
      markDirty();
    });

    var typeRow = QuizUI.el("div", "frow");
    typeRow.appendChild(field("Type", typeSelect));
    typeRow.appendChild(field("Categorie", catSelect));
    form.appendChild(typeRow);

    // Antwoorden of schatting
    if (question.type === "estimate") {
      var estRow = QuizUI.el("div", "frow");
      estRow.appendChild(field("Juiste waarde", input("number", question.correct_value, function (value) {
        question.correct_value = value === "" ? null : parseFloat(value);
      }, { step: "any" })));
      estRow.appendChild(field("Eenheid", input("text", question.unit, function (value) {
        question.unit = value;
      }, { placeholder: "meter" })));
      estRow.appendChild(field("Tolerantie (± volle punten)", input("number", question.tolerance, function (value) {
        question.tolerance = value === "" ? null : parseFloat(value);
      }, { step: "any", min: "0" })));
      estRow.appendChild(field("Nulpunt (leeg = 6× tolerantie)", input("number", question.max_error, function (value) {
        question.max_error = value === "" ? null : parseFloat(value);
      }, { step: "any", min: "0" })));
      form.appendChild(estRow);
    } else {
      var opts = QuizUI.el("div", "opts");
      var groupName = "correct-" + question.id;
      for (var i = 0; i < 4; i++) {
        opts.appendChild(optionRow(question, i, groupName, locked));
      }
      form.appendChild(field("Antwoorden — duid het juiste aan", opts));
    }

    // Media
    var mediaRow = QuizUI.el("div", "frow");
    mediaRow.appendChild(field("Afbeelding (pad onder /static)", input("text", question.image, function (value) {
      question.image = value || null;
    }, { placeholder: "/static/img/sjorring-kruis.svg" })));
    mediaRow.appendChild(field("Of emoji", input("text", question.visual, function (value) {
      question.visual = value || null;
    }, { placeholder: "🔥" })));
    form.appendChild(mediaRow);

    // Tijd en punten
    var timeInput = input("number", question.time_limit, function (value) {
      question.time_limit = parseInt(value, 10) || meta.limits.default_time;
      updateTags();
    }, { min: meta.limits.min_time, max: meta.limits.max_time, step: 5 });

    var double = document.createElement("input");
    double.type = "checkbox";
    double.checked = question.points_multiplier >= 2;
    double.disabled = locked;
    double.addEventListener("change", function () {
      question.points_multiplier = double.checked ? 2.0 : 1.0;
      markDirty();
      updateTags();
    });
    var doubleLabel = QuizUI.el("label", "checkline");
    doubleLabel.appendChild(double);
    doubleLabel.appendChild(document.createTextNode("🔥 Dubbele punten"));

    var timeRow = QuizUI.el("div", "frow");
    timeRow.appendChild(field(
      "Bedenktijd (" + meta.limits.min_time + "–" + meta.limits.max_time + "s)", timeInput
    ));
    var doubleWrap = QuizUI.el("div", "field");
    doubleWrap.appendChild(QuizUI.el("span", "field__label", "Punten"));
    doubleWrap.appendChild(doubleLabel);
    timeRow.appendChild(doubleWrap);
    form.appendChild(timeRow);

    // Uitleg
    var explain = document.createElement("textarea");
    explain.value = question.explanation || "";
    explain.rows = 2;
    explain.disabled = locked;
    explain.placeholder = "Verschijnt na de onthulling op de beamer.";
    explain.addEventListener("input", function () {
      question.explanation = explain.value || null;
      markDirty();
    });
    form.appendChild(field("Uitleg", explain));

    function updateTags() {
      // De labels in de gesloten balk meteen mee laten lopen.
      var row = form.parentElement;
      if (!row) return;
      var tags = row.querySelector(".qrow__tags");
      if (!tags) return;
      tags.innerHTML = "";
      if (question.points_multiplier >= 2) tags.appendChild(QuizUI.el("span", "tag tag--double", "2×"));
      tags.appendChild(QuizUI.el("span", "tag", question.time_limit + "s"));
      if (question.type === "estimate") tags.appendChild(QuizUI.el("span", "tag", "schatting"));
    }

    return form;
  }

  function optionRow(question, index, groupName, locked) {
    var row = QuizUI.el("div", "optrow " + OPT_CLASS[index]);
    if (question.correct_index === index) row.classList.add("is-correct");

    var radio = document.createElement("input");
    radio.type = "radio";
    radio.name = groupName;
    radio.checked = question.correct_index === index;
    radio.disabled = locked;
    radio.addEventListener("change", function () {
      question.correct_index = index;
      markDirty();
      var siblings = row.parentElement.querySelectorAll(".optrow");
      Array.prototype.forEach.call(siblings, function (node, i) {
        node.classList.toggle("is-correct", i === index);
      });
    });

    var pick = QuizUI.el("label", "optrow__pick");
    pick.appendChild(radio);
    pick.appendChild(document.createTextNode(OPT_KEYS[index]));
    row.appendChild(pick);

    var text = document.createElement("input");
    text.type = "text";
    text.value = (question.options && question.options[index]) || "";
    text.placeholder = "Antwoord " + OPT_KEYS[index];
    text.disabled = locked;
    text.addEventListener("input", function () {
      if (!question.options) question.options = ["", "", "", ""];
      question.options[index] = text.value;
      markDirty();
    });
    row.appendChild(text);

    return row;
  }
})();

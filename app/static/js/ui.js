/* Handvol hulpjes die alle drie de schermen gebruiken. */

(function (global) {
  "use strict";

  var STATUS_LABELS = {
    connecting: "Verbinden…",
    reconnecting: "Opnieuw verbinden…",
    online: "Verbonden",
    offline: "Geen verbinding"
  };

  var toastTimer = null;

  var UI = {
    $: function (id) {
      return document.getElementById(id);
    },

    /* 12345 -> "12 345", zodat scores van ver leesbaar blijven. */
    fmt: function (value) {
      return String(Math.round(value || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
    },

    /* Eén toast tegelijk: een stapel meldingen leest niemand toch. */
    toast: function (message, kind) {
      var existing = document.querySelector(".toast");
      if (existing) existing.remove();
      if (toastTimer) global.clearTimeout(toastTimer);

      var node = document.createElement("div");
      node.className = "toast" + (kind ? " toast--" + kind : "");
      node.textContent = message;
      document.body.appendChild(node);
      toastTimer = global.setTimeout(function () {
        node.remove();
        toastTimer = null;
      }, 3000);
    },

    /* Bindt de verbindingsindicator aan de socketstatus. */
    statusBinder: function (dot, text) {
      return function (status) {
        dot.dataset.status = status === "reconnecting" ? "connecting" : status;
        text.textContent = STATUS_LABELS[status] || status;
      };
    },

    /* Zet tekst als kind-element, zonder innerHTML. */
    el: function (tag, className, text) {
      var node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined && text !== null) node.textContent = text;
      return node;
    },

    medal: function (rank) {
      return { 1: "🥇", 2: "🥈", 3: "🥉" }[rank] || null;
    }
  };

  global.QuizUI = UI;
})(window);

/* Gedeelde toegangspoort voor /present en /admin.

   Beide schermen doen exact hetzelfde vooraf: hostcode vragen, controleren,
   onthouden en dan pas de socket openen. Dat staat hier één keer. */

(function (global) {
  "use strict";

  var STORE_SECRET = "jhquiz.host_secret";

  function store(key, value) {
    try {
      if (value === undefined) return global.localStorage.getItem(key);
      if (value === null) global.localStorage.removeItem(key);
      else global.localStorage.setItem(key, value);
    } catch (err) { /* privémodus: dan typt de leiding de code gewoon opnieuw */ }
    return null;
  }

  /* options: { view, reveal[], onMessage, onStatus, onOpen } */
  function HostGate(options) {
    var self = this;
    this.view = options.view || "";
    this.reveal = options.reveal || [];
    this.onMessage = options.onMessage || function () {};
    this.onStatus = options.onStatus || function () {};
    this.onOpen = options.onOpen || function () {};
    this.secret = "";
    this.socket = null;

    this.gate = document.getElementById("gate");
    this.form = document.getElementById("gateForm");
    this.input = document.getElementById("gateInput");
    this.error = document.getElementById("gateError");

    this.form.addEventListener("submit", function (event) {
      event.preventDefault();
      var candidate = self.input.value.trim();
      if (!candidate) return;
      self.error.textContent = "";
      self.tryCode(candidate).then(function (ok) {
        if (!ok) {
          self.error.textContent = "Die code klopt niet.";
          self.input.select();
        }
      }).catch(function () {
        self.error.textContent = "Geen verbinding met de server.";
      });
    });
  }

  HostGate.prototype.start = function () {
    var self = this;
    var saved = store(STORE_SECRET);
    if (!saved) {
      this.open();
      return;
    }
    this.tryCode(saved).then(function (ok) {
      if (!ok) self.open();
    }).catch(function () {
      self.open();
    });
  };

  HostGate.prototype.open = function (message) {
    this.gate.hidden = false;
    this.reveal.forEach(function (node) { if (node) node.hidden = true; });
    this.error.textContent = message || "";
    this.input.focus();
  };

  HostGate.prototype.tryCode = function (candidate) {
    var self = this;
    return fetch("/api/host/check?secret=" + encodeURIComponent(candidate))
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data.ok) return false;
        self.secret = candidate;
        store(STORE_SECRET, candidate);
        self.gate.hidden = true;
        self.reveal.forEach(function (node) { if (node) node.hidden = false; });
        self.connect();
        return true;
      });
  };

  HostGate.prototype.connect = function () {
    var self = this;
    if (this.socket) this.socket.stop();
    this.socket = new QuizSocket({
      path: "/ws/host",
      query: "secret=" + encodeURIComponent(this.secret) +
             (this.view ? "&view=" + encodeURIComponent(this.view) : ""),
      onStatus: this.onStatus,
      onMessage: this.onMessage,
      onOpen: this.onOpen,
      onFatal: function (code, message) {
        if (code === 4403) store(STORE_SECRET, null);
        self.open(message);
      }
    });
    this.socket.connect();
  };

  HostGate.prototype.send = function (type, extra) {
    if (!this.socket) return false;
    var payload = { t: type };
    if (extra) {
      Object.keys(extra).forEach(function (key) { payload[key] = extra[key]; });
    }
    return this.socket.send(payload);
  };

  global.HostGate = HostGate;
})(window);

/* Kleine WebSocket-wrapper met automatische reconnect.
   Bewust minimaal: de server stuurt bij elke verandering een volledige snapshot,
   dus na een reconnect hoeven we niets bij te houden of te hervragen. */

(function (global) {
  "use strict";

  var FATAL_CODES = {
    4403: "Verkeerde hostcode."
  };

  function QuizSocket(options) {
    this.path = options.path;
    this.query = options.query || "";
    this.onMessage = options.onMessage || function () {};
    this.onStatus = options.onStatus || function () {};
    this.onOpen = options.onOpen || function () {};
    this.onFatal = options.onFatal || function () {};

    this.ws = null;
    this.attempt = 0;
    this.stopped = false;
    this.retryTimer = null;
    this.pingTimer = null;
    this._bindWake();
  }

  QuizSocket.prototype.url = function () {
    var proto = global.location.protocol === "https:" ? "wss:" : "ws:";
    return proto + "//" + global.location.host + this.path + (this.query ? "?" + this.query : "");
  };

  QuizSocket.prototype.connect = function () {
    if (this.stopped) return;
    this._clearRetry();
    this.onStatus(this.attempt === 0 ? "connecting" : "reconnecting");

    var self = this;
    var ws;
    try {
      ws = new WebSocket(this.url());
    } catch (err) {
      this._scheduleRetry();
      return;
    }
    this.ws = ws;

    ws.onopen = function () {
      self.attempt = 0;
      self.onStatus("online");
      self._startPing();
      self.onOpen();
    };

    ws.onmessage = function (event) {
      var data;
      try {
        data = JSON.parse(event.data);
      } catch (err) {
        return;
      }
      if (data && data.t === "pong") return;
      self.onMessage(data);
    };

    ws.onclose = function (event) {
      self._stopPing();
      if (self.ws === ws) self.ws = null;
      if (self.stopped) return;
      if (FATAL_CODES[event.code]) {
        self.stopped = true;
        self.onStatus("offline");
        self.onFatal(event.code, FATAL_CODES[event.code]);
        return;
      }
      self.onStatus("offline");
      self._scheduleRetry();
    };

    ws.onerror = function () {
      // onclose volgt altijd; daar zit de reconnectlogica.
    };
  };

  QuizSocket.prototype.send = function (payload) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    try {
      this.ws.send(JSON.stringify(payload));
      return true;
    } catch (err) {
      return false;
    }
  };

  QuizSocket.prototype.isOpen = function () {
    return !!this.ws && this.ws.readyState === WebSocket.OPEN;
  };

  QuizSocket.prototype.stop = function () {
    this.stopped = true;
    this._clearRetry();
    this._stopPing();
    if (this.ws) {
      try { this.ws.close(); } catch (err) { /* al dicht */ }
      this.ws = null;
    }
  };

  QuizSocket.prototype._scheduleRetry = function () {
    if (this.stopped || this.retryTimer) return;
    this.attempt += 1;
    // 0,4s -> 0,8s -> 1,6s ... afgetopt op 5s, met wat ruis zodat 30 gsm'en
    // niet allemaal op exact hetzelfde moment terugkomen.
    var delay = Math.min(5000, 400 * Math.pow(2, this.attempt - 1));
    delay += Math.random() * 250;
    var self = this;
    this.retryTimer = global.setTimeout(function () {
      self.retryTimer = null;
      self.connect();
    }, delay);
  };

  QuizSocket.prototype._clearRetry = function () {
    if (this.retryTimer) {
      global.clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
  };

  QuizSocket.prototype._startPing = function () {
    this._stopPing();
    var self = this;
    // Houdt de verbinding open door reverse proxies met een idle timeout heen.
    this.pingTimer = global.setInterval(function () {
      self.send({ t: "ping" });
    }, 20000);
  };

  QuizSocket.prototype._stopPing = function () {
    if (this.pingTimer) {
      global.clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  };

  QuizSocket.prototype._bindWake = function () {
    var self = this;
    function wake() {
      if (self.stopped || self.isOpen()) return;
      self.attempt = 0;
      self._clearRetry();
      self.connect();
    }
    // Gsm uit de zak, scherm weer aan, wifi terug: meteen opnieuw proberen.
    global.addEventListener("online", wake);
    global.addEventListener("pageshow", wake);
    global.document.addEventListener("visibilitychange", function () {
      if (!global.document.hidden) wake();
    });
  };

  global.QuizSocket = QuizSocket;
})(window);

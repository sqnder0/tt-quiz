/* Confetti voor de finale. Eén canvas, geen bibliotheken, stopt vanzelf. */

(function (global) {
  "use strict";

  var COLORS = ["#4ade80", "#ffd166", "#ff8a3d", "#38bdf8", "#e9dcc0", "#c084fc"];

  function Confetti(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.pieces = [];
    this.running = false;
    this.until = 0;
    var self = this;
    global.addEventListener("resize", function () { self.resize(); });
  }

  Confetti.prototype.resize = function () {
    var dpr = Math.min(global.devicePixelRatio || 1, 2);
    this.canvas.width = global.innerWidth * dpr;
    this.canvas.height = global.innerHeight * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };

  Confetti.prototype.burst = function (count, durationMs) {
    if (global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    this.canvas.hidden = false;
    this.resize();
    var width = global.innerWidth;
    for (var i = 0; i < (count || 140); i++) {
      this.pieces.push({
        x: Math.random() * width,
        y: -20 - Math.random() * global.innerHeight * 0.5,
        w: 6 + Math.random() * 8,
        h: 8 + Math.random() * 12,
        vx: -1.4 + Math.random() * 2.8,
        vy: 1.6 + Math.random() * 3.4,
        rot: Math.random() * Math.PI,
        vr: -0.12 + Math.random() * 0.24,
        color: COLORS[(Math.random() * COLORS.length) | 0]
      });
    }
    this.until = Date.now() + (durationMs || 6000);
    if (!this.running) {
      this.running = true;
      this._tick();
    }
  };

  Confetti.prototype.stop = function () {
    this.pieces = [];
    this.running = false;
    this.until = 0;
    this.canvas.hidden = true;
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
  };

  Confetti.prototype._tick = function () {
    var ctx = this.ctx;
    var height = global.innerHeight;
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    var adding = Date.now() < this.until;
    for (var i = this.pieces.length - 1; i >= 0; i--) {
      var p = this.pieces[i];
      p.x += p.vx;
      p.y += p.vy;
      p.vy += 0.035;
      p.rot += p.vr;
      if (p.y > height + 40) {
        if (adding) {
          // Blijf doorstromen tot de burst voorbij is.
          p.y = -30;
          p.x = Math.random() * global.innerWidth;
          p.vy = 1.6 + Math.random() * 3.4;
        } else {
          this.pieces.splice(i, 1);
          continue;
        }
      }
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
      ctx.restore();
    }

    if (this.pieces.length === 0) {
      this.running = false;
      this.canvas.hidden = true;
      return;
    }
    var self = this;
    global.requestAnimationFrame(function () { self._tick(); });
  };

  global.Confetti = Confetti;
})(window);

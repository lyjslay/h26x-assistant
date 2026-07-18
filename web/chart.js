/* 轻量自包含折线/阶梯图渲染器（无任何外部依赖，适配离线部署）。
 * 支持：多序列折线、阶梯线、散点标记、坐标轴、网格、hover 提示、高 DPI。
 */
(function (global) {
  "use strict";

  function niceNum(range, round) {
    const exp = Math.floor(Math.log10(range || 1));
    const f = (range || 1) / Math.pow(10, exp);
    let nf;
    if (round) nf = f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10;
    else nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
    return nf * Math.pow(10, exp);
  }

  function fmtBps(v) {
    if (v >= 1e6) return (v / 1e6).toFixed(2) + " Mbps";
    if (v >= 1e3) return (v / 1e3).toFixed(1) + " kbps";
    return Math.round(v) + " bps";
  }
  function fmtBits(v) {
    if (v >= 8e6) return (v / 8e6).toFixed(2) + " MB";
    if (v >= 8e3) return (v / 8e3).toFixed(1) + " KB";
    return v + " b";
  }

  // series: [{name,color,points:[{x,y,meta}],type:'line'|'step',width,dash}]
  // markers: [{x,y,color,label,meta}]
  function LineChart(canvas, opts) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.opts = opts || {};
    this.series = [];
    this.markers = [];
    this.pad = { l: 78, r: 20, t: 18, b: 40 };
    this._hover = null;
    this._bindHover();
  }

  LineChart.prototype.setData = function (series, markers) {
    this.series = series || [];
    this.markers = markers || [];
    this._computeBounds();
    this.draw();
  };

  LineChart.prototype._computeBounds = function () {
    let xmin = Infinity, xmax = -Infinity, ymin = 0, ymax = -Infinity;
    const all = this.series.concat([{ points: this.markers }]);
    for (const s of all) {
      for (const p of (s.points || [])) {
        if (p.x < xmin) xmin = p.x;
        if (p.x > xmax) xmax = p.x;
        if (p.y > ymax) ymax = p.y;
        if (p.y < ymin) ymin = p.y;
      }
    }
    if (!isFinite(xmin)) { xmin = 0; xmax = 1; }
    if (!isFinite(ymax)) { ymax = 1; }
    if (xmax === xmin) xmax = xmin + 1;
    ymax = ymax * 1.08 || 1;
    this.bounds = { xmin, xmax, ymin, ymax };
  };

  LineChart.prototype._resize = function () {
    const dpr = global.devicePixelRatio || 1;
    const rect = this.canvas.getBoundingClientRect();
    const w = rect.width || this.canvas.width;
    const h = this.canvas.clientHeight || this.canvas.height;
    this.canvas.width = w * dpr;
    this.canvas.height = h * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.W = w; this.H = h;
  };

  LineChart.prototype._x = function (x) {
    const { xmin, xmax } = this.bounds;
    return this.pad.l + (x - xmin) / (xmax - xmin) * (this.W - this.pad.l - this.pad.r);
  };
  LineChart.prototype._y = function (y) {
    const { ymin, ymax } = this.bounds;
    return this.H - this.pad.b - (y - ymin) / (ymax - ymin) * (this.H - this.pad.t - this.pad.b);
  };

  LineChart.prototype.draw = function () {
    this._resize();
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.W, this.H);
    this._drawAxes();
    for (const s of this.series) this._drawSeries(s);
    this._drawMarkers();
    if (this._hover) this._drawCrosshair();
  };

  LineChart.prototype._drawAxes = function () {
    const ctx = this.ctx;
    const { xmin, xmax, ymax } = this.bounds;
    ctx.strokeStyle = "#263049"; ctx.fillStyle = "#7d89a5";
    ctx.lineWidth = 1; ctx.font = "11px monospace";

    // Y 轴刻度
    const yTicks = 5;
    const yStep = niceNum(ymax / yTicks, true);
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (let v = 0; v <= ymax; v += yStep) {
      const y = this._y(v);
      ctx.strokeStyle = "#1b2238";
      ctx.beginPath(); ctx.moveTo(this.pad.l, y); ctx.lineTo(this.W - this.pad.r, y); ctx.stroke();
      ctx.fillText((this.opts.yFmt || fmtBps)(v), this.pad.l - 6, y);
    }
    // X 轴刻度
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const xTicks = 8;
    const xStep = niceNum((xmax - xmin) / xTicks, true) || 1;
    for (let v = Math.ceil(xmin / xStep) * xStep; v <= xmax; v += xStep) {
      const x = this._x(v);
      ctx.strokeStyle = "#161d30";
      ctx.beginPath(); ctx.moveTo(x, this.pad.t); ctx.lineTo(x, this.H - this.pad.b); ctx.stroke();
      ctx.fillStyle = "#7d89a5";
      ctx.fillText(String(Math.round(v)), x, this.H - this.pad.b + 6);
    }
    ctx.fillStyle = "#55617d"; ctx.textAlign = "center";
    ctx.fillText(this.opts.xLabel || "", (this.W) / 2, this.H - 14);
  };

  LineChart.prototype._drawSeries = function (s) {
    const ctx = this.ctx;
    const pts = s.points;
    if (!pts.length) return;
    ctx.strokeStyle = s.color || "#3b82f6";
    ctx.lineWidth = s.width || 1.5;
    if (s.dash) ctx.setLineDash(s.dash); else ctx.setLineDash([]);
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
      const x = this._x(pts[i].x), y = this._y(pts[i].y);
      if (i === 0) { ctx.moveTo(x, y); continue; }
      if (s.type === "step") {
        const px = this._x(pts[i].x0 != null ? pts[i].x0 : pts[i - 1].x);
        ctx.lineTo(this._x(pts[i].x0 != null ? pts[i].x0 : pts[i].x), this._y(pts[i - 1].y));
      }
      ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.setLineDash([]);

    if (s.fill) {
      ctx.lineTo(this._x(pts[pts.length - 1].x), this._y(0));
      ctx.lineTo(this._x(pts[0].x), this._y(0));
      ctx.closePath();
      ctx.fillStyle = s.fill;
      ctx.fill();
    }
  };

  LineChart.prototype._drawMarkers = function () {
    const ctx = this.ctx;
    for (const m of this.markers) {
      ctx.fillStyle = m.color || "#f59e0b";
      const x = this._x(m.x), y = this._y(m.y);
      ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
    }
  };

  LineChart.prototype._nearest = function (mx) {
    // 在主序列(第一条 line)上找最近点
    let best = null, bestd = Infinity, bestSeries = null;
    for (const s of this.series) {
      if (s.noHover) continue;
      for (const p of s.points) {
        const d = Math.abs(this._x(p.x) - mx);
        if (d < bestd) { bestd = d; best = p; bestSeries = s; }
      }
    }
    return best ? { p: best, s: bestSeries } : null;
  };

  LineChart.prototype._bindHover = function () {
    const self = this;
    const tip = this.opts.tooltip;
    this.canvas.addEventListener("mousemove", function (e) {
      const rect = self.canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const hit = self._nearest(mx);
      self._hover = hit ? { x: mx, hit } : null;
      self.draw();
      if (tip && hit) {
        tip.style.display = "block";
        tip.style.left = (self._x(hit.p.x) + 12) + "px";
        tip.style.top = (self._y(hit.p.y) - 10) + "px";
        tip.innerHTML = (self.opts.tipFmt ? self.opts.tipFmt(hit.p, hit.s) : "");
      } else if (tip) { tip.style.display = "none"; }
    });
    this.canvas.addEventListener("mouseleave", function () {
      self._hover = null; self.draw();
      if (tip) tip.style.display = "none";
    });
    this.canvas.addEventListener("click", function (e) {
      const rect = self.canvas.getBoundingClientRect();
      const hit = self._nearest(e.clientX - rect.left);
      if (hit && self.opts.onClick) self.opts.onClick(hit.p, hit.s);
    });
  };

  LineChart.prototype._drawCrosshair = function () {
    const ctx = this.ctx;
    const p = this._hover.hit.p;
    const x = this._x(p.x), y = this._y(p.y);
    ctx.strokeStyle = "#3b82f688"; ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, this.pad.t); ctx.lineTo(x, this.H - this.pad.b); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#fff";
    ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fill();
  };

  global.LineChart = LineChart;
  global.ChartFmt = { fmtBps: fmtBps, fmtBits: fmtBits };
})(window);

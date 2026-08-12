/* Minimal synchronous canvas line charts - no dependencies, no deferred
   rendering. Every draw() paints immediately, so playback can never race an
   init commit (the bug that motivated replacing the previous chart lib). */

"use strict";

function niceTicks(lo, hi, n = 5) {
  if (!isFinite(lo) || !isFinite(hi)) return [];
  if (lo === hi) { hi = lo + 1; }
  const span = hi - lo;
  const step0 = span / n;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || mag * 10;
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) ticks.push(v);
  return ticks;
}

const DAY_FMT = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "2-digit", timeZone: "UTC" });

/* el: container. cfg: { x: seconds[], series: [{data, color, label, width?,
   dash?, right?}], band?: [t0,t1], height? }  Returns { draw(playheadIdx) }. */
function lineChart(el, title, cfg) {
  const wrap = document.createElement("div");
  wrap.className = "chart";
  const h = document.createElement("h3");
  h.textContent = title;
  wrap.appendChild(h);
  const canvas = document.createElement("canvas");
  wrap.appendChild(canvas);
  const legend = document.createElement("div");
  legend.className = "legend";
  for (const s of cfg.series) {
    const item = document.createElement("span");
    item.innerHTML = `<i style="background:${s.color}"></i>${s.label}`;
    legend.appendChild(item);
  }
  wrap.appendChild(legend);
  el.appendChild(wrap);

  const cssW = Math.min(1120, el.clientWidth - 28) || 900;
  const cssH = cfg.height || 200;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  canvas.style.width = cssW + "px";
  canvas.style.height = cssH + "px";
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const M = { l: 58, r: cfg.series.some((s) => s.right) ? 52 : 14, t: 8, b: 22 };
  const plotW = cssW - M.l - M.r;
  const plotH = cssH - M.t - M.b;
  const x = cfg.x;
  const x0 = x[0], x1 = x[x.length - 1];
  const X = (t) => M.l + ((t - x0) / (x1 - x0)) * plotW;

  function extent(side) {
    let lo = Infinity, hi = -Infinity;
    for (const s of cfg.series) {
      if (!!s.right !== side) continue;
      for (const v of s.data) {
        if (v == null || !isFinite(v)) continue;
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
    }
    if (lo === Infinity) return [0, 1];
    if (lo > 0 && lo < hi * 0.3) lo = 0;
    return lo === hi ? [lo - 1, hi + 1] : [lo, hi];
  }
  const [lLo, lHi] = extent(false);
  const [rLo, rHi] = extent(true);
  const Y = (v, right) => {
    const [lo, hi] = right ? [rLo, rHi] : [lLo, lHi];
    return M.t + plotH - ((v - lo) / (hi - lo)) * plotH;
  };

  function draw(playheadIdx = null) {
    ctx.clearRect(0, 0, cssW, cssH);
    ctx.font = "11px system-ui";

    if (cfg.band) {
      ctx.fillStyle = "rgba(196,78,82,0.10)";
      const bx0 = Math.max(M.l, X(cfg.band[0]));
      const bx1 = Math.min(cssW - M.r, X(cfg.band[1]));
      if (bx1 > bx0) ctx.fillRect(bx0, M.t, bx1 - bx0, plotH);
    }

    // y grid + labels (left)
    ctx.fillStyle = "#667";
    ctx.strokeStyle = "#e5e5e5";
    ctx.lineWidth = 1;
    ctx.textAlign = "right";
    for (const v of niceTicks(lLo, lHi)) {
      const y = Y(v, false);
      ctx.beginPath(); ctx.moveTo(M.l, y); ctx.lineTo(cssW - M.r, y); ctx.stroke();
      ctx.fillText(Math.round(v).toLocaleString(), M.l - 6, y + 3);
    }
    if (cfg.series.some((s) => s.right)) {
      ctx.textAlign = "left";
      for (const v of niceTicks(rLo, rHi)) {
        ctx.fillText((Math.round(v * 100) / 100).toLocaleString(), cssW - M.r + 6, Y(v, true) + 3);
      }
    }
    // x labels at UTC midnights
    ctx.textAlign = "center";
    ctx.fillStyle = "#667";
    for (let i = 0; i < x.length; i++) {
      if (x[i] % 86400 === 0) {
        const px = X(x[i]);
        ctx.strokeStyle = "#efefef";
        ctx.beginPath(); ctx.moveTo(px, M.t); ctx.lineTo(px, M.t + plotH); ctx.stroke();
        ctx.fillText(DAY_FMT.format(new Date(x[i] * 1000)), px, cssH - 7);
      }
    }

    // series
    for (const s of cfg.series) {
      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.width || 1.6;
      ctx.setLineDash(s.dash || []);
      ctx.beginPath();
      let pen = false;
      for (let i = 0; i < x.length; i++) {
        const v = s.data[i];
        if (v == null || !isFinite(v)) { pen = false; continue; }
        const px = X(x[i]), py = Y(v, !!s.right);
        if (pen) ctx.lineTo(px, py); else { ctx.moveTo(px, py); pen = true; }
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // playhead
    if (playheadIdx != null && playheadIdx < x.length) {
      const px = X(x[playheadIdx]);
      ctx.strokeStyle = "#111";
      ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.moveTo(px, M.t); ctx.lineTo(px, M.t + plotH); ctx.stroke();
    }
  }

  draw(0);
  return { draw };
}

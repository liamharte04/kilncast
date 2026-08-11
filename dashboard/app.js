/* Kilncast dashboard - static, no build step.
   Serve with: python -m http.server 8777 --directory dashboard  */

"use strict";

const $ = (sel) => document.querySelector(sel);
const main = $("#main");
let manifest = null;
let player = null; // active playback interval

const fetchJson = async (path) => (await fetch(path, { cache: "no-store" })).json();

const fmtKg = (v) => `${Math.round(v).toLocaleString()} kg`;

function stopPlayer() {
  if (player) { clearInterval(player); player = null; }
}

/* ---------------- headline strip (computed from manifest, never hardcoded) */
function renderHeadline() {
  // pooled ONLY over site-months where baseline, mpc and oracle all exist -
  // partial regeneration must never produce a nonsense headline
  const cells = {};
  for (const r of manifest.sweep) {
    if (r.methane_kg == null) continue;
    (cells[`${r.site}|${r.month}`] ??= {})[r.controller] = r.methane_kg;
  }
  let base = 0, mpc = 0, oracle = 0;
  const sites = new Set();
  for (const [k, c] of Object.entries(cells)) {
    if (c.baseline != null && c.mpc != null && c.oracle != null) {
      base += c.baseline; mpc += c.mpc; oracle += c.oracle;
      sites.add(k.split("|")[0]);
    }
  }
  if (!base || !mpc || mpc <= base) return;
  const uplift = (100 * (mpc - base) / base).toFixed(1);
  const gap = (100 * (mpc - base) / (oracle - base)).toFixed(0);
  $("#headline").innerHTML = `
    <div class="stat"><b>+${uplift}%</b><span>methane vs run-when-sunny, same hardware</span></div>
    <div class="stat"><b>${gap}%</b><span>of the perfect-forecast gap closed</span></div>
    <div class="stat"><b>${sites.size} sites x 4 seasons</b><span>real 2025 weather, real as-issued forecasts</span></div>`;
}

/* ---------------- chart helpers */
function playheadPlugin(getIdx) {
  return {
    hooks: {
      draw: (u) => {
        const idx = getIdx();
        if (idx == null) return;
        const x = u.valToPos(u.data[0][idx], "x", true);
        u.ctx.save();
        u.ctx.strokeStyle = "#111";
        u.ctx.lineWidth = 1.5;
        u.ctx.beginPath();
        u.ctx.moveTo(x, u.bbox.top);
        u.ctx.lineTo(x, u.bbox.top + u.bbox.height);
        u.ctx.stroke();
        u.ctx.restore();
      },
    },
  };
}

function bandPlugin(getBand, color = "rgba(196,78,82,0.12)") {
  return {
    hooks: {
      drawClear: (u) => {
        const band = getBand();
        if (!band) return;
        const [t0, t1] = band;
        const x0 = u.valToPos(t0, "x", true);
        const x1 = u.valToPos(t1, "x", true);
        u.ctx.save();
        u.ctx.fillStyle = color;
        u.ctx.fillRect(x0, u.bbox.top, x1 - x0, u.bbox.height);
        u.ctx.restore();
      },
    },
  };
}

function makeChart(el, title, series, data, opts = {}) {
  const wrap = document.createElement("div");
  wrap.className = "chart";
  wrap.innerHTML = `<h3>${title}</h3>`;
  el.appendChild(wrap);
  const u = new uPlot({
    width: Math.min(1120, el.clientWidth - 26),
    height: opts.height || 190,
    series: [{}, ...series],
    axes: [
      {},
      { size: 55 },
      ...(opts.rightAxis ? [{ side: 1, scale: "r", size: 50, grid: { show: false } }] : []),
    ],
    scales: opts.rightAxis ? { r: { auto: true } } : {},
    plugins: opts.plugins || [],
    legend: { live: false },
  }, data, wrap);
  return u;
}

const toEpoch = (ep) => ep.time.map((t) => new Date(t).getTime() / 1000);

function slice(ep, startIso, days) {
  const t0 = new Date(startIso).getTime() / 1000;
  const t1 = t0 + days * 86400;
  const ts = toEpoch(ep);
  const i0 = ts.findIndex((t) => t >= t0);
  let i1 = ts.findIndex((t) => t >= t1);
  if (i1 < 0) i1 = ts.length;
  const cut = (arr) => arr.slice(i0, i1);
  const out = { time: cut(ts) };
  for (const k of Object.keys(ep)) if (k !== "time") out[k] = cut(ep[k]);
  // re-zero cumulative methane inside the window
  const base0 = ep.ch4_cum[i0 > 0 ? i0 - 1 : 0];
  out.ch4_cum = out.ch4_cum.map((v) => +(v - (i0 > 0 ? base0 : 0)).toFixed(1));
  return out;
}

/* ---------------- storm view */
async function viewStorm() {
  stopPlayer();
  main.innerHTML = "<p>loading storm week...</p>";
  const storm = manifest.storm;
  if (!storm) { main.innerHTML = "<p>no storm week selected - run scripts/find_storm_week.py</p>"; return; }
  const [mpcEp, baseEp] = await Promise.all([
    fetchJson(`data/ep_${storm.key}_mpc.json`),
    fetchJson(`data/ep_${storm.key}_baseline.json`),
  ]);
  const days = 7;
  const m = slice(mpcEp, storm.week_start, days);
  const b = slice(baseEp, storm.week_start, days);
  const n = m.time.length;
  let idx = 0;

  main.innerHTML = `
    <div class="controls">
      <button id="play">&#9654; Play</button>
      <input id="scrub" type="range" min="0" max="${n - 1}" value="0" style="flex:1">
      <span id="clock" style="min-width:190px"></span>
    </div>
    <div class="counters">
      <div class="counter base"><b id="cbase">0 kg</b><span>run-when-sunny<span class="stalled" id="stall">STALLED</span></span></div>
      <div class="counter mpc"><b id="cmpc">0 kg</b><span>reads the forecast</span></div>
    </div>
    <div id="charts"></div>
    <p style="color:#667;font-size:13px">${storm.site} - week of ${storm.week_start}.
    The red dashed line is what the 7-day-ahead forecast promised; the grey line is what the sky delivered.</p>`;

  const charts = $("#charts");
  const getIdx = () => idx;
  const plugins = [playheadPlugin(getIdx)];

  const fc = storm.forecast || null;
  const fcSeries = fc ? slice({ time: fc.time, lead1: fc.lead1, lead7: fc.lead7, ch4_cum: fc.lead1 }, storm.week_start, days) : null;

  const skyData = [m.time, m.ghi, fcSeries ? fcSeries.lead1 : null, fcSeries ? fcSeries.lead7 : null]
    .filter((s) => s !== null);
  const skySeries = [
    { label: "actual W/m2", stroke: "#555", width: 1.5 },
    ...(fcSeries ? [
      { label: "forecast 1d ahead", stroke: "#3b7dd8", width: 1, dash: [4, 4] },
      { label: "forecast 7d ahead", stroke: "#c44e52", width: 1, dash: [6, 4] },
    ] : []),
  ];
  const uSky = makeChart(charts, "the sky: forecast vs delivered", skySeries, skyData, { plugins });

  const uCh4 = makeChart(charts, "cumulative methane (kg)", [
    { label: "baseline", stroke: "#c44e52", width: 2 },
    { label: "forecast MPC", stroke: "#2a7e43", width: 2 },
  ], [m.time, b.ch4_cum, m.ch4_cum], { plugins });

  const uStores = makeChart(charts, "MPC stores: CaO silo + H2 tank (kg), kiln temperature (right)", [
    { label: "silo kg", stroke: "#8a6d3b", width: 1.5 },
    { label: "H2 kg", stroke: "#3b7dd8", width: 1.5 },
    { label: "kiln temp (frac)", stroke: "#e8710a", width: 1.5, scale: "r" },
  ], [m.time, m.silo_kg, m.h2_kg, m.kiln_temp], { plugins, rightAxis: true });

  const charts3 = [uSky, uCh4, uStores];
  const update = () => {
    $("#scrub").value = idx;
    $("#clock").textContent = new Date(m.time[idx] * 1000).toUTCString().slice(0, 22);
    $("#cbase").textContent = fmtKg(b.ch4_cum[idx]);
    $("#cmpc").textContent = fmtKg(m.ch4_cum[idx]);
    const recent = b.ch4_cum[idx] - b.ch4_cum[Math.max(0, idx - 12)];
    $("#stall").style.visibility = idx > 24 && recent < 0.5 ? "visible" : "hidden";
    charts3.forEach((u) => u.redraw());
  };
  $("#scrub").oninput = (e) => { idx = +e.target.value; update(); };
  $("#play").onclick = () => {
    if (player) { stopPlayer(); $("#play").innerHTML = "&#9654; Play"; return; }
    if (idx >= n - 1) idx = 0;
    $("#play").textContent = "Pause";
    player = setInterval(() => {
      idx = Math.min(idx + 1, n - 1);
      update();
      if (idx >= n - 1) { stopPlayer(); $("#play").innerHTML = "&#9654; Replay"; }
    }, 70);
  };
  update();
}

/* ---------------- fault view */
async function viewFault() {
  stopPlayer();
  const faults = manifest.faults.filter((f) => f.scenario !== "none");
  if (!faults.length) { main.innerHTML = "<p>no fault runs exported yet</p>"; return; }
  main.innerHTML = `
    <div class="controls">
      <label>scenario <select id="fsel">${faults.map((f, i) =>
        `<option value="${i}">${f.scenario} - ${f.controller} (${f.month})</option>`).join("")}
      </select></label>
    </div>
    <div id="fbody"></div>`;
  const render = async (i) => {
    const f = faults[i];
    const body = $("#fbody");
    body.innerHTML = "<p>loading...</p>";
    const ref = manifest.faults.find((x) => x.scenario === "none"
      && x.controller === f.controller && x.month === f.month);
    const [ep, refEp] = await Promise.all([
      fetchJson(`data/ep_${f.key}.json`),
      ref ? fetchJson(`data/ep_${ref.key}.json`) : null,
    ]);
    body.innerHTML = "";
    const ts = toEpoch(ep);
    const [d0, d1] = f.fault_window_days || [10, 13];
    const band = [ts[0] + d0 * 86400, ts[0] + d1 * 86400];
    const plugins = [bandPlugin(() => band)];
    makeChart(body, `cumulative methane - fault window shaded (${f.scenario})`, [
      { label: "with fault", stroke: "#c44e52", width: 2 },
      ...(refEp ? [{ label: "no fault", stroke: "#2a7e43", width: 1.5, dash: [5, 4] }] : []),
    ], [ts, ep.ch4_cum, ...(refEp ? [refEp.ch4_cum] : [])], { plugins });
    makeChart(body, "kiln temperature + silo level", [
      { label: "kiln temp", stroke: "#e8710a", width: 1.5 },
      { label: "silo kg", stroke: "#8a6d3b", width: 1.5, scale: "r" },
    ], [ts, ep.kiln_temp, ep.silo_kg], { plugins, rightAxis: true });
    if (f.detection_events && f.detection_events.length) {
      body.insertAdjacentHTML("beforeend",
        `<div class="events"><b>autonomy log</b><ul>${
          f.detection_events.map((e) => `<li>${e}</li>`).join("")}</ul>
          <span style="color:#667">recovery uses a repair notification - a stated limitation, not autonomous re-probing</span></div>`);
    }
  };
  $("#fsel").onchange = (e) => render(+e.target.value);
  const def = faults.findIndex((f) => f.scenario === "kiln_heater" && f.controller === "mpc");
  $("#fsel").value = String(Math.max(0, def));
  render(Math.max(0, def));
}

/* ---------------- browse view */
async function viewBrowse() {
  stopPlayer();
  const eps = manifest.episodes;
  main.innerHTML = `
    <div class="controls"><label>episode <select id="esel">${eps.map((e, i) =>
      `<option value="${i}">${e.site} ${e.month} - ${e.controller} (${Math.round(e.methane_kg)} kg)</option>`).join("")}
    </select></label></div>
    <div id="ebody"></div>`;
  const render = async (i) => {
    const meta = eps[i];
    const body = $("#ebody");
    body.innerHTML = "<p>loading...</p>";
    const ep = await fetchJson(`data/ep_${meta.key}.json`);
    body.innerHTML = "";
    const ts = toEpoch(ep);
    makeChart(body, "solar available vs used (kW)", [
      { label: "solar", stroke: "#e8b117", width: 1 },
      { label: "used", stroke: "#444", width: 1 },
    ], [ts, ep.solar_kw, ep.used_kw]);
    makeChart(body, "cumulative methane (kg)", [
      { label: "kg", stroke: "#2a7e43", width: 2 },
    ], [ts, ep.ch4_cum]);
    makeChart(body, "stores + kiln temperature", [
      { label: "silo kg", stroke: "#8a6d3b", width: 1 },
      { label: "H2 kg", stroke: "#3b7dd8", width: 1 },
      { label: "kiln temp", stroke: "#e8710a", width: 1, scale: "r" },
    ], [ts, ep.silo_kg, ep.h2_kg, ep.kiln_temp], { rightAxis: true });
  };
  $("#esel").onchange = (e) => render(+e.target.value);
  render(0);
}

/* ---------------- boot */
const VIEWS = { storm: viewStorm, fault: viewFault, browse: viewBrowse };
document.querySelectorAll("nav button").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll("nav button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    VIEWS[btn.dataset.view]();
  };
});

(async () => {
  manifest = await fetchJson("data/manifest.json");
  renderHeadline();
  viewStorm();
})();

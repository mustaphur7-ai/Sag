const $ = s => document.querySelector(s);
const f = (n, d = 2) => n == null ? "–" : Number(n).toLocaleString("en-US", { maximumFractionDigits: d });
const M = n => Math.abs(n) >= 1e6 ? (n / 1e6).toFixed(2) + "M" : Math.abs(n) >= 1e3 ? (n / 1e3).toFixed(0) + "K" : String(n);
const BASE = "https://data-api.binance.vision/api/v3";

async function main() {
  const res = await fetch("data/levels.json?_=" + Date.now());
  if (!res.ok) throw new Error("data/levels.json not found (HTTP " + res.status + ")");
  const L = JSON.parse((await res.text()).replace(/\bNaN\b|-?Infinity\b/g, "null"));
  const off = L.paxg_offset || 0;

  // header + frozen status
  const reg = $("#regime");
  reg.textContent = (L.regime_now === "positive" ? "Positive" : "Negative") + " gamma";
  reg.className = "badge " + (L.regime_now === "positive" ? "pos" : "neg");
  const fz = $("#frozen"), until = new Date(L.valid_until);
  const tick = () => {
    const s = Math.max(0, Math.floor((until - Date.now()) / 1000));
    const p = n => String(n).padStart(2, "0");
    fz.textContent = `Frozen · next update in ${p(Math.floor(s / 3600))}:${p(Math.floor(s % 3600 / 60))}:${p(s % 60)}`;
    if (until - Date.now() < -6 * 3600e3) { $("#warn").hidden = false; $("#warn").textContent = "Levels are older than expected. The daily run may have failed."; }
  };
  tick(); setInterval(tick, 1000);
  if (L.warning) { $("#warn").hidden = false; $("#warn").textContent = L.warning; }

  const stats = [
    ["Gamma flip", L.flip_xau], ["Call wall", L.call_wall_xau], ["Put wall", L.put_wall_xau],
    ["25Δ skew", L.skew_25d], ["Total GEX", L.total_gex == null ? null : M(L.total_gex)],
    ["Built", new Date(L.generated_at).toUTCString().slice(5, 22) + " UTC"],
  ];
  $("#stats").innerHTML = stats.map(([a, b]) => `<div><b>${typeof b === "number" ? f(b) : b ?? "–"}</b><span>${a}</span></div>`).join("");

  // chart
  const chart = LightweightCharts.createChart($("#chart"), {
    layout: { background: { color: "#0b0e13" }, textColor: "#7d8aa0" },
    grid: { vertLines: { color: "#121722" }, horzLines: { color: "#121722" } },
    rightPriceScale: { borderColor: "#1f2736" }, timeScale: { borderColor: "#1f2736", timeVisible: true },
    autoSize: true,
  });
  const cs = chart.addCandlestickSeries({ upColor: "#26a671", downColor: "#e0525a", borderVisible: false, wickUpColor: "#26a671", wickDownColor: "#e0525a" });
  const line = (price, color, title, style = 0) => cs.createPriceLine({ price, color, title, lineStyle: style, lineWidth: 1, axisLabelVisible: true });
  if (L.call_wall_xau) line(L.call_wall_xau, "#e0525a", "Call wall", 2);
  if (L.put_wall_xau) line(L.put_wall_xau, "#26a671", "Put wall", 2);
  if (L.flip_xau) line(L.flip_xau, "#e3b341", "Flip", 0);
  L.table.forEach(r => line(r.xau, r.net_gex >= 0 ? "#5b8def" : "#a672e0", `${r.strike}`, 1));
  L.key_levels.forEach(k => line(k.level, "#7d8aa0", k.name, 3));

  const mapK = k => ({ time: Math.floor(k[0] / 1000), open: +k[1] + off, high: +k[2] + off, low: +k[3] + off, close: +k[4] + off });
  let last = null;
  async function loadCandles() {
    const k = await (await fetch(`${BASE}/klines?symbol=PAXGUSDT&interval=15m&limit=500`)).json();
    const data = k.map(mapK); cs.setData(data); last = data[data.length - 1]; chart.timeScale().fitContent(); paint(last.close);
  }
  function paint(p) {
    $("#price").textContent = f(p);
    const d = p - L.xau_spot;
    $("#chg").textContent = `${d >= 0 ? "+" : ""}${f(d)} since build`;
    $("#chg").style.color = d >= 0 ? "var(--up)" : "var(--dn)";
    document.querySelectorAll(".row").forEach(r => r.classList.toggle("near", Math.abs(+r.dataset.x - p) === Math.min(...[...document.querySelectorAll(".row")].map(q => Math.abs(+q.dataset.x - p)))));
  }
  async function poll() {
    try {
      const k = await (await fetch(`${BASE}/klines?symbol=PAXGUSDT&interval=15m&limit=2`)).json();
      k.map(mapK).forEach(c => cs.update(c)); paint(mapK(k[k.length - 1]).close);
    } catch (e) { /* keep last price */ }
  }
  try {
    await loadCandles();
    setInterval(poll, 15000);
  } catch (e) {
    // Live feed blocked or down: draw the candles frozen at build time.
    const fc = (L.candles || []).map(c => ({ time: c.t, open: c.o, high: c.h, low: c.l, close: c.c }));
    if (fc.length) { cs.setData(fc); chart.timeScale().fitContent(); }
    paint(L.xau_spot);
    $("#chg").textContent = "Live price unavailable, showing price at build";
  }

  // ladder
  const mx = Math.max(...L.ladder.map(r => Math.abs(r.net_gex))) || 1;
  $("#ladder").innerHTML = L.ladder.map(r =>
    `<div class="row" data-x="${r.xau}"><i style="width:${Math.abs(r.net_gex) / mx * 100}%;background:${r.net_gex >= 0 ? "var(--up)" : "var(--dn)"}"></i><span>${f(r.xau, 0)}</span><span>${M(r.net_gex)}</span></div>`).join("");

  // single combined table
  $("#tbl tbody").innerHTML = L.table.map(r => {
    const c = r.call === "BUY" ? "buy" : r.call === "SELL" ? "sell" : "none";
    return `<tr><td>${f(r.strike)}</td><td>${f(r.xau)}</td><td>${r.role}</td><td>${f(r.oi, 0)}</td>
      <td style="color:${r.net_gex >= 0 ? "var(--up)" : "var(--dn)"}">${M(r.net_gex)}</td>
      <td>${r.regime === "positive" ? "Positive" : "Negative"}</td><td>${r.iv_skew == null ? "–" : (r.iv_skew > 0 ? "+" : "") + r.iv_skew}</td>
      <td class="${c}">${r.call}</td><td>${r.lean} ${r.confidence}%</td><td>${r.invalidation}</td></tr>`;
  }).join("");
  $("#note").textContent = L.note + " Not financial advice.";
}
main().catch(e => {
  const w = $("#warn");
  w.hidden = false;
  w.textContent = "Could not load levels: " + e.message;
});

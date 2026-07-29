/** Lightweight canvas charts for hero + signal detail. */
window.ATO = window.ATO || {};

ATO.drawHeroMarket = function drawHeroMarket(canvas) {
  const ctx = canvas.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const resize = () => {
    const { clientWidth: w, clientHeight: h } = canvas;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    paint(w, h);
  };

  const seed = Array.from({ length: 48 }, (_, i) => {
    const base = 100 + Math.sin(i / 4) * 8 + i * 0.35;
    const open = base + (Math.random() - 0.5) * 3;
    const close = open + (Math.random() - 0.45) * 4;
    const high = Math.max(open, close) + Math.random() * 2;
    const low = Math.min(open, close) - Math.random() * 2;
    return { open, high, low, close };
  });

  function paint(w, h) {
    ctx.clearRect(0, 0, w, h);
    const pad = { t: 40, r: 24, b: 48, l: 24 };
    const plotW = w - pad.l - pad.r;
    const plotH = h - pad.t - pad.b;
    const lows = seed.map((c) => c.low);
    const highs = seed.map((c) => c.high);
    const min = Math.min(...lows);
    const max = Math.max(...highs);
    const y = (v) => pad.t + ((max - v) / (max - min)) * plotH;
    const step = plotW / seed.length;

    // horizon wash
    const g = ctx.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, "rgba(11,110,110,0.10)");
    g.addColorStop(0.55, "rgba(233,238,242,0)");
    g.addColorStop(1, "rgba(184,67,31,0.08)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);

    // guide lines
    ctx.strokeStyle = "rgba(16,20,26,0.08)";
    ctx.lineWidth = 1;
    for (let i = 0; i < 6; i++) {
      const yy = pad.t + (plotH / 5) * i;
      ctx.beginPath();
      ctx.moveTo(pad.l, yy);
      ctx.lineTo(w - pad.r, yy);
      ctx.stroke();
    }

    seed.forEach((c, i) => {
      const x = pad.l + i * step + step * 0.5;
      const up = c.close >= c.open;
      ctx.strokeStyle = up ? "#0b6e6e" : "#b8431f";
      ctx.fillStyle = up ? "rgba(11,110,110,0.55)" : "rgba(184,67,31,0.55)";
      ctx.beginPath();
      ctx.moveTo(x, y(c.high));
      ctx.lineTo(x, y(c.low));
      ctx.stroke();
      const top = y(Math.max(c.open, c.close));
      const bot = y(Math.min(c.open, c.close));
      ctx.fillRect(x - step * 0.28, top, step * 0.56, Math.max(2, bot - top));
    });

    // entry / tp ghost levels
    const mid = seed[Math.floor(seed.length * 0.62)];
    const entry = mid.close;
    const levels = [
      { v: entry, color: "#10141a", label: "ENTRY" },
      { v: entry * 0.97, color: "#b8431f", label: "SL" },
      { v: entry * 1.03, color: "#0b6e6e", label: "TP1" },
      { v: entry * 1.06, color: "#0b6e6e", label: "TP2" },
    ];
    levels.forEach((lv) => {
      const yy = y(lv.v);
      ctx.strokeStyle = lv.color;
      ctx.setLineDash([5, 5]);
      ctx.beginPath();
      ctx.moveTo(pad.l + plotW * 0.45, yy);
      ctx.lineTo(w - pad.r, yy);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = lv.color;
      ctx.font = "600 11px IBM Plex Mono, monospace";
      ctx.fillText(lv.label, w - pad.r - 36, yy - 6);
    });
  }

  resize();
  window.addEventListener("resize", resize);
};

ATO.drawSignalChart = function drawSignalChart(canvas, signal) {
  const ctx = canvas.getContext("2d");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const prices = expandSpark(signal);

  function paint() {
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const pad = { t: 28, r: 72, b: 28, l: 12 };
    const all = [...prices, signal.entry, signal.sl, signal.tp1, signal.tp2, signal.tp3];
    const min = Math.min(...all) * 0.995;
    const max = Math.max(...all) * 1.005;
    const plotW = w - pad.l - pad.r;
    const plotH = h - pad.t - pad.b;
    const xAt = (i) => pad.l + (i / (prices.length - 1)) * plotW;
    const yAt = (v) => pad.t + ((max - v) / (max - min)) * plotH;

    // area
    ctx.beginPath();
    prices.forEach((p, i) => {
      const x = xAt(i);
      const y = yAt(p);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.lineTo(xAt(prices.length - 1), pad.t + plotH);
    ctx.lineTo(xAt(0), pad.t + plotH);
    ctx.closePath();
    const fill = ctx.createLinearGradient(0, pad.t, 0, pad.t + plotH);
    const tone = signal.side === "long" ? "11,110,110" : "184,67,31";
    fill.addColorStop(0, `rgba(${tone},0.22)`);
    fill.addColorStop(1, `rgba(${tone},0)`);
    ctx.fillStyle = fill;
    ctx.fill();

    // price line with draw-on feel via dash offset animation handled outside
    ctx.beginPath();
    prices.forEach((p, i) => {
      const x = xAt(i);
      const y = yAt(p);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = signal.side === "long" ? "#0b6e6e" : "#b8431f";
    ctx.lineWidth = 2.25;
    ctx.stroke();

    const levels = [
      { key: "Entry", v: signal.entry, c: "#10141a" },
      { key: "SL", v: signal.sl, c: "#b8431f" },
      { key: "TP1", v: signal.tp1, c: "#0b6e6e" },
      { key: "TP2", v: signal.tp2, c: "#0b6e6e" },
      { key: "TP3", v: signal.tp3, c: "#0b6e6e" },
    ];
    levels.forEach((lv) => {
      const y = yAt(lv.v);
      ctx.strokeStyle = lv.c;
      ctx.lineWidth = 1.25;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(w - pad.r, y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = lv.c;
      ctx.font = "600 11px IBM Plex Mono, monospace";
      ctx.fillText(`${lv.key} ${ATO.fmt(lv.v, lv.v < 1 ? 4 : 3)}`, w - pad.r + 8, y + 4);
    });
  }

  paint();
  window.addEventListener("resize", paint);
};

function expandSpark(signal) {
  const s = signal.spark.slice();
  // densify for nicer line
  const out = [];
  for (let i = 0; i < s.length - 1; i++) {
    out.push(s[i]);
    out.push((s[i] + s[i + 1]) / 2);
  }
  out.push(s[s.length - 1]);
  return out;
}

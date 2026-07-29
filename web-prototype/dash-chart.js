/** TradingView Lightweight Charts + live refresh every 60s. */
window.ATO = window.ATO || {};

ATO.createLiveChart = function createLiveChart(container, signal, options) {
  if (typeof LightweightCharts === "undefined") {
    setStatus("err", "Chart-Lib fehlt");
    return {
      onUpdate() {},
      async refreshNow() {},
      setTimeframe() {},
      getTimeframe() {
        return "1h";
      },
      destroy() {},
    };
  }

  const opts = options || {};
  const allowed = ["15m", "1h", "4h", "1d"];
  let timeframe = allowed.includes(opts.timeframe)
    ? opts.timeframe
    : allowed.includes(signal.timeframe)
      ? signal.timeframe
      : "1h";

  const chart = LightweightCharts.createChart(container, {
    layout: {
      background: { type: "solid", color: "#12161b" },
      textColor: "#8b95a1",
      fontFamily: "JetBrains Mono, monospace",
      fontSize: 11,
    },
    grid: {
      vertLines: { color: "rgba(255,255,255,0.04)" },
      horzLines: { color: "rgba(255,255,255,0.04)" },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: "rgba(255,255,255,0.2)", labelBackgroundColor: "#2a323c" },
      horzLine: { color: "rgba(255,255,255,0.2)", labelBackgroundColor: "#2a323c" },
    },
    rightPriceScale: {
      borderColor: "#2a323c",
      scaleMargins: { top: 0.08, bottom: 0.22 },
    },
    timeScale: {
      borderColor: "#2a323c",
      timeVisible: true,
      secondsVisible: false,
    },
    autoSize: true,
  });

  // v4 API: addCandlestickSeries / createPriceLine
  const candleSeries = chart.addCandlestickSeries({
    upColor: "#3dbe8c",
    downColor: "#e06b5c",
    borderUpColor: "#3dbe8c",
    borderDownColor: "#e06b5c",
    wickUpColor: "#3dbe8c",
    wickDownColor: "#e06b5c",
  });

  const volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "vol",
  });
  chart.priceScale("vol").applyOptions({
    scaleMargins: { top: 0.82, bottom: 0 },
  });

  const lines = [
    candleSeries.createPriceLine({
      price: signal.entry,
      color: "#d4a84b",
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: "ENTRY",
    }),
    candleSeries.createPriceLine({
      price: signal.sl,
      color: "#e06b5c",
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: "SL",
    }),
    candleSeries.createPriceLine({
      price: signal.tp1,
      color: "#3dbe8c",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: "TP1",
    }),
    candleSeries.createPriceLine({
      price: signal.tp2,
      color: "#3dbe8c",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: "TP2",
    }),
    candleSeries.createPriceLine({
      price: signal.tp3,
      color: "#3dbe8c",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: "TP3",
    }),
  ];

  let timer = null;
  let stopped = false;
  let loadSeq = 0;
  const listeners = [];

  async function load() {
    const seq = ++loadSeq;
    const exchange = (signal.exchange || "binance").toLowerCase();
    const qs = new URLSearchParams({
      symbol: signal.symbol,
      exchange: exchange.includes("kucoin") ? "kucoin" : "binance",
      timeframe,
    });
    const res = await fetch(`/api/candles?${qs}`);
    const payload = await res.json();
    if (seq !== loadSeq) return null;
    if (!res.ok || payload.error) throw new Error(payload.error || "candle fetch failed");
    const candles = payload.candles || [];
    if (!candles.length) throw new Error("keine Kerzen");
    candleSeries.setData(
      candles.map((c) => ({
        time: c.time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
    );
    volumeSeries.setData(
      candles.map((c) => ({
        time: c.time,
        value: c.volume,
        color:
          c.close >= c.open ? "rgba(61,190,140,0.35)" : "rgba(224,107,92,0.35)",
      }))
    );
    chart.timeScale().fitContent();
    const last = candles[candles.length - 1];
    const lastClose = last ? last.close : null;
    listeners.forEach((fn) =>
      fn({ lastClose, candles, at: Date.now(), timeframe })
    );
    return lastClose;
  }

  async function tick() {
    if (stopped) return;
    setStatus("wait", `Aktualisiere ${timeframe}…`);
    try {
      await load();
      const t = new Date().toLocaleTimeString("de-DE", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        timeZone: "Europe/Berlin",
      });
      setStatus("live", `Live ${timeframe} · aktualisiert ${t}`);
    } catch (err) {
      console.warn(err);
      setStatus("err", `Feed-Fehler · ${err.message || "Retry 60s"}`);
    }
  }

  function setStatus(kind, text) {
    const el = document.getElementById("liveStatus");
    if (!el) return;
    el.dataset.kind = kind;
    el.textContent = text;
  }

  tick();
  timer = setInterval(tick, 60_000);

  return {
    onUpdate(fn) {
      listeners.push(fn);
    },
    async refreshNow() {
      await tick();
    },
    getTimeframe() {
      return timeframe;
    },
    async setTimeframe(next) {
      if (!allowed.includes(next) || next === timeframe) return;
      timeframe = next;
      await tick();
    },
    destroy() {
      stopped = true;
      if (timer) clearInterval(timer);
      lines.forEach((l) => {
        try {
          candleSeries.removePriceLine(l);
        } catch (_) {
          /* ignore */
        }
      });
      chart.remove();
    },
  };
};

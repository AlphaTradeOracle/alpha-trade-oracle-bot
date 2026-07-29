(() => {
  const listEl = document.getElementById("list");
  const stage = document.getElementById("stage");
  const clock = document.getElementById("clock");
  let filter = "all";
  let activeId = ATO.signals.find((s) => s.status !== "closed")?.id || ATO.signals[0].id;
  let live = null;
  let lastClose = null;
  let selectedTf = null;

  const TF_OPTIONS = ["15m", "1h", "4h", "1d"];

  function tickClock() {
    clock.textContent = new Date().toLocaleTimeString("de-DE", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      timeZone: "Europe/Berlin",
    });
  }
  tickClock();
  setInterval(tickClock, 1000);

  function sideLabel(s) {
    return s.side === "long" ? "LONG" : "SHORT";
  }

  function defaultTf(signal) {
    return TF_OPTIONS.includes(signal.timeframe) ? signal.timeframe : "1h";
  }

  function pnlPct(signal, price) {
    if (price == null) return null;
    const raw =
      signal.side === "long"
        ? ((price - signal.entry) / signal.entry) * 100
        : ((signal.entry - price) / signal.entry) * 100;
    return raw;
  }

  function rMultiple(signal, price) {
    if (price == null) return null;
    const risk = Math.abs(signal.entry - signal.sl);
    if (!risk) return null;
    const move =
      signal.side === "long" ? price - signal.entry : signal.entry - price;
    return move / risk;
  }

  function renderList() {
    const rows = ATO.signals.filter((s) => {
      if (filter === "long" && s.side !== "long") return false;
      if (filter === "short" && s.side !== "short") return false;
      if (filter === "open" && s.status === "closed") return false;
      return true;
    });
    listEl.innerHTML = rows
      .map(
        (s) => `
      <button type="button" class="item ${s.id === activeId ? "active" : ""}" data-id="${s.id}" data-side="${s.side}">
        <span class="sym">${s.symbol}</span>
        <span class="side ${s.side}">${sideLabel(s)}</span>
        <span class="score">${ATO.fmt(s.score, 1)}</span>
        <span class="when">${s.opened} · ${s.exchange} · ${s.timeframe}</span>
      </button>`
      )
      .join("");
    listEl.querySelectorAll(".item").forEach((btn) => {
      btn.addEventListener("click", () => {
        activeId = btn.dataset.id;
        selectedTf = null;
        renderList();
        renderStage();
      });
    });
  }

  function tfSwitcherHtml(active) {
    return `<div class="tf-switch" role="group" aria-label="Timeframe">
      ${TF_OPTIONS.map(
        (tf) =>
          `<button type="button" class="tf-btn ${tf === active ? "active" : ""}" data-tf="${tf}">${tf}</button>`
      ).join("")}
    </div>`;
  }

  function renderStage() {
    const s = ATO.getById(activeId);
    if (!s) return;
    if (live) {
      live.destroy();
      live = null;
    }
    lastClose = null;

    const digits = s.entry < 0.1 ? 5 : s.entry < 1 ? 4 : 3;
    const status = s.status === "closed" ? `Closed · ${s.result || ""}` : "Open";
    const tf = selectedTf || defaultTf(s);
    selectedTf = tf;

    stage.innerHTML = `
      <div class="head">
        <div>
          <h1>${s.symbol}</h1>
          <div class="sub">
            <span>${s.opened} Europe/Berlin</span>
            <span>${s.exchange}</span>
            <span>${s.timeframe}</span>
            <span>${s.phase}</span>
            <span>${status}</span>
            <span id="liveStatus" data-kind="wait">Lade Live-Feed…</span>
          </div>
        </div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <button type="button" class="btn-refresh" id="refreshBtn">Refresh</button>
          <div class="badge ${s.side}">${ATO.dirLabel(s.direction)} · Score ${ATO.fmt(s.score, 1)}</div>
        </div>
      </div>

      <div class="kpis">
        <div class="kpi"><div class="l">Entry</div><div class="v">${ATO.fmt(s.entry, digits)}</div></div>
        <div class="kpi"><div class="l">Live</div><div class="v" id="kpiLive">—</div></div>
        <div class="kpi"><div class="l">PnL %</div><div class="v" id="kpiPnl">—</div></div>
        <div class="kpi"><div class="l">R jetzt</div><div class="v" id="kpiR">—</div></div>
      </div>

      <div class="grid">
        <section class="card">
          <div class="card-head">
            <h2>Live Chart · 60s Update</h2>
            ${tfSwitcherHtml(tf)}
          </div>
          <div class="chart-box" id="tv"></div>
          <div class="legend">
            <span><i style="background:#d4a84b"></i>Entry</span>
            <span><i style="background:#e06b5c"></i>SL</span>
            <span><i style="background:#3dbe8c"></i>TP1–3</span>
            <span>TradingView Lightweight Charts · Exchange Live</span>
          </div>
        </section>
        <div style="display:grid;gap:16px">
          <section class="card">
            <h2>Levels</h2>
            <div class="levels">
              ${levelRow("Entry", s.entry, digits, "#d4a84b")}
              ${levelRow("SL", s.sl, digits, "#e06b5c")}
              ${levelRow("TP1", s.tp1, digits, "#3dbe8c")}
              ${levelRow("TP2", s.tp2, digits, "#3dbe8c")}
              ${levelRow("TP3", s.tp3, digits, "#3dbe8c")}
            </div>
          </section>
          <section class="card">
            <h2>Begründung</h2>
            <ul class="why">
              ${s.confirms.map((c) => `<li>${c}</li>`).join("")}
            </ul>
            <div class="invalid"><strong>Ungültig:</strong> ${s.invalid}</div>
          </section>
          <section class="card llm-card">
            <div class="card-head">
              <h2>LLM Bewertung</h2>
              <span class="llm-tag">testweise · GPT-5.5</span>
            </div>
            <p class="llm-hint">Experimentelle Desk-Einschätzung — keine Anlageberatung. Nutzt OpenRouter (<code>openai/gpt-5.5</code>).</p>
            <button type="button" class="btn-llm" id="llmEvalBtn">Bewerten (GPT-5.5)</button>
            <div class="llm-out" id="llmOut" hidden></div>
          </section>
        </div>
      </div>
    `;

    live = ATO.createLiveChart(document.getElementById("tv"), s, { timeframe: tf });
    live.onUpdate(({ lastClose: close }) => {
      lastClose = close;
      const liveEl = document.getElementById("kpiLive");
      const pnlEl = document.getElementById("kpiPnl");
      const rEl = document.getElementById("kpiR");
      if (!liveEl) return;
      if (close == null) return;
      liveEl.textContent = ATO.fmt(close, digits);
      const pct = pnlPct(s, close);
      const r = rMultiple(s, close);
      pnlEl.textContent = `${pct >= 0 ? "+" : ""}${ATO.fmt(pct, 2)}%`;
      pnlEl.style.color = pct >= 0 ? "var(--long)" : "var(--short)";
      rEl.textContent = `${r >= 0 ? "+" : ""}${ATO.fmt(r, 2)}R`;
      rEl.style.color = r >= 0 ? "var(--long)" : "var(--short)";
    });

    document.getElementById("refreshBtn").addEventListener("click", () => {
      live.refreshNow();
    });

    stage.querySelectorAll(".tf-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const next = btn.dataset.tf;
        if (!next || next === selectedTf) return;
        selectedTf = next;
        stage.querySelectorAll(".tf-btn").forEach((b) =>
          b.classList.toggle("active", b.dataset.tf === next)
        );
        await live.setTimeframe(next);
      });
    });

    document.getElementById("llmEvalBtn").addEventListener("click", () => {
      runLlmEval(s);
    });
  }

  function levelRow(k, v, digits, color) {
    return `<div class="row">
      <span class="k">${k}</span>
      <div class="bar"><span style="width:100%;background:${color}"></span></div>
      <span>${ATO.fmt(v, digits)}</span>
    </div>`;
  }

  function verdictClass(v) {
    const x = (v || "").toLowerCase();
    if (["bullish", "keep", "scale"].includes(x)) return "bull";
    if (["bearish", "exit"].includes(x)) return "bear";
    return "mid";
  }

  function verdictLabel(v) {
    const map = {
      bullish: "Bullish",
      cautious: "Vorsichtig",
      bearish: "Bearish",
      keep: "Halten",
      scale: "Skalieren",
      exit: "Exit",
    };
    return map[(v || "").toLowerCase()] || v || "—";
  }

  async function runLlmEval(signal) {
    const btn = document.getElementById("llmEvalBtn");
    const out = document.getElementById("llmOut");
    if (!btn || !out) return;

    btn.disabled = true;
    btn.textContent = "Bewerte…";
    out.hidden = false;
    out.innerHTML = `<div class="llm-loading">GPT-5.5 analysiert Signal…</div>`;

    const price = lastClose;
    const pct = pnlPct(signal, price);
    const r = rMultiple(signal, price);
    const body = {
      symbol: signal.symbol,
      side: signal.side,
      direction: signal.direction,
      entry: signal.entry,
      sl: signal.sl,
      tp1: signal.tp1,
      tp2: signal.tp2,
      tp3: signal.tp3,
      score: signal.score,
      phase: signal.phase,
      confirms: signal.confirms,
      invalid: signal.invalid,
      exchange: signal.exchange,
      signal_timeframe: signal.timeframe,
      selected_timeframe: (live && live.getTimeframe()) || selectedTf || defaultTf(signal),
      live_price: price,
      pnl_pct: pct != null ? Number(pct.toFixed(3)) : null,
      r_multiple: r != null ? Number(r.toFixed(3)) : null,
      status: signal.status || "open",
    };

    try {
      const res = await fetch("/api/llm/evaluate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!data.ok) {
        out.innerHTML = `<div class="llm-err">
          <strong>Nicht verfügbar</strong>
          <p>${escapeHtml(data.message || data.error || "Unbekannter Fehler")}</p>
        </div>`;
        return;
      }
      const reasons = (data.reasons || [])
        .map((r) => `<li>${escapeHtml(r)}</li>`)
        .join("");
      out.innerHTML = `
        <div class="llm-result">
          <div class="llm-meta">
            <span class="llm-verdict ${verdictClass(data.verdict)}">${escapeHtml(verdictLabel(data.verdict))}</span>
            <span class="llm-conf">Konfidenz ${ATO.fmt(data.confidence, 0)}%</span>
            <span class="llm-model">${escapeHtml(data.model || "openai/gpt-5.5")}</span>
          </div>
          ${data.summary ? `<p class="llm-summary">${escapeHtml(data.summary)}</p>` : ""}
          <ul class="llm-reasons">${reasons}</ul>
          ${
            data.risk_note
              ? `<div class="llm-risk"><strong>Risiko:</strong> ${escapeHtml(data.risk_note)}</div>`
              : ""
          }
        </div>`;
    } catch (err) {
      out.innerHTML = `<div class="llm-err">
        <strong>Netzwerkfehler</strong>
        <p>${escapeHtml(err.message || String(err))}</p>
      </div>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Bewerten (GPT-5.5)";
    }
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  document.querySelectorAll(".filters .f").forEach((btn) => {
    btn.addEventListener("click", () => {
      filter = btn.dataset.f;
      document.querySelectorAll(".filters .f").forEach((b) =>
        b.classList.toggle("active", b === btn)
      );
      renderList();
    });
  });

  renderList();
  renderStage();
})();

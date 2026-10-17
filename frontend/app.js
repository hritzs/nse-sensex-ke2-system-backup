(() => {
  const RUNS = ["0916","0917","0918","0919","NOW"];
  let activeRun = localStorage.getItem("minuteActiveRun") || "0916";
  let latestState = null;
  let refreshTimer = null;

  const $ = id => document.getElementById(id);

  function fmt(v, digits = 2) {
    if (v === null || v === undefined || !Number.isFinite(Number(v))) return "--";
    return Number(v).toLocaleString("en-IN", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    });
  }

  function signed(v, digits = 2) {
    if (v === null || v === undefined || !Number.isFinite(Number(v))) return "--";
    const x = Number(v);
    return `${x >= 0 ? "+" : ""}${fmt(x, digits)}`;
  }

  function timeOnly(value) {
    if (!value) return "--";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value).slice(-8);
    return d.toLocaleTimeString("en-IN", {hour12:false});
  }

  function set(id, value) {
    const n = $(id);
    if (n) n.textContent = value;
  }

  function renderTabs(allRuns) {
    const host = $("runTabs");
    host.innerHTML = "";
    for (const runId of RUNS) {
      const state = allRuns?.[runId] || {};
      const b = document.createElement("button");
      b.className = `run-tab ${runId === activeRun ? "active" : ""}`;
      b.textContent = runId === "NOW" ? "NOW" : `${runId.slice(0,2)}:${runId.slice(2)}`;
      b.title = state.status || "WAITING";
      b.onclick = () => {
        activeRun = runId;
        localStorage.setItem("minuteActiveRun", activeRun);
        refresh();
      };
      host.appendChild(b);
    }
  }

  function renderExpiry(data) {
    const select = $("expirySelect");
    const available = data?.available || [];
    const old = data?.selected_expiry || data?.target_expiry || "";
    select.innerHTML = available.map(x => `<option value="${x}">${x}</option>`).join("");
    if (old && available.includes(old)) select.value = old;
    set("expiryBadge", `ACTIVE EXPIRY: ${data?.selected_expiry || data?.target_expiry || "--"}`);
  }

  function render(state) {
    latestState = state;
    const market = state.market || {};
    const entry = state.entry || {};
    const pos = state.position || {};
    const greeks = state.greeks || {};
    const hedge = state.hedge || {};
    const pnl = state.pnl || {};
    const exit = state.exit || {};

    const badge = $("connectionBadge");
    badge.className = `badge ${state.status === "RUNNING" || state.status === "WAITING" || state.status === "ARMED" ? "good" : state.status === "COMPLETED" ? "muted" : "muted"}`;
    badge.textContent = state.status || "--";

    set("statusValue", state.status || "--");
    set("spotValue", fmt(market.synthetic_future ?? market.spot, 2));
    set("marketAtmValue", fmt(market.atm, 0));
    set("marketStraddleValue", fmt(market.straddle, 2));
    set("positionStrikeValue", fmt(pos.strike, 0));
    set("positionStraddleValue", fmt(pos.current_straddle, 2));
    set("minStraddleValue", fmt(pos.min_straddle, 2));
    set("maxStraddleValue", fmt(pos.max_straddle, 2));

    set("entryCe", fmt(entry.ce_price, 2));
    set("entryPe", fmt(entry.pe_price, 2));
    set("entryStraddle", fmt(entry.straddle, 2));
    set("currentCe", fmt(pos.current_ce, 2));
    set("currentPe", fmt(pos.current_pe, 2));
    set("quoteSource", entry.quote_source || "--");
    set("entryTime", timeOnly(entry.timestamp));
    set("entryExpiry", entry.expiry || "--");

    set("optionPnl", signed(pnl.option, 2));
    set("futurePnl", signed(pnl.synthetic_hedge ?? pnl.future, 2));
    set("totalPnl", signed(pnl.total, 2));

    const size = Math.max(1, Number($("greekSize").value) || 1);
    const greeksToRender = [
      ["delta", 6, "deltaRaw"],
      ["gamma", 7, "gammaRaw"],
      ["theta", 4, "thetaRaw"],
      ["vega", 4, "vegaRaw"]
    ];
    for (const [key, digits, rawId] of greeksToRender) {

      // Delta uses the backend's display-only smoothed value.
      // Gamma / Theta / Vega remain raw.
      const raw =
        key === "delta"
          ? (greeks.display_delta ?? greeks.delta)
          : greeks[key];

      set(
        key,
        raw == null
          ? "--"
          : fmt(
              Number(raw) * size,
              digits
            )
      );

      set(
        rawId,
        raw == null
          ? "1 Qty = --"
          : `1 Qty = ${fmt(raw, digits)}`
      );
    }

    set("hedge", fmt(hedge.current, 4));
    set("hedgeTarget", fmt(hedge.target, 4));
    set("pointsOut", fmt(hedge.points_out, 3));
    set("pointsAllowed", fmt(hedge.points_allowed, 3));
    set("tpPoints", fmt(hedge.tp_points, 2));
    set("tpBps", fmt(hedge.tp_bps, 1));
    set("slPoints", fmt(hedge.sl_points, 2));
    set("rtvValue", fmt(hedge.rtv_value, 2));
    set("rtvThreshold", fmt(hedge.rtv_threshold, 2));
    set("otmIv", hedge.otm_iv == null ? "--" : `${fmt(Number(hedge.otm_iv) * 100, 2)}%`);
    set("ivDrop", hedge.iv_drop_threshold == null ? "10.00%" : `${fmt(Number(hedge.iv_drop_threshold) * 100, 2)}%`);
    set("exitReason", exit.reason || "RUNNING");

    renderHedgeEvents(hedge.events || []);
    renderHistory(state.history || []);
    drawChart(state.history || []);
  }

  function renderHedgeEvents(events) {
    const body = $("hedgeEvents");
    if (!events.length) {
      body.innerHTML = `<tr><td colspan="9" class="empty">No hedge events</td></tr>`;
      return;
    }
    body.innerHTML = events.slice(0,50).map(e => `
      <tr>
        <td>${timeOnly(e.timestamp)}</td>
        <td>${e.type || "--"}</td>
        <td>${fmt(e.old_hedge,4)}</td>
        <td>${fmt(e.new_hedge,4)}</td>
        <td>${signed(e.change,4)}</td>
        <td>${fmt(e.net_delta ?? e.delta,5)}</td>
        <td>${fmt(e.points_out,3)}</td>
        <td>${fmt(e.points_allowed,3)}</td>
        <td>${fmt(e.synthetic,2)}</td>
      </tr>`).join("");
  }

  function renderHistory(rows) {
    const body = $("history");
    const visible = [...rows].reverse().slice(0,120);
    if (!visible.length) {
      body.innerHTML = `<tr><td colspan="8" class="empty">No minute history yet</td></tr>`;
      return;
    }
    body.innerHTML = visible.map(r => `
      <tr>
        <td>${timeOnly(r.timestamp)}</td>
        <td>${fmt(r.synthetic_future ?? r.spot,2)}</td>
        <td>${fmt(r.atm,0)}</td>
        <td>${fmt(r.current_ce,2)}</td>
        <td>${fmt(r.current_pe,2)}</td>
        <td>${fmt(r.straddle,2)}</td>
        <td>${fmt(r.delta,5)}</td>
        <td>${signed(r.total_pnl,2)}</td>
      </tr>`).join("");
  }

  function drawChart(rows) {
    const canvas = $("pnlChart");
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(300, rect.width);
    const h = Math.max(120, rect.height);
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.clearRect(0,0,w,h);

    const values = rows.map(r => Number(r.total_pnl)).filter(Number.isFinite);
    if (!values.length) return;

    const pad = {l:46,r:14,t:14,b:24};
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = Math.max(0.001, max - min);
    const x = i => pad.l + (i / Math.max(1, values.length-1)) * (w-pad.l-pad.r);
    const y = v => h-pad.b - ((v-min)/span) * (h-pad.t-pad.b);

    ctx.strokeStyle = "#1c3044";
    ctx.lineWidth = 1;
    for(let i=0;i<4;i++){
      const yy = pad.t + i * (h-pad.t-pad.b)/3;
      ctx.beginPath(); ctx.moveTo(pad.l,yy); ctx.lineTo(w-pad.r,yy); ctx.stroke();
    }

    ctx.fillStyle = "#8294a8";
    ctx.font = "9px Arial";
    ctx.fillText(fmt(max,2), 5, pad.t+4);
    ctx.fillText(fmt(min,2), 5, h-pad.b);

    ctx.beginPath();
    values.forEach((v,i) => { const px=x(i),py=y(v); if(i===0)ctx.moveTo(px,py);else ctx.lineTo(px,py); });
    ctx.strokeStyle = "#62d39c";
    ctx.lineWidth = 1.5;
    ctx.stroke();

    const current = values[values.length-1];
    ctx.fillStyle = "#e8eff6";
    ctx.fillText(`Current ${signed(current,2)}`, Math.max(pad.l, w-130), 11);
  }

  async function getJson(url, options={}) {
    const response = await fetch(url, {cache:"no-store", ...options});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  async function refresh() {
    try {
      const [runs, expiry] = await Promise.all([
        getJson("/api/runs"),
        getJson("/api/expiries")
      ]);
      renderTabs(runs.runs || {});
      renderExpiry(expiry);
      const state = await getJson(`/api/runs/${activeRun}`);
      render(state);
      $("connectionBadge").className = `badge ${state.status === "COMPLETED" ? "muted" : "good"}`;
      $("connectionBadge").textContent = state.status || "CONNECTED";
    } catch (err) {
      $("connectionBadge").className = "badge bad";
      $("connectionBadge").textContent = "OFFLINE";
      console.error(err);
    }
  }

  $("confirmExpiry").onclick = async () => {
    try {
      const expiry = $("expirySelect").value;
      await getJson("/api/select_expiry", {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({expiry})
      });
      await refresh();
    } catch (err) { alert(err.message); }
  };

  $("startNow").onclick = async () => {
    try {
      activeRun = "NOW";
      localStorage.setItem("minuteActiveRun", activeRun);
      await getJson("/api/runs/NOW/start", {method:"POST"});
      await refresh();
    } catch (err) { alert(err.message); }
  };

  $("clearRun").onclick = async () => {
    if (!confirm(`Clear persisted state for ${activeRun}?`)) return;
    try {
      await getJson(`/api/runs/${activeRun}/clear`, {method:"POST"});
      await refresh();
    } catch (err) { alert(err.message); }
  };

  $("greekSize").addEventListener("input", () => {
    localStorage.setItem("minuteGreekSize", $("greekSize").value);
    if (latestState) render(latestState);
  });

  const savedSize = localStorage.getItem("minuteGreekSize");
  if (savedSize && Number(savedSize) > 0) $("greekSize").value = savedSize;

  setInterval(() => {
    const d = new Date();
    $("clock").textContent = d.toLocaleTimeString("en-IN", {hour12:false});
  }, 250);

  refresh();
  refreshTimer = setInterval(refresh, 1000);
  window.addEventListener("resize", () => latestState && drawChart(latestState.history || []));
})();

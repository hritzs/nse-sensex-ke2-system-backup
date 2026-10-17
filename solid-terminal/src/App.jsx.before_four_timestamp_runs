import {
  createSignal,
  createMemo,
  createEffect,
  For,
  Show,
  onMount,
  onCleanup,
  batch,
  untrack
} from "solid-js";
import { createStore, produce } from "solid-js/store";

import "./App.css";

const BACKEND_HOST = window.location.hostname;
const API_BASE = `http://${BACKEND_HOST}:9001`;
const WS_BASE = `ws://${BACKEND_HOST}:9001`;

/* ==========================================================
   Dalal Street
   Final Institutional SolidJS Frontend

   Features:
   ✔ Loads ALL stocks from backend automatically
   ✔ Multi-panel workspace
   ✔ Expiry ribbon selector
   ✔ 10 ATM strikes default
   ✔ Change strike count instantly
   ✔ Copyable numbers
   ✔ Fast websocket rendering
   ✔ Bloomberg style UI
========================================================== */

export default function App() {
  const [theme, setTheme] = createSignal(localStorage.getItem('app_theme') || 'dark');
  createEffect(() => {
    document.body.className = theme() === 'light' ? 'theme-light' : 'theme-dark';
    document.documentElement.style.colorScheme = theme() === 'light' ? 'light' : 'dark';
    localStorage.setItem('app_theme', theme());
  });

  const [status, setStatus] = createSignal("Connecting...");
  const [books, setBooks] = createStore({});
  const [activeExpiries, setActiveExpiries] = createSignal({});
  const [symbols, setSymbols] = createSignal([]);
  const [panels, setPanels] = createSignal([]);
  const [eodStore, setEodStore] = createStore({}); // Global Store for EOD Bhavcopy overrides
  const [activeTab, setActiveTab] = createSignal("Screener"); // "Terminal", "Screener", "History", "Results", "Forwards", "Spreads"
  const [resultsData, setResultsData] = createSignal([]);
  const [historySymbol, setHistorySymbol] = createSignal("");
  const savedOverrides = JSON.parse(localStorage.getItem('screener_overrides') || '{"strikes":{}, "gaps":{}, "futures":{}}');
  const [overridesStore, setOverridesStore] = createStore(savedOverrides);

  // Centralized Application Logging
  const [appLogs, setAppLogs] = createSignal([]);
  const addLog = (msg) => {
    const d = new Date();
    const ts = d.getHours().toString().padStart(2, '0') + ':' + 
               d.getMinutes().toString().padStart(2, '0') + ':' + 
               d.getSeconds().toString().padStart(2, '0') + '.' + 
               d.getMilliseconds().toString().padStart(3, '0');
    setAppLogs(prev => [...prev, `[${ts}] ${msg}`].slice(-1000)); // Keep last 1000 logs
  };

  // Hoisted Watchlist to be shared globally with the Ticker Tape
  const [watchlist, setWatchlist] = createSignal(new Set(JSON.parse(localStorage.getItem('iv_watchlist') || '[]')));
  const toggleWatchlist = (sym) => {
    const next = new Set(watchlist());
    if (next.has(sym)) next.delete(sym); else next.add(sym);
    localStorage.setItem('iv_watchlist', JSON.stringify([...next]));
    setWatchlist(next);
  };

  let ws;
  let retry;
  let connectionTimeout;

  let msgBuffer = [];
  let bufferTimer = null;

  function processBuffer() {
    const currentBuffer = msgBuffer;
    msgBuffer = [];
    bufferTimer = null;

    batch(() => {
      const bookUpdates = {};
      const expUpdates = {};
      const newSymbols = new Set();

      for (const d of currentBuffer) {
        if (d.type === "chain") {
          const key = `${d.symbol}_${d.expiry}`;
          let existingBook = books[key];
          let bookData = { ...d, _rowsMap: new Map() };
          if (d.rows) d.rows.forEach(r => bookData._rowsMap.set(r.k, r));
          
          if (d.future !== undefined && d.future !== null && d.future > 0) {
              bookData.future = d.future;
          } else if (d.spot !== undefined && d.spot !== null && d.spot > 0) {
              bookData.future = d.spot;
          }
          bookUpdates[key] = bookData;
          
          if (!expUpdates[d.symbol]) expUpdates[d.symbol] = new Set();
          expUpdates[d.symbol].add(d.expiry);
          newSymbols.add(d.symbol);
        } 
        else if (d.type === "surface" || d.type === "analytics") {
          let underlying, expiry, strike, isCall, isFut = false;
          if (d.symbol.includes('|')) {
              const parts = d.symbol.split('|');
              underlying = parts[0];
              expiry = parts[1];
              if (parts.length >= 4) {
                  strike = parseFloat(parts[2]);
                  isCall = parts[3] === 'CE';
                  isFut = parts[3] === 'FUT' || parts[3] === 'XX';
              } else if (parts.length === 3) {
                  isFut = true;
                  strike = 0;
              }
          } else {
              const match = d.symbol.match(/^([A-Z0-9\-]+?)\d{2}[A-Z]{3}/i);
              underlying = d.underlying || (match ? match[1].toUpperCase() : d.symbol);
              expiry = d.expiry;
              strike = d.strike;
              isCall = d.isCall !== undefined ? d.isCall : d.symbol.toUpperCase().endsWith("CE");
              isFut = d.symbol.toUpperCase().endsWith("FUT");
          }
          
          if (!expiry) continue;
          
          const key = `${underlying}_${expiry}`;
          
          if (!bookUpdates[key]) {
            const existing = books[key];
            if (existing) {
              bookUpdates[key] = { ...existing, _rowsMap: new Map() };
              existing.rows.forEach(r => bookUpdates[key]._rowsMap.set(r.k, { ...r }));
            } else {
              bookUpdates[key] = { type: "chain", symbol: underlying, expiry: expiry, future: d.future || d.spot || null, _rowsMap: new Map() };
            }
          }
          
          const book = bookUpdates[key];
          
          if (!isFut && strike != null && !isNaN(strike)) {
              let row = book._rowsMap.get(strike);
              if (!row) {
                row = { k: strike };
                book._rowsMap.set(strike, row);
              }
              const bid = d.bid1 !== undefined ? d.bid1 : (d.bid !== undefined ? d.bid : d.ltp);
              const ask = d.ask1 !== undefined ? d.ask1 : (d.ask !== undefined ? d.ask : d.ltp);
              if (isCall) {
                row.ce_ltp = d.ltp; row.ce_bid = bid; row.ce_ask = ask; row.ce_iv = d.iv;
                row.ce_bid_iv = d.iv_bid !== undefined && d.iv_bid > 0 ? d.iv_bid : d.iv;
                row.ce_ask_iv = d.iv_ask !== undefined && d.iv_ask > 0 ? d.iv_ask : d.iv;
                row.ce_delta = d.delta; row.ce_gamma = d.gamma; row.ce_theta = d.theta; row.ce_vega = d.vega;
                row.ce_chg5 = d.chg5; row.ce_chg15 = d.chg15; row.ce_chg30 = d.chg30; row.ce_chg60 = d.chg60;
                row.ce_chg1d = d.chg1d; row.ce_max_iv = d.max_iv; row.ce_min_iv = d.min_iv; row.ce_oi = d.oi;
              } else {
                row.pe_ltp = d.ltp; row.pe_bid = bid; row.pe_ask = ask; row.pe_iv = d.iv;
                row.pe_bid_iv = d.iv_bid !== undefined && d.iv_bid > 0 ? d.iv_bid : d.iv;
                row.pe_ask_iv = d.iv_ask !== undefined && d.iv_ask > 0 ? d.iv_ask : d.iv;
                row.pe_delta = d.delta; row.pe_gamma = d.gamma; row.pe_theta = d.theta; row.pe_vega = d.vega;
                row.pe_chg5 = d.chg5; row.pe_chg15 = d.chg15; row.pe_chg30 = d.chg30; row.pe_chg60 = d.chg60;
                row.pe_chg1d = d.chg1d; row.pe_max_iv = d.max_iv; row.pe_min_iv = d.min_iv; row.pe_oi = d.oi;
              }
          }
          
          if (d.atm_strike !== undefined) book.atm_strike = d.atm_strike;

          if (isFut && d.ltp) {
              book.future = d.ltp;
          } else if (d.future !== undefined && d.future !== null && d.future > 0) {
              book.future = d.future;
          } else if (d.spot !== undefined && d.spot !== null && d.spot > 0) {
              book.future = d.spot;
          }

          if (isFut || strike == null || isNaN(strike)) {
              if (!expUpdates[underlying]) expUpdates[underlying] = new Set();
              expUpdates[underlying].add(expiry);
              newSymbols.add(underlying);
              continue;
          }

          if (!expUpdates[underlying]) expUpdates[underlying] = new Set();
          expUpdates[underlying].add(d.expiry);
          newSymbols.add(underlying);
        }
      }

      // Final bulk apply to store
      for (const [key, book] of Object.entries(bookUpdates)) {
        const rowsArray = Array.from(book._rowsMap.values()).sort((a,b) => a.k - b.k);
        delete book._rowsMap;
        book.rows = rowsArray;
        setBooks(key, book);
      }

      // Handle Expiries updates
      if (Object.keys(expUpdates).length > 0) {
        setActiveExpiries(prev => {
          let next = null;
          for (const sym in expUpdates) {
            const arr = prev[sym] || [];
            let added = false;
            expUpdates[sym].forEach(exp => { if (!arr.includes(exp)) added = true; });
            if (added) {
              if (!next) next = { ...prev };
              const newArr = [...arr];
              expUpdates[sym].forEach(exp => { if (!newArr.includes(exp)) newArr.push(exp); });
              next[sym] = newArr.sort(sortExpiries);
            }
          }
          return next || prev;
        });
      }

      // Handle Symbol updates
      if (newSymbols.size > 0) {
        setSymbols(prev => {
          let changed = false;
          newSymbols.forEach(s => { if (!prev.includes(s)) changed = true; });
          if (changed) {
            const next = [...prev];
            newSymbols.forEach(s => { if (!next.includes(s)) next.push(s); });
            return next.sort();
          }
          return prev;
        });
      }
    });
  }

  // Global Heartbeat Tick to safely force reactivity on complex derived dashboards
  const [tick, setTick] = createSignal(0);
  createEffect(() => {
    const interval = setInterval(() => setTick(v => v + 1), 1000);
    onCleanup(() => clearInterval(interval));
  });

  // Hydrate Data from Session Snapshot on Mount
  onMount(() => {
     try {
       const cachedBooks = sessionStorage.getItem('books_snapshot');
       const cachedExps = sessionStorage.getItem('exps_snapshot');
       const cachedSyms = sessionStorage.getItem('syms_snapshot');
       if (cachedBooks) setBooks(JSON.parse(cachedBooks));
       if (cachedExps) setActiveExpiries(JSON.parse(cachedExps));
       if (cachedSyms) setSymbols(JSON.parse(cachedSyms));
     } catch(e) {}
  });

  // Automatically Cache Live State to prevent blank screens during WS Disconnects
  createEffect(() => {
    if (tick() > 0 && tick() % 10 === 0) {
       try {
           const activeSyms = symbols();
           if (activeSyms.length > 0) {
               sessionStorage.setItem('syms_snapshot', JSON.stringify(activeSyms));
               sessionStorage.setItem('exps_snapshot', JSON.stringify(activeExpiries()));
               sessionStorage.setItem('books_snapshot', JSON.stringify(untrack(() => books)));
           }
       } catch(e) {}
    }
  });

  // Global Diagnostic Logger (Throttled to every 10 seconds to prevent lag)
  createEffect(() => {
    if (tick() > 0 && tick() % 10 === 0) {
      const now = new Date();
      const ts = `${now.getHours()}:${now.getMinutes()}:${now.getSeconds()}.${now.getMilliseconds()}`;
      console.log(`[DEBUG] Tick: ${tick()} | WS ReadyState: ${ws?.readyState} | Time: ${ts}`);
      console.log(`[DEBUG] Symbols Count: ${symbols().length} | Books loaded: ${Object.keys(books).length}`);
      
      if (symbols().length > 0) {
        const testSym = symbols().includes("NIFTY") ? "NIFTY" : symbols()[0];
        const testExp = activeExpiries()[testSym]?.[0];
        const testBook = books[`${testSym}_${testExp}`];
        console.log(`[DEBUG] Screener sanity check for '${testSym}':`, {
           hasExpiry: !!testExp,
           hasBook: !!testBook,
           futurePrice: testBook?.future,
           optionsRowCount: testBook?.rows?.length || 0
        });
      }
    }
  });

  // Keep Overrides Synced into LocalStorage
  createEffect(() => {
    localStorage.setItem('screener_overrides', JSON.stringify(overridesStore));
  });

  // Auto-Fetch Upcoming Results Data globally
  onMount(() => {
    const fetchResults = () => {
      fetch(`${API_BASE}/get-results`)
        .then(r => r.json())
        .then(d => setResultsData(d || []))
        .catch(e => {
          console.error("Results fetch error:", e);
          setTimeout(fetchResults, 3000);
        });
    };
    fetchResults();
  });

  // Auto-Fetch Latest Bhavcopy from Backend
  onMount(() => {
    const fetchBhavcopy = () => {
      fetch(`${API_BASE}/bhavcopy`)
        .then(res => res.ok ? res.text() : Promise.reject("Not available"))
        .then(csvText => {
          if (typeof parseBhavcopyCSV === "function") parseBhavcopyCSV(csvText, setEodStore);
          addLog("[SYSTEM] Auto-loaded Bhavcopy successfully!");
          console.log("Auto-loaded Bhavcopy successfully!");
        })
        .catch(() => {
          addLog("[SYSTEM] Auto Bhavcopy not found. Waiting for manual upload or retrying...");
          console.log("Auto Bhavcopy not found. Waiting for manual upload or retrying...");
          setTimeout(fetchBhavcopy, 5000);
        });
    };
    fetchBhavcopy();
  });

  // Auto-Fetch EOD IVs from Backend to hydrate Real-Time Terminal & Screener before 9:15
  onMount(() => {
    const fetchEodIvs = () => {
      fetch(`${API_BASE}/eod-ivs`)
        .then(res => res.json())
        .then(data => {
          if (data && Object.keys(data).length > 0) {
            // Normalize flat dict or array of objects into a flat dict
            let flatData = {};
            if (Array.isArray(data)) {
                data.forEach(d => { if (d.symbol) flatData[d.symbol] = d.iv ?? d.ltp ?? d.close ?? d.spot; });
            } else {
                for (const [key, val] of Object.entries(data)) {
                    if (val && typeof val === 'object') {
                        flatData[key] = val.iv ?? val.ltp ?? val.close ?? val.spot;
                    } else if (typeof val === 'number') {
                        flatData[key] = val;
                    }
                }
            }
            
            const newEodStore = {};
            for (const [rawKey, val] of Object.entries(flatData)) {
                // Match: SYM_DD-MMM-YY_STRIKE_TYPE_IV  (with or without hyphens in expiry)
                const m = rawKey.match(/^([A-Z0-9\-&_]+)_((\d{2}-[A-Z]{3}-\d{2,4}|\d{2}[A-Z]{3}\d{2,4}))_(\d+(?:\.\d+)?)_(CE|PE)_IV$/i);
                if (m) {
                    const sym    = m[1].toUpperCase();
                    let   exp    = m[2].toUpperCase();
                    if (!exp.includes('-')) exp = exp.slice(0,2) + '-' + exp.slice(2,5) + '-' + exp.slice(5);
                    const strike = parseFloat(m[4]);
                    const type   = m[5].toUpperCase();
                    newEodStore[`${sym}_${exp}_${strike}_${type}_IV`] = val;
                } else {
                    // Match: SYM_DD-MMM-YY_STRIKE_CE (or PE or FUT)
                    const mp = rawKey.match(/^([A-Z0-9\-&_]+)_((\d{2}-[A-Z]{3}-\d{2,4}|\d{2}[A-Z]{3}\d{2,4}))(?:_(\d+(?:\.\d+)?))?_(CE|PE|FUT)$/i);
                    if (mp) {
                        const sym    = mp[1].toUpperCase();
                        let   exp    = mp[2].toUpperCase();
                        if (!exp.includes('-')) exp = exp.slice(0,2)+'-'+exp.slice(2,5)+'-'+exp.slice(5);
                        const strikeStr = mp[4];
                        const type   = mp[5].toUpperCase();
                        if (type === 'FUT') {
                            if (!newEodStore[sym]) newEodStore[sym] = {};
                            if (!newEodStore[sym][exp]) newEodStore[sym][exp] = {};
                            newEodStore[sym][exp].FUT = val;
                        } else if (strikeStr) {
                            const strike = parseFloat(strikeStr);
                            newEodStore[`${sym}_${exp}_${strike}_${type}`] = val;
                        }
                    } else {
                        newEodStore[rawKey] = val; // Fallback to ensure we never drop unparsed data
                    }
                }
            }
            
            setEodStore(newEodStore);
          }
        })
        .catch(e => {
          addLog(`[SYSTEM] EOD IVs not loaded yet, retrying...`);
          console.log("EOD IVs not loaded yet, retrying:", e);
          setTimeout(fetchEodIvs, 3000);
        });
    };
    fetchEodIvs();
  });

  const sendMessage = (msg) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  };

  onMount(connect);

  onCleanup(() => {
    clearTimeout(retry);
    clearTimeout(connectionTimeout);
    if (ws) ws.close();
  });

  function connect() {
    ws = new WebSocket(WS_BASE);

    connectionTimeout = setTimeout(() => {
      if (ws && ws.readyState === 0) { // WebSocket.CONNECTING
        addLog("[WS] Connection taking longer than expected. Backend busy.");
        console.warn("WS connection taking longer than expected. Backend is busy parsing massive datasets. Waiting patiently...");
        // Removed ws.close() to prevent infinite disconnect loops during massive initial data loads
      }
    }, 15000);

    ws.onopen = () => {
      clearTimeout(connectionTimeout);
      setStatus("Connected");
      addLog("[WS] Connected to Live Market Data Server");
      
      // Hydrate Backend with Saved Overrides upon connection
      setTimeout(() => {
        Object.entries(overridesStore.strikes || {}).forEach(([exp, val]) => {
          if (val) sendMessage({ action: 'set_override', target: exp, value: val, symbol: 'NIFTY' });
        });
        Object.entries(overridesStore.gaps || {}).forEach(([exp, val]) => {
          if (val) sendMessage({ action: 'set_gap_override', target: exp, value: val, symbol: 'NIFTY' });
        });
        Object.entries(overridesStore.futures || {}).forEach(([exp, val]) => {
          if (val && val.offset !== undefined) sendMessage({ action: 'set_future_override', target: exp, base: val.base_expiry || null, offset: val.offset, symbol: 'NIFTY' });
        });
      }, 1000); // 1-second delay to ensure socket readiness
    };

    ws.onerror = () => {
      setStatus("Socket Error");
      addLog("[WS] Socket Error encountered");
    };

    ws.onclose = () => {
      setStatus("Reconnecting...");
      addLog("[WS] Socket disconnected. Attempting to reconnect...");
      retry = setTimeout(connect, 1500);
    };

    ws.onmessage = (ev) => {
      try {
        const parsed = JSON.parse(ev.data);
        if (parsed.type === "log" || parsed.type === "system") {
          addLog(`[SERVER] ${parsed.message || JSON.stringify(parsed)}`);
          return;
        }
        msgBuffer.push(parsed);
        if (!bufferTimer) {
          bufferTimer = requestAnimationFrame(processBuffer);
        }
      } catch {}
    };
  }

  // Fetch available symbols on mount
  onMount(() => {
    const fetchSymbols = async () => {
      try {
        const res = await fetch(`${API_BASE}/symbols`);
        if (!res.ok) throw new Error("Network response was not ok");
        const data = await res.json();
        if (data.symbols && data.symbols.length > 0) {
          setSymbols(data.symbols);
          setPanels(p => p.length === 0 ? [{ id: 1, symbol: "NIFTY", expiry: "", strikes: 10 }] : p);
          addLog(`[SYSTEM] Fetched ${data.symbols.length} available symbols from backend.`);
        } else {
          throw new Error("Symbols array is empty");
        }
      } catch (err) {
        addLog("[SYSTEM] Failed to load symbols, retrying...");
        console.error("Failed to load symbols, retrying:", err);
        setTimeout(fetchSymbols, 3000);
      }
    };
    fetchSymbols();
  });

  // Keep History Selected Symbol in sync if empty
  createEffect(() => {
    if (!historySymbol() && symbols().length > 0) {
      setHistorySymbol(symbols()[0]);
    }
  });

  function addPanel() {
    const first = "NIFTY";

    setPanels(p => [
      ...p,
      {
        id: Date.now(),
        symbol: first,
        expiry: "",
        strikes: 10
      }
    ]);
  }

  function removePanel(id) {
    if (panels().length === 1) return;
    setPanels(p => p.filter(x => x.id !== id));
  }

  function updatePanel(id, patch) {
    setPanels(p =>
      p.map(x =>
        x.id === id
          ? { ...x, ...patch }
          : x
      )
    );
  }

  return (
    <div class={`app ${theme() === 'light' ? 'theme-light' : 'theme-dark'}`} style={{ display: "flex", "flex-direction": "column", height: "100vh", overflow: "hidden" }}>
      {/* Unconditionally inject scoped CSS via innerHTML to fix parsing/hydration issues across systems */}
      <style innerHTML={`
          :root {
            --green: #4caf50;
            --red: #e57373;
            --blue: #64b5f6;
            --yellow: #ffb74d;
            --green-flash: #388e3c;
            --red-flash: #c62828;
            --green-bg: rgba(76, 175, 80, 0.07);
            --red-bg: rgba(229, 115, 115, 0.07);
            --blue-bg: rgba(100, 181, 246, 0.07);
          }
          .color-up { color: var(--green) !important; }
          .color-down { color: var(--red) !important; }
          .theme-light, body.theme-light {
            --bg: #f6f8fa;
            --panel: #ffffff;
            --panel2: #f3f4f6;
            --border: #d0d7de;
            --text: #24292f;
            --muted: #57606a;
            --blue: #1565c0;
            --green: #2e7d32;
            --red: #b71c1c;

            --yellow: #795548;
            --btn-active-bg: rgba(0,0,0,0.1);
            --red-bg: rgba(183, 28, 28, 0.04);
            --green-bg: rgba(46, 125, 50, 0.04);
            --blue-bg: rgba(21, 101, 192, 0.04);
            --green-flash: #2e7d32;
            --red-flash: #b71c1c;
          }
          body.theme-light {
            background-color: var(--bg) !important;
            color: var(--text) !important;
          }
          .theme-light select, .theme-light input, .theme-light button {
            color-scheme: light !important;
          }
          .theme-light table th {
            background-color: var(--panel2) !important;
          }
          .theme-light table tr:hover td {
            background-color: rgba(0, 0, 0, 0.04) !important;
          }
          .theme-light .blue-chip-row td {
            background-color: rgba(9, 105, 218, 0.05) !important;
          }
      `} />
      <header class="topbar">
        <div>
          <div class="title">
            Dalal Street
          </div>
          <div class="subtitle">
            SolidJS • Real Time Chain • Greeks
          </div>
        </div>

        {/* Main App Navigation Tabs */}
        <div style={{ display: "flex", gap: "10px" }}>
          <button 
            class={activeTab() === "Terminal" ? "expTab active" : "expTab"} 
            onClick={() => setActiveTab("Terminal")}
          >
            Real-Time Terminal
          </button>
          <button 
            class={activeTab() === "Screener" ? "expTab active" : "expTab"} 
            onClick={() => setActiveTab("Screener")}
          >
            Live Screener
          </button>
          <button 
            class={activeTab() === "History" ? "expTab active" : "expTab"} 
            onClick={() => setActiveTab("History")}
          >
            Stock History
          </button>
          <button 
            class={activeTab() === "Results" ? "expTab active" : "expTab"} 
            onClick={() => setActiveTab("Results")}
          >
            Results
          </button>
          <button 
            class={activeTab() === "Forwards" ? "expTab active" : "expTab"} 
            onClick={() => setActiveTab("Forwards")}
          >
            Forward IV
          </button>
          <button 
            class={activeTab() === "Spreads" ? "expTab active" : "expTab"} 
            onClick={() => setActiveTab("Spreads")}
          >
            Spread IV
          </button>
          <button 
            class={activeTab() === "FB Spreads" ? "expTab active" : "expTab"} 
            onClick={() => setActiveTab("FB Spreads")}
          >
            FB Spreads
          </button>
          <button 
            class={activeTab() === "IV Summary" ? "expTab active" : "expTab"} 
            onClick={() => setActiveTab("IV Summary")}
          >
            IV Summary
          </button>
          <button 
            class={activeTab() === "Logs" ? "expTab active" : "expTab"} 
            onClick={() => setActiveTab("Logs")}
          >
            System Logs
          </button>
        </div>

        <div style={{ display: "flex", "align-items": "center", gap: "15px" }}>
          <div class="status">
            {status()}
          </div>
          <button 
            style={{ padding: "4px 8px", background: "var(--panel2)", color: "var(--text)", border: "1px solid var(--border)", "border-radius": "4px", cursor: "pointer", "font-size": "12px", display: "flex", "align-items": "center", gap: "5px", "font-weight": "bold" }}
            onClick={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
            title="Toggle Light/Dark Mode"
          >
            {theme() === 'dark' ? '☀️ Light' : '🌙 Dark'}
          </button>
        </div>
      </header>

      {/* TICKER TAPE */}
      <TickerTape books={books} activeExpiries={activeExpiries} tick={tick} watchlist={watchlist} />

      {/* REAL-TIME TERMINAL VIEW */}
      <Show when={activeTab() === "Terminal"}>
        <div style={{ display: "flex", "flex-direction": "column", flex: 1, minHeight: 0, overflowY: "auto", padding: "15px" }}>
          <div class="toolbar" style={{ "margin-bottom": "20px" }}>
            <div class="workspace">
              Multi Chain Workspace
            </div>

            <button class="addBtn" onClick={addPanel}>
              + Add Panel
            </button>
          </div>

          <div class="gridWrap">
            <For each={panels()}>
              {(p) => (
                <Panel
                  panel={p}
                  books={books}
                  activeExpiries={activeExpiries}
                  symbols={symbols}
                  onRemove={() => removePanel(p.id)}
                  onUpdate={(x) =>
                    updatePanel(p.id, x)
                  }
                />
              )}
            </For>
          </div>
        </div>
      </Show>

      {/* LIVE SCREENER VIEW */}
      <Show when={activeTab() === "Screener"}>
        <LiveScreener 
          symbols={symbols} 
          books={books} 
          activeExpiries={activeExpiries} 
          eodStore={eodStore} 
          overridesStore={overridesStore}
          tick={tick} 
          resultsData={resultsData}
          setActiveTab={setActiveTab}
          setHistorySymbol={setHistorySymbol}
          watchlist={watchlist}
          toggleWatchlist={toggleWatchlist}
        />
      </Show>

      {/* STOCK HISTORY VIEW */}
      <Show when={activeTab() === "History"}>
        <StockHistory 
          symbols={symbols} 
          books={books} 
          activeExpiries={activeExpiries} 
          eodStore={eodStore} 
          overridesStore={overridesStore}
          tick={tick} 
          historySymbol={historySymbol} 
          setHistorySymbol={setHistorySymbol} 
        />
      </Show>

      {/* RESULTS VIEW */}
      <Show when={activeTab() === "Results"}>
        <ResultsView data={resultsData} setData={setResultsData} />
      </Show>

      {/* FORWARD IV VIEW */}
      <Show when={activeTab() === "Forwards"}>
        <ForwardIVView symbols={symbols} books={books} activeExpiries={activeExpiries} eodStore={eodStore} overridesStore={overridesStore} />
      </Show>

      {/* SPREAD IV VIEW */}
      <Show when={activeTab() === "Spreads"}>
        <SpreadIVView symbols={symbols} books={books} activeExpiries={activeExpiries} eodStore={eodStore} overridesStore={overridesStore} />
      </Show>

      {/* FRONT-BACK SPREADS VIEW */}
      <Show when={activeTab() === "FB Spreads"}>
        <FrontBackSpreadsView symbols={symbols} books={books} activeExpiries={activeExpiries} eodStore={eodStore} overridesStore={overridesStore} />
      </Show>

      {/* IV SUMMARY VIEW */}
      <Show when={activeTab() === "IV Summary"}>
        <IVSummaryView symbols={symbols} books={books} activeExpiries={activeExpiries} eodStore={eodStore} setEodStore={setEodStore} sendMessage={sendMessage} overridesStore={overridesStore} setOverridesStore={setOverridesStore} />
      </Show>

      {/* SYSTEM LOGS VIEW */}
      <Show when={activeTab() === "Logs"}>
        <SystemLogsView logs={appLogs} setLogs={setAppLogs} />
      </Show>

    </div>
  );
}
// ─── PLACE THIS WITH YOUR OTHER TOP-LEVEL UTILITY FUNCTIONS ───────────────────
// (near getDTE, calcImpliedVol, normIV — BEFORE FBRow, SpreadRow, SummaryRow)

/**
 * Safely fetches a value from eodStore bypassing expiry format mismatches.
 */
function getEodVal(eodStore, sym, exp, strike, type, isIV = false) {
    if (!eodStore || !sym || !exp || strike == null || !type) return null;
    const suffix = isIV ? `_${type}_IV` : `_${type}`;
    let val = eodStore[`${sym}_${exp}_${strike}${suffix}`];
    if (val != null) return val;
    
    const expClean = exp.replace(/-/g, '').toUpperCase();
    val = eodStore[`${sym}_${expClean}_${strike}${suffix}`];
    if (val != null) return val;
    
    let expHyphen = expClean;
    if (expClean.length >= 7) {
        expHyphen = expClean.substring(0,2) + '-' + expClean.substring(2,5) + '-' + expClean.substring(5);
    }
    val = eodStore[`${sym}_${expHyphen}_${strike}${suffix}`];
    if (val != null) return val;
    
    // O(1) Fast Fallback: Try forcing 4-digit year (if 2-digit) or 2-digit year (if 4-digit)
    if (expClean.length === 7) {
        const expH4 = expClean.substring(0,2) + '-' + expClean.substring(2,5) + '-20' + expClean.substring(5, 7);
        val = eodStore[`${sym}_${expH4}_${strike}${suffix}`];
        if (val != null) return val;
    } else if (expClean.length === 9) {
        const expH2 = expClean.substring(0,2) + '-' + expClean.substring(2,5) + '-' + expClean.substring(7, 9);
        val = eodStore[`${sym}_${expH2}_${strike}${suffix}`];
        if (val != null) return val;
    }
    return null;
}

/**
 * Derives the EOD forward/future price for a given symbol+expiry+strike
 * using put-call parity from EOD CE and PE prices.
 * Returns null if insufficient data.
 */
function getEodFuture(eodStore, sym, expiry, strike, cePx, pePx) {
  // If both CE and PE EOD prices exist, use put-call parity: F = K + C - P
  if (cePx != null && pePx != null && strike != null) {
    const f = strike + cePx - pePx;
    if (f > 0 && isFinite(f)) return f;
  }

  // Fallback: try to find a FUT record in eodStore for this sym+expiry
  if (eodStore && sym && expiry) {
    const futPx = eodStore?.[sym]?.[expiry]?.FUT;
    if (futPx != null && futPx > 0) return futPx;

    // Try alternate expiry key formats (5-char: "07MAY", 7-char: "07MAY25")
    const keys = Object.keys(eodStore?.[sym] ?? {});
    for (const k of keys) {
      if (k.startsWith(expiry.substring(0, 5))) {
        const px = eodStore[sym][k]?.FUT;
        if (px != null && px > 0) return px;
      }
    }
  }

  return null;
}
/* ==========================================================
   FRONT-BACK SPREADS DASHBOARD
========================================================== */

function DetailCell(props) {
  return (
    <Show when={props.o1 && props.o2} fallback={<td style={{ "text-align": 'right', ...props.style }}>-</td>}>
      <td style={{ "font-size": '11px', color: 'var(--muted)', "white-space": 'nowrap', "line-height": '1.4', "text-align": 'right', "font-family": 'monospace', ...props.style }}>
        <div>{props.o1.strike} IV {props.iv1?.toFixed(2) || '-'} Px {props.p1?.toFixed(2) || '-'}</div>
        <div>{props.o2.strike} IV {props.iv2?.toFixed(2) || '-'} Px {props.p2?.toFixed(2) || '-'}</div>
      </td>
    </Show>
  );
}

function FBRow(props) {
  const strike = props.strike;
  const isAtm = createMemo(() => props.baseAtm() !== null && strike === props.baseAtm());

  const co = createMemo(() => props.strikesMap().get(strike)?.CE);
  const po = createMemo(() => props.strikesMap().get(strike)?.PE);
  const coH = createMemo(() => props.strikesMap().get(strike + props.spGap())?.CE);
  const poH = createMemo(() => props.strikesMap().get(strike + props.spGap())?.PE);
  const coL = createMemo(() => props.strikesMap().get(strike - props.spGap())?.CE);
  const poL = createMemo(() => props.strikesMap().get(strike - props.spGap())?.PE);

  const pBackIvDiff = createMemo(() => (poL()?.iv_bid != null && po()?.iv_ask != null) ? normIV(poL().iv_bid) - normIV(po().iv_ask) : null);
  const pBackPxDiff = createMemo(() => (poL()?.put_bid != null && po()?.put_ask != null) ? poL().put_bid - po().put_ask : null);
  const pFrontIvDiff = createMemo(() => (po()?.iv_bid != null && poL()?.iv_ask != null) ? normIV(po().iv_bid) - normIV(poL().iv_ask) : null);
  const pFrontPxDiff = createMemo(() => (po()?.put_bid != null && poL()?.put_ask != null) ? po().put_bid - poL().put_ask : null);
  
  const cFrontIvDiff = createMemo(() => (coH()?.iv_bid != null && co()?.iv_ask != null) ? normIV(coH().iv_bid) - normIV(co().iv_ask) : null);
  const cFrontPxDiff = createMemo(() => (coH()?.call_bid != null && co()?.call_ask != null) ? coH().call_bid - co().call_ask : null);
  const cBackIvDiff = createMemo(() => (co()?.iv_bid != null && coH()?.iv_ask != null) ? normIV(co().iv_bid) - normIV(coH().iv_ask) : null);
  const cBackPxDiff = createMemo(() => (co()?.call_bid != null && coH()?.call_ask != null) ? co().call_bid - coH().call_ask : null);

  // Map EOD premium to IV directly through Black Scholes
  const getEodIv = (s, type) => {
      const precomputedIv = getEodVal(props.eodStore, props.sym, props.expiry, s, type, true);
      return precomputedIv != null ? precomputedIv : null;
  };

  const eodPFrontIvDiff = createMemo(() => {
     const iv1 = getEodIv(strike, 'PE');
     const iv2 = getEodIv(strike - props.spGap(), 'PE');
     return (iv1 != null && iv2 != null) ? iv1 - iv2 : null;
  });
  const eodPBackIvDiff = createMemo(() => {
     const iv1 = getEodIv(strike - props.spGap(), 'PE');
     const iv2 = getEodIv(strike, 'PE');
     return (iv1 != null && iv2 != null) ? iv1 - iv2 : null;
  });
  const eodCFrontIvDiff = createMemo(() => {
     const iv1 = getEodIv(strike + props.spGap(), 'CE');
     const iv2 = getEodIv(strike, 'CE');
     return (iv1 != null && iv2 != null) ? iv1 - iv2 : null;
  });
  const eodCBackIvDiff = createMemo(() => {
     const iv1 = getEodIv(strike, 'CE');
     const iv2 = getEodIv(strike + props.spGap(), 'CE');
     return (iv1 != null && iv2 != null) ? iv1 - iv2 : null;
  });

  return (
    <tr class={isAtm() ? "atmRow" : ""}>
      <td style={{ "text-align": 'center', "font-weight": 'bold', "border-right": '1px solid var(--border)', padding: '6px' }}>
        {strike} {isAtm() && <span style={{ "font-size": '10px', color: 'var(--muted)' }}><br/>(ATM)</span>}
      </td>
      <SpreadCell value={pFrontIvDiff()} eodValue={props.showEod() ? eodPFrontIvDiff() : null} />
      <Show when={props.showPxDiff()}><PriceCell value={pFrontPxDiff()} format={v => v.toFixed(2)} /></Show>
      <Show when={props.showDetails()}><DetailCell o1={po()} o2={poL()} iv1={po()?.iv_bid} iv2={poL()?.iv_ask} p1={po()?.put_bid} p2={poL()?.put_ask} /></Show>
      
      <SpreadCell value={pBackIvDiff()} eodValue={props.showEod() ? eodPBackIvDiff() : null} />
      <Show when={props.showPxDiff()}><PriceCell value={pBackPxDiff()} format={v => v.toFixed(2)} /></Show>
      <Show when={props.showDetails()}><DetailCell o1={poL()} o2={po()} iv1={poL()?.iv_bid} iv2={po()?.iv_ask} p1={poL()?.put_bid} p2={po()?.put_ask} style={{ "border-right": '1px solid var(--border)' }} /></Show>
      
      <SpreadCell value={cFrontIvDiff()} eodValue={props.showEod() ? eodCFrontIvDiff() : null} />
      <Show when={props.showPxDiff()}><PriceCell value={cFrontPxDiff()} format={v => v.toFixed(2)} /></Show>
      <Show when={props.showDetails()}><DetailCell o1={coH()} o2={co()} iv1={coH()?.iv_bid} iv2={co()?.iv_ask} p1={coH()?.call_bid} p2={co()?.call_ask} /></Show>
      
      <SpreadCell value={cBackIvDiff()} eodValue={props.showEod() ? eodCBackIvDiff() : null} />
      <Show when={props.showPxDiff()}><PriceCell value={cBackPxDiff()} format={v => v.toFixed(2)} /></Show>
      <Show when={props.showDetails()}><DetailCell o1={co()} o2={coH()} iv1={co()?.iv_bid} iv2={coH()?.iv_ask} p1={co()?.call_bid} p2={coH()?.call_ask} /></Show>
    </tr>
  );
}

function FBTableInstance(props) {
  const currentSymbol = () => props.config.symbol || 'NIFTY';
  const expiry = () => props.config.expiry;
  const strikeGap = () => props.config.strikeGap || 100;
  const spreadGap = () => props.config.spreadGap || 100;
  const numStrikes = () => props.config.numStrikes || 10;
  const showDetails = () => props.config.showDetails ?? true;
  const showPxDiff = () => props.config.showPxDiff ?? true;
  const showEod = () => props.config.showEod ?? true;

  const localData = createMemo(() => {
    const sym = currentSymbol();
    const exp_list = props.activeExpiries()[sym] || [];
    
    const data = {};
    exp_list.forEach(exp => {
      const book = props.books[`${sym}_${exp}`];
      if (!book) return;
      
      let finalFut = book.future;
      const ovr = props.overridesStore?.futures?.[exp];
      if (ovr) {
          if (ovr.base_expiry) {
              const baseBook = props.books[`${sym}_${ovr.base_expiry}`];
              if (baseBook && baseBook.future != null) finalFut = baseBook.future + ovr.offset;
          } else if (ovr.offset != null && !isNaN(ovr.offset)) {
              finalFut = ovr.offset;
          }
      }
      
      let atm = null;
      if (finalFut && book.rows.length > 0) {
         atm = book.rows.reduce((p, c) => Math.abs(c.k - finalFut) < Math.abs(p.k - finalFut) ? c : p).k;
      }

      const options = [];
      book.rows.forEach(r => {
        options.push({ strike: r.k, OptionType: 'CE', iv_bid: r.ce_bid_iv !== undefined ? r.ce_bid_iv : r.ce_iv, iv_ask: r.ce_ask_iv !== undefined ? r.ce_ask_iv : r.ce_iv, call_bid: r.ce_bid || r.ce_ltp, call_ask: r.ce_ask || r.ce_ltp, chg1d: r.ce_chg1d });
        options.push({ strike: r.k, OptionType: 'PE', iv_bid: r.pe_bid_iv !== undefined ? r.pe_bid_iv : r.pe_iv, iv_ask: r.pe_ask_iv !== undefined ? r.pe_ask_iv : r.pe_iv, put_bid: r.pe_bid || r.pe_ltp, put_ask: r.pe_ask || r.pe_ltp, chg1d: r.pe_chg1d });
      });
      
      data[book.expiry] = {
        atm_strike: props.overridesStore?.strikes?.[exp] || atm,
        next_m_ltp: finalFut,
        options: options
      };
    });
    
    return { expiry_list: exp_list, data: data };
  });


  const expiries = () => localData()?.expiry_list || [];

  createEffect(() => {
    const exps = expiries();
    const e = expiry();
    if (exps.length > 0 && !exps.includes(e)) {
      props.updateConfig({ expiry: exps[0] });
    }
  });

  const expData = () => {
    const e = expiry();
    return e ? localData()?.data?.[e] : null;
  };

  const atmStrike = () => expData()?.atm_strike;
  const futPrice = () => expData()?.next_m_ltp;
  const options = () => expData()?.options || [];

  const strikesMap = createMemo(() => {
    const map = new Map();
    options().forEach(opt => {
      if (!map.has(opt.strike)) map.set(opt.strike, {});
      map.get(opt.strike)[opt.OptionType] = opt;
    });
    return map;
  });

  const strikesWindow = createMemo(() => {
    const atm = atmStrike();
    if (!atm) return [];
    const map = strikesMap();
    const allStrikes = new Set(map.keys());
    const sGap = strikeGap();
    const nStrikes = numStrikes();
    
    const baseAtm = Math.round(atm / sGap) * sGap;
    const half = Math.floor(nStrikes / 2);
    const window = [];
    for (let i = -half; i < nStrikes - half; i++) {
      const s = baseAtm + i * sGap;
      if (allStrikes.has(s)) window.push(s);
    }
    return window;
  });

  const inputStyle = { 
    padding: '4px 8px', "border-radius": '4px', border: '1px solid var(--border)', 
    background: 'var(--panel)', color: 'var(--text)', width: '90px',
    "font-size": '13px', outline: 'none'
  };

  const subCols = () => 1 + (showPxDiff() ? 1 : 0) + (showDetails() ? 1 : 0);
  const mainCols = () => subCols() * 2;

  return (
    <div style={{ "min-width": '800px', background: 'var(--bg)', border: '1px solid var(--border)', "border-radius": '8px', overflow: 'visible' }}>
      <div style={{ padding: '12px 15px', background: 'var(--panel2)', "border-bottom": '1px solid var(--border)', display: 'flex', gap: '15px', "align-items": 'center', "flex-wrap": 'wrap' }}>
        <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Symbol:</label>
        <select style={{ ...inputStyle, width: '100px' }} value={currentSymbol()} onChange={e => props.updateConfig({ symbol: e.target.value })}>
          <For each={props.availableSymbols()}>{s => <option value={s}>{s}</option>}</For>
        </select>

        <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Expiry:</label>
        <select style={{ ...inputStyle, width: '120px' }} value={expiry()} onChange={e => props.updateConfig({ expiry: e.target.value })}>
          <For each={expiries()}>{exp => <option value={exp}>{exp}</option>}</For>
        </select>
        
        <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Strike Gap:</label>
        <input type="number" style={inputStyle} value={strikeGap()} onChange={e => props.updateConfig({ strikeGap: Number(e.target.value) })} />
        
        <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Spread Gap:</label>
        <input type="number" style={inputStyle} value={spreadGap()} onChange={e => props.updateConfig({ spreadGap: Number(e.target.value) })} />
        
        <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Strikes:</label>
        <input type="number" style={inputStyle} value={numStrikes()} onChange={e => props.updateConfig({ numStrikes: Number(e.target.value) })} />

        <label style={{ display: 'flex', "align-items": 'center', gap: '5px', "font-size": '13px', cursor: 'pointer', "font-weight": 'bold' }}>
          <input type="checkbox" checked={showPxDiff()} onChange={e => props.updateConfig({ showPxDiff: e.target.checked })} /> Px Diff
        </label>
        <label style={{ display: 'flex', "align-items": 'center', gap: '5px', "font-size": '13px', cursor: 'pointer', "font-weight": 'bold' }}>
          <input type="checkbox" checked={showDetails()} onChange={e => props.updateConfig({ showDetails: e.target.checked })} /> Details
        </label>
        <label style={{ display: 'flex', "align-items": 'center', gap: '5px', "font-size": '13px', cursor: 'pointer', "font-weight": 'bold' }}>
          <input type="checkbox" checked={showEod()} onChange={e => props.updateConfig({ showEod: e.target.checked })} /> Show EOD
        </label>
        
        <button onClick={props.removeTable} style={{ "margin-left": 'auto', background: 'var(--red)', color: '#fff', border: 'none', "border-radius": '4px', padding: '4px 10px', cursor: 'pointer', "font-weight": 'bold' }}>✕ Remove</button>
      </div>

      <div style={{ padding: '8px 15px', "border-bottom": '1px solid var(--border)', display: 'flex', gap: '20px', "font-size": '13px' }}>
        <span>ATM <strong>{atmStrike() || '-'}</strong></span>
        <span>Futures <strong>{futPrice()?.toFixed(2) || '-'}</strong></span>
      </div>

      <div class="tableWrap" style={{ border: "none", "border-radius": 0, resize: "vertical", overflow: "auto", "min-height": "200px" }}>
        <table style={{ width: '100%', "border-collapse": 'collapse' }}>
          <thead>
            <tr>
              <th rowspan="3" style={{ "text-align": 'center', "border-right": '1px solid var(--border)' }}>Strike</th>
              <th colspan={mainCols()} style={{ "text-align": 'center', "border-right": '1px solid var(--border)', background: 'var(--red-bg)' }}>Put Spreads</th>
              <th colspan={mainCols()} style={{ "text-align": 'center', background: 'var(--green-bg)' }}>Call Spreads</th>
            </tr>
            <tr>
              <th colspan={subCols()} style={{ "text-align": 'center' }}>Put Front</th>
              <th colspan={subCols()} style={{ "text-align": 'center', "border-right": '1px solid var(--border)' }}>Put Back</th>
              <th colspan={subCols()} style={{ "text-align": 'center' }}>Call Front</th>
              <th colspan={subCols()} style={{ "text-align": 'center' }}>Call Back</th>
            </tr>
            <tr>
              <th>IV Diff</th><Show when={showPxDiff()}><th>Px Diff</th></Show><Show when={showDetails()}><th>Detail</th></Show>
              <th>IV Diff</th><Show when={showPxDiff()}><th>Px Diff</th></Show><Show when={showDetails()}><th style={{ "border-right": '1px solid var(--border)' }}>Detail</th></Show>
              <th>IV Diff</th><Show when={showPxDiff()}><th>Px Diff</th></Show><Show when={showDetails()}><th>Detail</th></Show>
              <th>IV Diff</th><Show when={showPxDiff()}><th>Px Diff</th></Show><Show when={showDetails()}><th>Detail</th></Show>
            </tr>
          </thead>
          <tbody>
            <Show when={expiry()} fallback={<tr><td colspan={mainCols() * 2 + 1} style={{ "text-align": 'center', padding: '20px' }} class="empty">No expiry selected</td></tr>}>
              <Show when={strikesWindow().length > 0} fallback={<tr><td colspan={mainCols() * 2 + 1} style={{ "text-align": 'center', padding: '20px' }} class="empty">No data available</td></tr>}>
                <For each={strikesWindow()}>
                  {strike => <FBRow 
                      sym={currentSymbol()}
                      expiry={expiry()}
                      dte={getDTE(expiry())}
                      futPrice={futPrice()}
                      strike={strike} 
                      strikesMap={strikesMap} 
                      spGap={spreadGap} 
                      baseAtm={() => atmStrike() ? Math.round(atmStrike() / strikeGap()) * strikeGap() : null}
                      showEod={showEod} 
                      showPxDiff={showPxDiff} 
                      showDetails={showDetails} 
                      eodStore={props.eodStore}
                  />}
                </For>
              </Show>
            </Show>
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ==========================================================
   SYSTEM LOGS VIEW (Component)
========================================================== */
function SystemLogsView(props) {
  const downloadLogs = () => {
    const blob = new Blob([props.logs().join("\n")], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `dalal_street_logs_${new Date().getTime()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div style={{ padding: "20px", display: "flex", "flex-direction": "column", flex: 1, minHeight: 0 }}>
      <div class="toolbar" style={{ display: "flex", "justify-content": "space-between", "margin-bottom": "10px", padding: "10px 20px", "border-radius": "6px" }}>
        <h2 style={{ margin: 0, color: "var(--text)" }}>System Output Console</h2>
        <div>
          <button class="addBtn" onClick={() => props.setLogs([])} style={{ "margin-right": "10px", background: "var(--red)", border: "none" }}>
            Clear Logs
          </button>
          <button class="addBtn" onClick={downloadLogs}>
            ⬇ Download log.txt
          </button>
        </div>
      </div>
      <div style={{ flex: 1, background: "#1e1e1e", border: "1px solid var(--border)", "border-radius": "6px", padding: "15px", overflowY: "auto", "font-family": "monospace", "font-size": "13px", color: "#4caf50", "white-space": "pre-wrap" }}>
        <For each={props.logs()}>
          {log => <div style={{ "margin-bottom": "4px" }}>{log}</div>}
        </For>
        <Show when={props.logs().length === 0}>
          <div class="empty" style={{ color: "var(--muted)" }}>No logs recorded yet. Listening for backend broadcasts...</div>
        </Show>
      </div>
    </div>
  );
}

function FrontBackSpreadsView(props) {
  const [tables, setTables] = createSignal(null);

  createEffect(() => {
    fetch(`${API_BASE}/api/config?key=fb_multi_tables&user_id=${getUserId()}&t=${Date.now()}`)
      .then(res => {
        if (res.ok) return res.json();
        const saved = localStorage.getItem('fb_multi_tables');
        if (saved) return JSON.parse(saved);
        return null;
      })
      .then(savedConfig => {
        if (savedConfig && savedConfig.value) savedConfig = savedConfig.value; // Handle DB wrapped payload natively
        if (savedConfig?.length > 0) {
          setTables(savedConfig);
        } else {
          setTables([{ id: Date.now(), expiry: '', strikeGap: 100, spreadGap: 100, numStrikes: 10, showDetails: true, showPxDiff: true, symbol: 'NIFTY' }]);
        }
      })
      .catch(() => {
        setTables([{ id: Date.now(), expiry: '', strikeGap: 100, spreadGap: 100, numStrikes: 10, showDetails: true, showPxDiff: true, symbol: 'NIFTY' }]);
      });
  });

  createEffect(() => {
    const t = tables();
    if (t && t.length > 0 && !t[0].expiry) {
      const exps = props.activeExpiries()[t[0].symbol] || [];
      if (exps.length > 0) {
        setTables(t.map((tbl, i) => i === 0 ? { ...tbl, expiry: exps[0] } : tbl));
      }
    }
  });

  createEffect(() => {
    const t = tables();
    if (t) {
      const configPayload = { key: 'fb_multi_tables', value: t, user_id: getUserId() };
      fetch(`${API_BASE}/api/config`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(configPayload) })
        .catch(err => console.error("Failed to save FB summary config to backend", err));
      localStorage.setItem('fb_multi_tables', JSON.stringify(t));
    }
  });

  const addTable = () => {
    const t = tables();
    setTables([...t, { 
      id: Date.now(), 
      expiry: t[0]?.expiry || '', 
      strikeGap: t[0]?.strikeGap || 100, 
      spreadGap: t[0]?.spreadGap || 100, 
      numStrikes: t[0]?.numStrikes || 10, 
      showDetails: t[0]?.showDetails ?? true, 
      showPxDiff: t[0]?.showPxDiff ?? true,
      symbol: t[0]?.symbol || 'NIFTY'
    }]);
  };

  const updateTable = (id, newConfig) => {
    setTables(tables().map(t => t.id === id ? { ...t, ...newConfig } : t));
  };

  const removeTable = (id) => {
    if (tables().length > 1) {
      setTables(tables().filter(t => t.id !== id));
    } else {
      alert("Cannot remove the last table.");
    }
  };

  return (
    <div style={{ display: 'flex', "flex-direction": 'column', flex: 1, minHeight: 0, overflowY: 'auto', padding: '15px' }}>
      <div style={{ display: 'flex', "justify-content": 'space-between', "align-items": 'center', "margin-bottom": '20px' }}>
        <h2 style={{ margin: 0, color: 'var(--text)' }}>Front-Back Spread Dashboard</h2>
        <button onClick={addTable} class="addBtn">
          + Add Comparison Table
        </button>
      </div>
      <Show when={tables()} fallback={<div class="empty">Loading dashboard configuration...</div>}>
      <div style={{ display: 'flex', gap: '20px', "padding-bottom": '20px', "flex-wrap": 'wrap', "align-items": 'flex-start' }}>
          <For each={tables()}>
            {t => (
              <FBTableInstance 
                id={t.id} 
                config={t} 
                updateConfig={(c) => updateTable(t.id, c)} 
                removeTable={() => removeTable(t.id)} 
                availableSymbols={props.symbols}
                books={props.books}
                eodStore={props.eodStore}
                overridesStore={props.overridesStore}
                activeExpiries={props.activeExpiries}
              />
            )}
          </For>
        </div>
      </Show>
    </div>
  );
}

/* ==========================================================
   PANEL
========================================================== */

function Panel(props) {
  const expiries = createMemo(() => props.activeExpiries()[props.panel.symbol] || []);

  createEffect(() => {
    if (
      expiries().length > 0 &&
      !props.panel.expiry
    ) {
      props.onUpdate({
        expiry: expiries()[0]
      });
    }
  });

  const book = createMemo(() => props.panel.expiry ? props.books[`${props.panel.symbol}_${props.panel.expiry}`] || null : null);

  const rows = createMemo(() => {
    if (!book()) return [];

    const r = [...book().rows];

    const fut = book().future;

    if (fut) {
      // Sort by distance to future price (ATM)
      r.sort((a, b) => Math.abs(a.k - fut) - Math.abs(b.k - fut));
      return r.slice(0, props.panel.strikes).sort((a, b) => a.k - b.k);
    }
    
    // Fallback if future price is missing: return strikes around the median
    r.sort((a, b) => a.k - b.k);
    const mid = Math.floor(r.length / 2);
    const half = Math.floor(props.panel.strikes / 2);
    const start = Math.max(0, mid - half);
    
    return r.slice(start, start + props.panel.strikes);
  });

  const future = createMemo(() =>
    book()?.future || null
  );

  const atm = createMemo(() => {
    if (!rows().length || !future()) return null;

    let best = rows()[0].k;
    let diff = Math.abs(best - future());

    for (const r of rows()) {
      const d = Math.abs(r.k - future());

      if (d < diff) {
        diff = d;
        best = r.k;
      }
    }

    return best;
  });

  return (
    <div class="card">

      {/* Controls */}
      <div class="cardHead">

        <select
          class="select"
          value={props.panel.symbol}
          onChange={(e) =>
            props.onUpdate({
              symbol: e.target.value,
              expiry: ""
            })
          }
        >
          <For each={props.symbols()}>
            {(s)=>
              <option value={s}>
                {s}
              </option>
            }
          </For>
        </select>

        <select
          class="select"
          value={props.panel.expiry}
          onChange={(e)=>
            props.onUpdate({
              expiry: e.target.value
            })
          }
        >
          <For each={expiries()}>
            {(e)=>
              <option value={e}>
                {e}
              </option>
            }
          </For>
        </select>

        <select
          class="select small"
          value={props.panel.strikes}
          onChange={(e)=>
            props.onUpdate({
              strikes: +e.target.value
            })
          }
        >
          <option>10</option>
          <option>20</option>
          <option>30</option>
          <option>40</option>
          <option>60</option>
          <option value="999">All</option>
        </select>

        <Show when={props.onRemove && !props.hideClose}>
          <button
            class="removeBtn"
            onClick={props.onRemove}
          >
            ×
          </button>
        </Show>
      </div>

      {/* Ribbon */}
      <div class="metrics">

        <div class="metric">
          <span>Future</span>
          <b>{fmt(future())}</b>
        </div>

        <div class="metric">
          <span>ATM</span>
          <b>{atm() || "-"}</b>
        </div>

        <div class="metric">
          <span>Rows</span>
          <b>{rows().length}</b>
        </div>

      </div>

      {/* Expiry Tabs */}
      <div class="expiryTabs">
        <For each={expiries()}>
          {(ex)=>(
            <button
              class={
                ex === props.panel.expiry
                  ? "expTab active"
                  : "expTab"
              }
              onClick={() =>
                props.onUpdate({
                  expiry: ex
                })
              }
            >
              {ex}
            </button>
          )}
        </For>
      </div>

      {/* Table */}
      <div class="tableWrap" style={{ resize: "vertical", overflow: "auto", "min-height": "200px" }}>

        <Show
          when={rows().length}
          fallback={
            <div class="empty">
              Waiting for data...
            </div>
          }
        >

          <table>
            <thead>
              <tr>
                <th>CE Bid</th>
                <th>CE Ask</th>
                <th>CE LTP</th>
                <th>CE IV</th>
                <th>CE Delta</th>
                <th>CE Gamma</th>
                <th>CE Theta</th>
                <th>CE Vega</th>

                <th class="strike">
                  Strike
                </th>

                <th>PE IV</th>
                <th>PE Delta</th>
                <th>PE Gamma</th>
                <th>PE Theta</th>
                <th>PE Vega</th>
                <th>PE LTP</th>
                <th>PE Bid</th>
                <th>PE Ask</th>
              </tr>
            </thead>

            <tbody>
              <For each={rows()}>
                {(r)=>(
                  <tr
                    class={
                      r.k === atm()
                        ? "atmRow"
                        : ""
                    }
                  >
                    <FlashTd val={r.ce_bid ?? r.ce_ltp} format={fmt} />
                    <FlashTd val={r.ce_ask ?? r.ce_ltp} format={fmt} />
                    <FlashTd val={r.ce_ltp} format={fmt} />
                    <FlashTd val={r.ce_iv} format={iv} />
                    <FlashTd val={r.ce_delta} format={v => greek(v, 2)} class="color-up" />
                    <FlashTd val={r.ce_gamma} format={v => greek(v, 4)} />
                    <FlashTd val={r.ce_theta} format={v => greek(v, 2)} class="color-down" />
                    <FlashTd val={r.ce_vega} format={v => greek(v, 2)} />

                    <td class="strike">
                      {r.k}
                    </td>

                    <FlashTd val={r.pe_iv} format={iv} />
                    <FlashTd val={r.pe_delta} format={v => greek(v, 2)} class="color-down" />
                    <FlashTd val={r.pe_gamma} format={v => greek(v, 4)} />
                    <FlashTd val={r.pe_theta} format={v => greek(v, 2)} class="color-down" />
                    <FlashTd val={r.pe_vega} format={v => greek(v, 2)} />
                    <FlashTd val={r.pe_ltp} format={fmt} />
                    <FlashTd val={r.pe_bid ?? r.pe_ltp} format={fmt} />
                    <FlashTd val={r.pe_ask ?? r.pe_ltp} format={fmt} />
                  </tr>
                )}
              </For>
            </tbody>

          </table>

        </Show>

      </div>

    </div>
  );
}

/* ==========================================================
   SPREAD IV DASHBOARD (Converted from React to SolidJS)
========================================================== */

function calcFwd(t1, ivNear, t2, ivFar, label, strike) {
  if (t1 == null || ivNear == null || t2 == null || ivFar == null || t1 >= t2 || ivNear <= 0 || ivFar <= 0) return null;
  // Normalize inputs to standard decimals (0.xx) for mathematical validity
  const i1 = normIV(ivNear) / 100;
  const i2 = normIV(ivFar) / 100;
  const v1 = Math.pow(i1, 2) * t1;
  const v2 = Math.pow(i2, 2) * t2;
  const result = v2 > v1 ? Math.sqrt((v2 - v1) / (t2 - t1)) : null;
  return result !== null ? result * 100 : null;
}

function SpreadCell(props) {
  return (
    <td style={{ 
      "text-align": 'right', 
      "border-right": props.borderRight ? '1px solid var(--border)' : 'none',
      color: props.value > 0 ? 'var(--green)' : props.value < 0 ? 'var(--red)' : 'inherit',
      "font-weight": 'bold',
      "font-family": 'monospace'
    }}>
      {props.value != null && !isNaN(props.value) ? props.value.toFixed(2) : '-'}
      <div>{props.value != null && !isNaN(props.value) ? props.value.toFixed(2) : '-'}</div>
      <Show when={props.eodValue != null && !isNaN(props.eodValue)}>
      <div style={{ "font-size": '10px', color: 'var(--muted)', "font-weight": 'normal' }}>E: {props.eodValue.toFixed(2)}</div>
      </Show>
    </td>
  );
}

const getOpt = (d, strike, type) => d?.options?.find(o => Number(o.strike) === strike && o.OptionType === type);

function SpreadRow(props) {
  const strike = props.strike;
  const isAtm = createMemo(() => props.baseAtm() !== null && strike === props.baseAtm());

  const ot1 = createMemo(() => props.typeOverrides()[`${strike}_1`] || (props.d1()?.next_m_ltp <= strike ? 'CE' : 'PE'));
  const ot2 = createMemo(() => props.typeOverrides()[`${strike}_2`] || (props.d2()?.next_m_ltp <= strike ? 'CE' : 'PE'));
  const ot3 = createMemo(() => props.typeOverrides()[`${strike}_3`] || (props.d3()?.next_m_ltp <= strike ? 'CE' : 'PE'));

  const o1 = createMemo(() => getOpt(props.d1(), strike, ot1()));
  const o2 = createMemo(() => getOpt(props.d2(), strike, ot2()));
  const o3 = createMemo(() => getOpt(props.d3(), strike, ot3()));

  const f12s = createMemo(() => calcFwd(props.dte1(), o1()?.iv_ask, props.dte2(), o2()?.iv_bid, "", strike));
  const f12l = createMemo(() => calcFwd(props.dte1(), o1()?.iv_bid, props.dte2(), o2()?.iv_ask, "", strike));

  const f23s = createMemo(() => calcFwd(props.dte2(), o2()?.iv_ask, props.dte3(), o3()?.iv_bid, "", strike));
  const f23l = createMemo(() => calcFwd(props.dte2(), o2()?.iv_bid, props.dte3(), o3()?.iv_ask, "", strike));

  const spdS = createMemo(() => (f12s() !== null && f23l() !== null) ? f12s() - f23l() : null);
  const spdL = createMemo(() => (f12l() !== null && f23s() !== null) ? f12l() - f23s() : null);

  const getEodIv = (sym, exp, type) => {
      const precomputedIv = getEodVal(props.eodStore, sym, exp, strike, type, true);
      return precomputedIv != null ? precomputedIv : null;
  };

  const eodSpdS = createMemo(() => {
      const t1 = ot1(), t2 = ot2(), t3 = ot3();
      return untrack(() => {
          const e1_iv = getEodIv(props.sym, props.exp1, t1);
          const e2_iv = getEodIv(props.sym, props.exp2, t2);
          const e3_iv = getEodIv(props.sym, props.exp3, t3);
          if (e1_iv == null || e2_iv == null || e3_iv == null) return null;
          const staticDte1 = Math.floor(getDTE(props.exp1)) + 1;
          const staticDte2 = Math.floor(getDTE(props.exp2)) + 1;
          const staticDte3 = Math.floor(getDTE(props.exp3)) + 1;
          const eodF12s = calcFwd(staticDte1, e1_iv, staticDte2, e2_iv, "", strike);
          const eodF23l = calcFwd(staticDte2, e2_iv, staticDte3, e3_iv, "", strike);
          return (eodF12s !== null && eodF23l !== null) ? eodF12s - eodF23l : null;
      });
  });
  
  const eodSpdL = createMemo(() => {
      const t1 = ot1(), t2 = ot2(), t3 = ot3();
      return untrack(() => {
          const e1_iv = getEodIv(props.sym, props.exp1, t1);
          const e2_iv = getEodIv(props.sym, props.exp2, t2);
          const e3_iv = getEodIv(props.sym, props.exp3, t3);
          if (e1_iv == null || e2_iv == null || e3_iv == null) return null;
          const staticDte1 = Math.floor(getDTE(props.exp1)) + 1;
          const staticDte2 = Math.floor(getDTE(props.exp2)) + 1;
          const staticDte3 = Math.floor(getDTE(props.exp3)) + 1;
          const eodF12l = calcFwd(staticDte1, e1_iv, staticDte2, e2_iv, "", strike);
          const eodF23s = calcFwd(staticDte2, e2_iv, staticDte3, e3_iv, "", strike);
          return (eodF12l !== null && eodF23s !== null) ? eodF12l - eodF23s : null;
      });
  });

  return (
    <tr class={isAtm() ? "atmRow" : ""}>
      <td style={{ "text-align": 'center', "font-weight": 'bold', "border-right": '1px solid var(--border)', padding: '6px' }}>
        {strike} {isAtm() && <span style={{ "font-size": '10px', color: 'var(--muted)' }}><br/>(ATM)</span>}
        <div style={{ display: 'flex', "justify-content": 'center', gap: '2px', "margin-top": '4px' }}>
            <TypeToggler leg={1} strike={strike} typeOverrides={props.typeOverrides} setTypeOverrides={props.setTypeOverrides} autoVal={props.d1()?.next_m_ltp <= strike ? 'CE' : 'PE'} />
            <TypeToggler leg={2} strike={strike} typeOverrides={props.typeOverrides} setTypeOverrides={props.setTypeOverrides} autoVal={props.d2()?.next_m_ltp <= strike ? 'CE' : 'PE'} />
            <TypeToggler leg={3} strike={strike} typeOverrides={props.typeOverrides} setTypeOverrides={props.setTypeOverrides} autoVal={props.d3()?.next_m_ltp <= strike ? 'CE' : 'PE'} />
        </div>
      </td>
      <FlashTd val={f12s()} format={v => v.toFixed(2)} style={{ "text-align": 'right' }} />
      <FlashTd val={f12l()} format={v => v.toFixed(2)} style={{ "text-align": 'right', "border-right": '1px solid var(--border)' }} />
      <FlashTd val={f23s()} format={v => v.toFixed(2)} style={{ "text-align": 'right' }} />
      <FlashTd val={f23l()} format={v => v.toFixed(2)} style={{ "text-align": 'right', "border-right": '1px solid var(--border)' }} />
      <SpreadCell value={spdS()} />
      <SpreadCell value={spdL()} borderRight={props.showEod() || props.showLegDetails()} />
      
      <Show when={props.showEod()}>
        <SpreadCell value={eodSpdS()} />
        <SpreadCell value={eodSpdL()} borderRight={props.showLegDetails()} />
      </Show>

      <Show when={props.showLegDetails()}>
          <FlashTd val={normIV(o1()?.iv_bid)} format={v => v.toFixed(2)} style={{ "text-align": 'right' }} />
          <FlashTd val={normIV(o1()?.iv_ask)} format={v => v.toFixed(2)} style={{ "text-align": 'right', "border-right": '1px solid var(--border)' }} />
          <FlashTd val={normIV(o2()?.iv_bid)} format={v => v.toFixed(2)} style={{ "text-align": 'right' }} />
          <FlashTd val={normIV(o2()?.iv_ask)} format={v => v.toFixed(2)} style={{ "text-align": 'right', "border-right": '1px solid var(--border)' }} />
          <FlashTd val={normIV(o3()?.iv_bid)} format={v => v.toFixed(2)} style={{ "text-align": 'right' }} />
          <FlashTd val={normIV(o3()?.iv_ask)} format={v => v.toFixed(2)} style={{ "text-align": 'right' }} />
      </Show>
    </tr>
  );
}

function SpreadTableInstance(props) {
  const currentSymbol = () => props.config.symbol || 'NIFTY';
  const exp1 = () => props.config.exp1;
  const exp2 = () => props.config.exp2;
  const exp3 = () => props.config.exp3;
  const gap = () => props.config.gap || 100;
  const numStrikes = () => props.config.numStrikes || 15;
  const showLegDetails = () => props.config.showLegDetails ?? true;
  const showEod = () => props.config.showEod ?? true;

  const [eodData, setEodData] = createSignal(null);
  const [typeOverrides, setTypeOverrides] = createSignal({});

  const localData = createMemo(() => {
    const sym = currentSymbol();
    const exp_list = props.activeExpiries()[sym] || [];
    
    const data = {};
    exp_list.forEach(exp => {
      const book = props.books[`${sym}_${exp}`];
      if (!book) return;
      
      let finalFut = book.future;
      const ovr = props.overridesStore?.futures?.[exp];
      if (ovr) {
          if (ovr.base_expiry) {
              const baseBook = props.books[`${sym}_${ovr.base_expiry}`];
              if (baseBook && baseBook.future != null) finalFut = baseBook.future + ovr.offset;
          } else if (ovr.offset != null && !isNaN(ovr.offset)) {
              finalFut = ovr.offset;
          }
      }
      
      let atm = null;
      if (finalFut && book.rows.length > 0) {
         atm = book.rows.reduce((p, c) => Math.abs(c.k - finalFut) < Math.abs(p.k - finalFut) ? c : p).k;
      }

      const options = [];
      book.rows.forEach(r => {
        options.push({ strike: r.k, OptionType: 'CE', iv_bid: r.ce_bid_iv !== undefined ? r.ce_bid_iv : r.ce_iv, iv_ask: r.ce_ask_iv !== undefined ? r.ce_ask_iv : r.ce_iv });
        options.push({ strike: r.k, OptionType: 'PE', iv_bid: r.pe_bid_iv !== undefined ? r.pe_bid_iv : r.pe_iv, iv_ask: r.pe_ask_iv !== undefined ? r.pe_ask_iv : r.pe_iv });
      });
      
      data[book.expiry] = {
        atm_strike: props.overridesStore?.strikes?.[exp] || atm,
        next_m_ltp: finalFut,
        options: options
      };
    });
    
    return { expiry_list: exp_list, data: data };
  });

  createEffect(() => {
      const sym = currentSymbol();
      let isMounted = true;
      const fetchEod = async () => {
          try {
              const res = await fetch(`${API_BASE}/api/portfolio/data?user_id=${getUserId()}&symbol=${sym}&t=${Date.now()}`);
              if (res.ok) {
                  const d = await res.json();
                  if (isMounted && d.status === 'success') setEodData(d.eod_data);
              }
          } catch(e) {}
      };
      fetchEod();
      const timer = setInterval(fetchEod, 15000);
      onCleanup(() => { isMounted = false; clearInterval(timer); });
  });

  const sourceData = () => localData();
  const expiries = () => sourceData()?.expiry_list || [];
  
  const d1 = () => sourceData()?.data?.[exp1()];
  const d2 = () => sourceData()?.data?.[exp2()];
  const d3 = () => sourceData()?.data?.[exp3()];

  createEffect(() => {
      const exps = expiries();
      const e1 = exp1();
      const e2 = exp2();
      const e3 = exp3();
      if (exps.length > 0) {
          let changed = false;
          let nextConfig = {};
          if (!exps.includes(e1)) { nextConfig.exp1 = exps[0]; changed = true; }
          if (!exps.includes(e2)) { nextConfig.exp2 = exps[1] || exps[0]; changed = true; }
          if (!exps.includes(e3)) { nextConfig.exp3 = exps[2] || exps[0]; changed = true; }
          if (changed) props.updateConfig(nextConfig);
      }
  });

  const validSelection = () => exp1() && exp2() && exp3() && exp1() !== exp2() && exp1() !== exp3() && exp2() !== exp3();

  const strikesWindow = createMemo(() => {
    if (!validSelection() || !d1() || !d2() || !d3()) return [];
    const s1 = d1().options?.map(o => Number(o.strike)) || [];
    const s2 = d2().options?.map(o => Number(o.strike)) || [];
    const s3 = d3().options?.map(o => Number(o.strike)) || [];
    const allStrikes = new Set([...s1, ...s2, ...s3]);
    
    const atm = d1().atm_strike;
    if (!atm) return [];
    
    const baseAtm = Math.round(atm / gap()) * gap();
    const half = Math.floor(numStrikes() / 2);
    const window = [];
    for (let i = -half; i < numStrikes() - half; i++) {
      const s = baseAtm + i * gap();
      if (allStrikes.has(s)) window.push(s);
    }
    return window;
  });

  const dte1 = () => getDTE(exp1());
  const dte2 = () => getDTE(exp2());
  const dte3 = () => getDTE(exp3());

  const inputStyle = { 
      padding: '4px 8px', "border-radius": '4px', border: '1px solid var(--border)', 
      background: 'var(--panel)', color: 'var(--text)', width: '90px',
      "font-size": '13px', outline: 'none'
  };
  
  const totalCols = () => showLegDetails() ? (showEod() ? 15 : 13) : (showEod() ? 9 : 7);

  return (
    <div style={{ "min-width": '950px', background: 'var(--bg)', border: '1px solid var(--border)', "border-radius": '8px', overflow: 'visible' }}>
      <div style={{ padding: '12px 15px', background: 'var(--panel2)', "border-bottom": '1px solid var(--border)', display: 'flex', gap: '15px', "align-items": 'center', "flex-wrap": 'wrap' }}>
          <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Symbol:</label>
          <select style={{ ...inputStyle, width: '100px' }} value={currentSymbol()} onChange={e => props.updateConfig({ symbol: e.target.value })}>
              <For each={props.availableSymbols()}>
                  {s => <option value={s}>{s}</option>}
              </For>
          </select>

          <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Exp 1:</label>
          <select style={{ ...inputStyle, width: '110px' }} value={exp1()} onChange={e => props.updateConfig({ exp1: e.target.value })}>
              <For each={expiries()}>{exp => <option value={exp}>{exp}</option>}</For>
          </select>
          
          <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Exp 2:</label>
          <select style={{ ...inputStyle, width: '110px' }} value={exp2()} onChange={e => props.updateConfig({ exp2: e.target.value })}>
              <For each={expiries()}>{exp => <option value={exp}>{exp}</option>}</For>
          </select>

          <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Exp 3:</label>
          <select style={{ ...inputStyle, width: '110px' }} value={exp3()} onChange={e => props.updateConfig({ exp3: e.target.value })}>
              <For each={expiries()}>{exp => <option value={exp}>{exp}</option>}</For>
          </select>
          
          <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Gap:</label>
          <input type="number" style={{ ...inputStyle, width: '60px' }} value={gap()} onChange={e => props.updateConfig({ gap: Number(e.target.value) })} />
          
          <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Strikes:</label>
          <input type="number" style={{ ...inputStyle, width: '60px' }} value={numStrikes()} onChange={e => props.updateConfig({ numStrikes: Number(e.target.value) })} />

          <label style={{ display: 'flex', "align-items": 'center', gap: '5px', "font-size": '13px', cursor: 'pointer', "font-weight": 'bold', "margin-left": '5px' }}>
            <input type="checkbox" checked={showLegDetails()} onChange={e => props.updateConfig({ showLegDetails: e.target.checked })} /> Show Legs
          </label>
          <label style={{ display: 'flex', "align-items": 'center', gap: '5px', "font-size": '13px', cursor: 'pointer', "font-weight": 'bold', "margin-left": '5px' }}>
            <input type="checkbox" checked={showEod()} onChange={e => props.updateConfig({ showEod: e.target.checked })} /> Show EOD
          </label>

          <button onClick={props.removeTable} style={{ "margin-left": 'auto', background: 'var(--red)', color: '#fff', border: 'none', "border-radius": '4px', padding: '4px 10px', cursor: 'pointer', "font-weight": 'bold' }}>
            ✕ Remove
          </button>
      </div>

      <Show when={validSelection() && d1() && d2() && d3()}>
          <div style={{ padding: '8px 15px', "border-bottom": '1px solid var(--border)', display: 'flex', gap: '20px', "font-size": '13px' }}>
              <div><strong>Exp 1:</strong> Fut <strong>{d1()?.next_m_ltp?.toFixed(2) || '-'}</strong> ATM <strong>{d1()?.atm_strike || '-'}</strong></div>
              <div><strong>Exp 2:</strong> Fut <strong>{d2()?.next_m_ltp?.toFixed(2) || '-'}</strong> ATM <strong>{d2()?.atm_strike || '-'}</strong></div>
              <div><strong>Exp 3:</strong> Fut <strong>{d3()?.next_m_ltp?.toFixed(2) || '-'}</strong> ATM <strong>{d3()?.atm_strike || '-'}</strong></div>
          </div>
      </Show>

      <div class="tableWrap" style={{ border: "none", "border-radius": 0, resize: "vertical", overflow: "auto", "min-height": "200px" }}>
        <table style={{ width: '100%', "border-collapse": 'collapse' }}>
          <thead>
            <tr>
              <th rowspan="2" style={{ "text-align": 'center', "border-right": '1px solid var(--border)' }}>Strike</th>
              <th colspan="2" style={{ "text-align": 'center', background: 'var(--green-bg)', "border-right": '1px solid var(--border)' }}>Fwd 1→2</th>
              <th colspan="2" style={{ "text-align": 'center', background: 'var(--green-bg)', "border-right": '1px solid var(--border)' }}>Fwd 2→3</th>
              <th colspan="2" style={{ "text-align": 'center', "border-right": '1px solid var(--border)' }}>Spd (1→2)-(2→3)</th>
              <Show when={showEod()}>
                 <th colspan="2" style={{ "text-align": 'center', "border-right": showLegDetails() ? '1px solid var(--border)' : 'none' }}>EOD Spd</th>
              </Show>
              <Show when={showLegDetails()}>
                 <th colspan="2" style={{ "text-align": 'center', background: 'var(--blue-bg)', "border-right": '1px solid var(--border)' }}>Exp 1</th>
                 <th colspan="2" style={{ "text-align": 'center', background: 'var(--blue-bg)', "border-right": '1px solid var(--border)' }}>Exp 2</th>
                 <th colspan="2" style={{ "text-align": 'center', background: 'var(--blue-bg)' }}>Exp 3</th>
              </Show>
            </tr>
            <tr>
              <th style={{ "text-align": 'right' }}>Short</th><th style={{ "text-align": 'right', "border-right": '1px solid var(--border)' }}>Long</th>
              <th style={{ "text-align": 'right' }}>Short</th><th style={{ "text-align": 'right', "border-right": '1px solid var(--border)' }}>Long</th>
              <th style={{ "text-align": 'right' }}>Short</th><th style={{ "text-align": 'right', "border-right": showEod() ? '1px solid var(--border)' : (showLegDetails() ? '1px solid var(--border)' : 'none') }}>Long</th>
              <Show when={showEod()}>
                 <th style={{ "text-align": 'right' }}>EOD S</th><th style={{ "text-align": 'right', "border-right": showLegDetails() ? '1px solid var(--border)' : 'none' }}>EOD L</th>
              </Show>
              <Show when={showLegDetails()}>
                 <th style={{ "text-align": 'right' }}>Bid IV</th><th style={{ "text-align": 'right', "border-right": '1px solid var(--border)' }}>Ask IV</th>
                 <th style={{ "text-align": 'right' }}>Bid IV</th><th style={{ "text-align": 'right', "border-right": '1px solid var(--border)' }}>Ask IV</th>
                 <th style={{ "text-align": 'right' }}>Bid IV</th><th style={{ "text-align": 'right' }}>Ask IV</th>
              </Show>
            </tr>
          </thead>
          <tbody>
            <Show when={!validSelection()}>
              <tr><td colspan={totalCols()} style={{ "text-align": 'center', padding: '20px' }} class="empty">Select three different expiries</td></tr>
            </Show>
            <Show when={validSelection() && strikesWindow().length === 0}>
              <tr><td colspan={totalCols()} style={{ "text-align": 'center', padding: '20px' }} class="empty">No strikes available</td></tr>
            </Show>
            <Show when={validSelection() && strikesWindow().length > 0}>
              <For each={strikesWindow()}>
                {strike => <SpreadRow 
                    sym={currentSymbol()}
                    exp1={exp1()} exp2={exp2()} exp3={exp3()}
                    eodStore={props.eodStore}
                    strike={strike} 
                    d1={d1} d2={d2} d3={d3} 
                    dte1={dte1} dte2={dte2} dte3={dte3}
                    typeOverrides={typeOverrides} setTypeOverrides={setTypeOverrides}
                    baseAtm={() => d1()?.atm_strike ? Math.round(d1().atm_strike / gap()) * gap() : null}
                    showEod={showEod} showLegDetails={showLegDetails}
                />}
              </For>
            </Show>
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SpreadIVView(props) {
  const [tables, setTables] = createSignal(null);

  // Load config from backend on mount, with localStorage fallback
  createEffect(() => {
    fetch(`${API_BASE}/api/config?key=spr_multi_tables&user_id=${getUserId()}&t=${Date.now()}`)
      .then(res => {
        if (res.ok) return res.json();
        const saved = localStorage.getItem('spr_multi_tables');
        if (saved) return JSON.parse(saved);
        return null;
      })
      .then(savedConfig => {
        if (savedConfig && savedConfig.value) savedConfig = savedConfig.value;
        if (savedConfig?.length > 0) {
          setTables(savedConfig);
        } else {
          setTables([{ id: Date.now(), exp1: '', exp2: '', exp3: '', gap: 100, numStrikes: 15, symbol: 'NIFTY', showLegDetails: true }]);
        }
      })
      .catch(() => {
        setTables([{ id: Date.now(), exp1: '', exp2: '', exp3: '', gap: 100, numStrikes: 15, symbol: 'NIFTY', showLegDetails: true }]);
      });
  });

  // Save config to backend and localStorage on change
  createEffect(() => {
    const t = tables();
    if (t) {
      const configPayload = { key: 'spr_multi_tables', value: t, user_id: getUserId() };
      fetch(`${API_BASE}/api/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(configPayload)
      }).catch(err => console.error("Failed to save spreads config to backend", err));
      localStorage.setItem('spr_multi_tables', JSON.stringify(t));
    }
  });

  const addTable = () => {
      const t = tables();
      setTables([...t, { 
          id: Date.now(), 
          exp1: t[0]?.exp1 || '', 
          exp2: t[0]?.exp2 || '', 
          exp3: t[0]?.exp3 || '', 
          gap: t[0]?.gap || 100, 
          numStrikes: t[0]?.numStrikes || 15,
          symbol: t[0]?.symbol || 'NIFTY',
          showLegDetails: t[0]?.showLegDetails ?? true
      }]);
  };

  const updateTable = (id, newConfig) => {
      setTables(tables().map(t => t.id === id ? { ...t, ...newConfig } : t));
  };

  const removeTable = (id) => {
      if (tables().length > 1) setTables(tables().filter(t => t.id !== id));
      else alert("Cannot remove the last table.");
  };

  return (
      <div style={{ display: 'flex', "flex-direction": 'column', flex: 1, minHeight: 0, overflowY: 'auto', padding: '15px' }}>
          <div style={{ display: 'flex', "justify-content": 'space-between', "align-items": 'center', "margin-bottom": '20px' }}>
              <h2 style={{ margin: 0, color: 'var(--text)' }}>3-Expiry Spread Dashboard</h2>
              <button onClick={addTable} class="addBtn">
                  + Add Comparison Table
              </button>
          </div>
          <Show when={tables()} fallback={<div class="empty">Loading dashboard configuration...</div>}>
          <div style={{ display: 'flex', gap: '20px', "padding-bottom": '20px', "flex-wrap": 'wrap', "align-items": 'flex-start' }}>
                  <For each={tables()}>
                      {t => (
                          <SpreadTableInstance 
                              id={t.id} 
                              config={t} 
                              updateConfig={(c) => updateTable(t.id, c)} 
                              removeTable={() => removeTable(t.id)} 
                              availableSymbols={props.symbols}
                              books={props.books}
                              eodStore={props.eodStore}
                              activeExpiries={props.activeExpiries}
                              overridesStore={props.overridesStore}
                          />
                      )}
                  </For>
              </div>
          </Show>
      </div>
  );
}

/* ==========================================================
   FORWARD IV DASHBOARD (Converted from React to SolidJS)
========================================================== */

const getUserId = () => {
  let uid = localStorage.getItem('screener_user_id');
  if (!uid) {
    uid = 'user_' + Math.random().toString(36).substr(2, 9);
    localStorage.setItem('screener_user_id', uid);
  }
  return uid;
};

function getDTE(expiryStr) {
  if (!expiryStr) return 0;
  let d, m, y;
  const months = { "JAN":0, "FEB":1, "MAR":2, "APR":3, "MAY":4, "JUN":5, "JUL":6, "AUG":7, "SEP":8, "OCT":9, "NOV":10, "DEC":11 };
  
  let impliedYear = false;

  if (expiryStr.includes('-')) {
    const parts = expiryStr.split('-');
    if (parts.length !== 3) return 0.000001;
    d = parseInt(parts[0], 10);
    m = months[parts[1].substring(0, 3).toUpperCase()];
    y = parseInt(parts[2], 10);
    if (y < 100) y += 2000;
  } else {
    const match = expiryStr.match(/^(\d{1,2})([A-Z]{3})(\d{2})?$/i);
    if (!match) return 0.000001;
    d = parseInt(match[1], 10);
    m = months[match[2].substring(0, 3).toUpperCase()];
    if (match[3]) {
        y = parseInt(match[3], 10) + 2000;
    } else {
        y = new Date().getFullYear();
        impliedYear = true;
    }
  }

  if (m === undefined) return 0.000001;

  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  
  const expDate = new Date(y, m, d);
  expDate.setHours(0, 0, 0, 0);

  // If the date is strictly in the past and we assumed the year, dynamically bump to next year
  if (expDate.getTime() < today.getTime() && impliedYear) {
      expDate.setFullYear(y + 1);
  }
  
  let daysDiff = Math.round((expDate.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
  
  // Trading Day Fraction Normalization (9:15 to 15:30)
  const currentMinutes = now.getHours() * 60 + now.getMinutes() + now.getSeconds() / 60;
  const startMins = 9 * 60 + 15; // 555
  const endMins = 15 * 60 + 30;  // 930
  const totalTradingMins = endMins - startMins; // 375
  
  let fraction = 0;
  if (currentMinutes < startMins) {
      fraction = 1;
  } else if (currentMinutes >= endMins) {
      fraction = 0;
  } else {
      fraction = (endMins - currentMinutes) / totalTradingMins;
  }
  
  let diff = daysDiff + fraction;

  return Math.max(0.000001, diff);
}

function calculateForwardIV(dte1, ivAsk1, ivBid1, dte2, ivBid2, ivAsk2, strike) {
    if (!dte1 || !dte2 || dte1 <= 0 || dte2 <= 0 || dte1 >= dte2) return { short: null, long: null };

    const calc = (i1, i2, label) => {
        if (i1 === null || i2 === null || !isFinite(i1) || !isFinite(i2) || i1 <= 0 || i2 <= 0) return null;
        const v1 = dte1 * Math.pow(i1 / 100, 2);
        const v2 = dte2 * Math.pow(i2 / 100, 2);
        const v = (v2 - v1) / (dte2 - dte1);
        const result = v > 0 ? Math.sqrt(v) * 100 : null;
        return result;
    };
    const s = calc(ivAsk1, ivBid2, "Short (Near Ask vs Far Bid)");
    const l = calc(ivBid1, ivAsk2, "Long (Near Bid vs Far Ask)");
    return { short: s, long: l };
}

function PriceCell(props) {
  const [flashColor, setFlashColor] = createSignal(null);
  let timer = null;
  
  createEffect((prev) => {
    const cur = props.value;
    if (prev !== undefined && cur != null && prev != null && cur !== prev) {
      setFlashColor(cur > prev ? "var(--green-flash, #43a047)" : "var(--red-flash, #e53935)");
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        setFlashColor(null);
      }, 300);
    }
    return cur;
  });

  return (
    <td class={props.class || ""} style={{ 
      "text-align": "right", 
      color: flashColor() || undefined,
      transition: flashColor() ? "none" : "color 0.6s ease-out",
      ...props.style 
    }}>
      {props.value != null && !isNaN(props.value) ? props.format(props.value) : '-'}
    </td>
  );
}

function FlashTd(props) {
  const [flashColor, setFlashColor] = createSignal(null);
  let timer = null;

  createEffect((prev) => {
    const cur = props.val;
    if (prev !== undefined && cur != null && prev != null && cur !== prev) {
      setFlashColor(cur > prev ? "var(--green-flash, #43a047)" : "var(--red-flash, #e53935)");
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        setFlashColor(null);
      }, 300);
    }
    return cur;
  });

  return (
    <td class={props.class || ""} style={{
      color: flashColor() || undefined,
      transition: flashColor() ? "none" : "color 0.6s ease-out",
      ...props.style
    }}>
      {props.display !== undefined ? props.display : ((props.val != null && !Number.isNaN(props.val)) ? (props.format ? props.format(props.val) : props.val) : '-')}
    </td>
  );
}

/* ==========================================================
   IV SUMMARY DASHBOARD
========================================================== */

function EditableCell(props) {
  const [val, setVal] = createSignal(props.value !== null && props.value !== undefined ? props.value : '');
  const [isFocused, setIsFocused] = createSignal(false);

  createEffect(() => {
    const newVal = props.value !== null && props.value !== undefined ? props.value : '';
    if (!isFocused()) {
      setVal(newVal);
    }
  });

  const handleBlur = () => {
    setIsFocused(false);
    const numVal = parseFloat(val());
    if (val() === '') {
      props.onUpdate(null);
    } else if (!isNaN(numVal) && numVal !== props.value) {
      props.onUpdate(numVal);
    } else {
      setVal(props.value !== null && props.value !== undefined ? props.value : '');
    }
  };

  const handleFocus = () => {
    setIsFocused(true);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') e.target.blur();
  };

  return (
    <input
      type="number"
      class="select"
      style={{ width: "65px", "text-align": "center", ...(props.isOverridden ? { "border-color": "var(--green)" } : {}) }}
      value={val()}
      onInput={(e) => setVal(e.target.value)}
      onFocus={handleFocus}
      onBlur={handleBlur}
      onKeyDown={handleKeyDown}
      placeholder={props.placeholder}
      title={props.title}
    />
  );
}

function InlineOverrides(props) {
  const activeFut = () => props.sourceData()?.overrides?.[props.expiry];
  const activeStrike = () => props.sourceData()?.strike_overrides?.[props.expiry];
  const activeGap = () => props.sourceData()?.gap_overrides?.[props.expiry];

  const [baseExp, setBaseExp] = createSignal('');
  const [fut, setFut] = createSignal('');
  const [strike, setStrike] = createSignal('');
  const [gap, setGap] = createSignal('');

  const inputStyle = { 
    width: '65px', padding: '3px 5px', "font-size": '11px', 
    "margin-right": '6px', background: 'var(--panel)', 
    color: 'var(--text)', border: '1px solid var(--border)', "border-radius": '3px'
  };
  
  const btnStyle = { 
    padding: '3px 8px', "font-size": '11px', cursor: 'pointer',
    background: 'var(--panel2)', color: 'var(--text)',
    border: '1px solid var(--border)', "border-radius": '3px', "min-width": '35px'
  };

  const btnDangerStyle = { ...btnStyle, background: 'var(--red)', color: '#fff', border: 'none' };
  const btnSuccessStyle = { ...btnStyle, background: 'var(--green)', color: '#fff', border: 'none' };

  const handleFut = () => {
    if (!fut() && !baseExp() && activeFut()) {
      props.setOverridesStore("futures", props.expiry, null);
      props.sendMessage({ action: 'delete_future_override', target: props.expiry, symbol: props.symbol, user_id: getUserId() });
    } else if (fut() !== '' || baseExp() !== '') {
      props.setOverridesStore("futures", props.expiry, { base_expiry: baseExp() || null, offset: parseFloat(fut() || 0) });
      props.sendMessage({ action: 'set_future_override', target: props.expiry, base: baseExp() || null, offset: parseFloat(fut() || 0), symbol: props.symbol, user_id: getUserId() });
      setFut('');
      setBaseExp('');
    }
  };

  const handleStrike = () => {
    if (!strike() && activeStrike()) {
      props.setOverridesStore("strikes", props.expiry, null);
      props.sendMessage({ action: 'set_override', target: props.expiry, value: null, symbol: props.symbol, user_id: getUserId() });
    } else if (strike()) {
      props.setOverridesStore("strikes", props.expiry, parseFloat(strike()));
      props.sendMessage({ action: 'set_override', target: props.expiry, value: parseFloat(strike()), symbol: props.symbol, user_id: getUserId() });
      setStrike('');
    }
  };

  const handleGap = () => {
    if (!gap() && activeGap()) {
      props.setOverridesStore("gaps", props.expiry, null);
      props.sendMessage({ action: 'set_gap_override', target: props.expiry, value: null, symbol: props.symbol, user_id: getUserId() });
    } else if (gap()) {
      props.setOverridesStore("gaps", props.expiry, parseFloat(gap()));
      props.sendMessage({ action: 'set_gap_override', target: props.expiry, value: parseFloat(gap()), symbol: props.symbol, user_id: getUserId() });
      setGap('');
    }
  };

  return (
    <div style={{ display: 'flex', "flex-direction": 'column', gap: '5px', "text-align": 'left' }}>
      <div style={{ display: 'flex', "align-items": 'center', "justify-content": 'space-between' }}>
        <span style={{ width: '30px', "font-size": '11px', "font-weight": 'bold', color: activeFut() ? 'var(--green)' : 'var(--muted)' }}>Fut:</span>
        <select style={{ ...inputStyle, width: '85px', padding: '2px 4px' }} value={baseExp()} onChange={e => setBaseExp(e.target.value)}>
          <option value="">{activeFut()?.base_expiry ? `[${activeFut().base_expiry}]` : 'Fixed'}</option>
          <For each={props.sourceData()?.expiry_list || []}>
            {ex => <option value={ex}>{ex}</option>}
          </For>
        </select>
        <input style={{...inputStyle, width: '50px'}} placeholder={activeFut() ? activeFut().offset : "Price/Off"} value={fut()} onChange={e => setFut(e.target.value)} type="number" />
        <button style={(!fut() && !baseExp()) && activeFut() ? btnDangerStyle : ((fut() || baseExp()) ? btnSuccessStyle : btnStyle)} onClick={handleFut}>
          {(!fut() && !baseExp()) && activeFut() ? 'Clr' : 'Set'}
        </button>
      </div>
      <div style={{ display: 'flex', "align-items": 'center', "justify-content": 'space-between' }}>
        <span style={{ width: '30px', "font-size": '11px', "font-weight": 'bold', color: activeStrike() ? 'var(--green)' : 'var(--muted)' }}>Strk:</span>
        <input style={inputStyle} placeholder={activeStrike() || "Auto"} value={strike()} onChange={e => setStrike(e.target.value)} type="number" />
        <button style={!strike() && activeStrike() ? btnDangerStyle : (strike() ? btnSuccessStyle : btnStyle)} onClick={handleStrike}>
          {!strike() && activeStrike() ? 'Clr' : 'Set'}
        </button>
      </div>
      <div style={{ display: 'flex', "align-items": 'center', "justify-content": 'space-between' }}>
        <span style={{ width: '30px', "font-size": '11px', "font-weight": 'bold', color: activeGap() ? 'var(--green)' : 'var(--muted)' }}>Gap:</span>
        <input style={inputStyle} placeholder={activeGap() || "Auto"} value={gap()} onChange={e => setGap(e.target.value)} type="number" />
        <button style={!gap() && activeGap() ? btnDangerStyle : (gap() ? btnSuccessStyle : btnStyle)} onClick={handleGap}>
          {!gap() && activeGap() ? 'Clr' : 'Set'}
        </button>
      </div>
    </div>
  );
}

function SummaryRow(props) {
  const expData = createMemo(() => props.sourceData()?.data?.[props.expiryStr] || {});
  const atmOptions = createMemo(() => expData().options?.filter(o => o.strike === expData().atm_strike) || []);
  
  const ceOpt = createMemo(() => atmOptions().find(o => o.OptionType === 'CE') || {});
  const peOpt = createMemo(() => atmOptions().find(o => o.OptionType === 'PE') || {});

  const efut = createMemo(() => {
      const cePx = getEodVal(props.eodStore, props.currentSymbol, props.expiryStr, expData().atm_strike, 'CE');
      const pePx = getEodVal(props.eodStore, props.currentSymbol, props.expiryStr, expData().atm_strike, 'PE');
      return getEodFuture(props.eodStore, props.currentSymbol, props.expiryStr, expData().atm_strike, cePx, pePx);
  });

  const eodCeOpt = createMemo(() => {
      const precomputedIv = getEodVal(props.eodStore, props.currentSymbol, props.expiryStr, expData().atm_strike, 'CE', true);
      return { iv_ltp: precomputedIv != null ? precomputedIv : null };
  });
  
  const eodPeOpt = createMemo(() => {
      const precomputedIv = getEodVal(props.eodStore, props.currentSymbol, props.expiryStr, expData().atm_strike, 'PE', true);
      return { iv_ltp: precomputedIv != null ? precomputedIv : null };
  });

  const isGapOvr = createMemo(() => props.sourceData()?.gap_overrides?.[props.expiryStr] != null);
  const isStrikeOvr = createMemo(() => props.sourceData()?.strike_overrides?.[props.expiryStr] != null);
  const isFutOvr = createMemo(() => props.sourceData()?.overrides?.[props.expiryStr] != null);

  const calcNotes = () => {
    const futureValues = props.sourceData()?.future_values || {};
    const fData = Object.values(futureValues).find(fv => fv.expiry === props.expiryStr);
    return fData?.calc_notes || "Calculation notes not available.";
  };

  return (
    <tr>
      <td style={{ "text-align": 'left', padding: "8px" }}>
        <div style={{ "font-weight": 'bold', color: "var(--text)" }}>{props.expiryStr}</div>
        <div style={{ "font-size": '11px', color: 'var(--muted)' }}>{props.dte().toFixed(2)} DTE</div>
      </td>
      <td 
        class={isFutOvr() ? 'highlight-col fw-bold' : ''} 
        style={{ "text-align": 'right', "font-family": 'monospace', cursor: 'help', position: 'relative' }}
        onMouseEnter={() => props.setHoveredExpiry(props.expiryStr)}
        onMouseLeave={() => props.setHoveredExpiry(null)}
      >
        <FlashTd val={expData().next_m_ltp} format={v => v.toFixed(2)} />
        <Show when={props.hoveredExpiry() === props.expiryStr}>
          <div style={{ position: 'absolute', "z-index": 1000, background: '#222', color: '#fff', padding: '10px 14px', "border-radius": '6px', "font-size": '12px', "white-space": 'normal', "max-width": '320px', width: 'max-content', top: '50%', left: '100%', transform: 'translateY(-50%)', "margin-left": '12px', "font-weight": 'normal', "pointer-events": 'none', "box-shadow": '0 4px 8px rgba(0,0,0,0.3)', "text-align": 'left' }}>
            {calcNotes()}
            <div style={{ position: 'absolute', top: '50%', right: '100%', transform: 'translateY(-50%)', "border-width": '6px', "border-style": 'solid', "border-color": 'transparent #222 transparent transparent' }} />
          </div>
        </Show>
      </td>
      <td style={{ "text-align": 'center' }}>
        <EditableCell value={expData().gap} onUpdate={v => props.handleGapUpdate(props.expiryStr, v)} isOverridden={isGapOvr()} placeholder="Gap" title="Set custom Gap interval for this expiry" />
      </td>
      <td style={{ "text-align": 'center' }}>
        <EditableCell value={expData().atm_strike} onUpdate={v => props.handleStrikeUpdate(props.expiryStr, v)} isOverridden={isStrikeOvr()} placeholder="Auto ATM" title="Force a specific ATM Strike for this expiry" />
      </td>
      <FlashTd val={normIV(ceOpt().iv_bid)} format={(v) => v.toFixed(2) + '%'} style={{ "text-align": 'right' }} />
      <FlashTd val={normIV(ceOpt().iv_ask)} format={(v) => v.toFixed(2) + '%'} style={{ "text-align": 'right' }} />
      <FlashTd val={normIV(ceOpt().iv_ltp)} format={(v) => v.toFixed(2) + '%'} style={{ "text-align": 'right', color: "var(--green)", "font-weight": "bold" }} class="highlight-col" />
      <Show when={props.showEod()}>
        <FlashTd val={eodCeOpt().iv_ltp} format={(v) => v.toFixed(2) + '%'} style={{ "text-align": 'right' }} />
      </Show>
      <FlashTd val={normIV(peOpt().iv_ltp)} format={(v) => v.toFixed(2) + '%'} style={{ "text-align": 'right', color: "var(--green)", "font-weight": "bold" }} class="highlight-col" />
      <Show when={props.showEod()}>
        <FlashTd val={eodPeOpt().iv_ltp} format={(v) => v.toFixed(2) + '%'} style={{ "text-align": 'right' }} />
      </Show>
      <FlashTd val={normIV(peOpt().iv_bid)} format={(v) => v.toFixed(2) + '%'} style={{ "text-align": 'right' }} />
      <FlashTd val={normIV(peOpt().iv_ask)} format={(v) => v.toFixed(2) + '%'} style={{ "text-align": 'right' }} />
      <td style={{ "vertical-align": 'middle', padding: "8px" }}>
        <InlineOverrides 
          expiry={props.expiryStr} 
          sourceData={props.sourceData} 
          sendMessage={props.sendMessage} 
          symbol={props.currentSymbol}
          setOverridesStore={props.setOverridesStore}
        />
      </td>
    </tr>
  );
}

function SummaryTableInstance(props) {
  const currentSymbol = () => props.config.symbol || 'NIFTY';
  const showEod = () => props.config.showEod ?? true;
  const isIndex = () => INDICES.has(currentSymbol());
  
  // STRICT OVERRIDE: Indices NEVER use actual futures, regardless of saved config. Stocks default to true.
  const useActual = () => isIndex() ? false : (props.config.useActual ?? true);
  const [hoveredExpiry, setHoveredExpiry] = createSignal(null);

  // Proactively synchronize the backend with the correct future type on load and symbol change
  createEffect(() => {
    props.sendMessage({ 
      action: 'set_future_type', 
      symbol: currentSymbol(), 
      use_actual: useActual(), 
      user_id: getUserId() 
    });
  });

  const localData = createMemo(() => {
    const sym = currentSymbol();
    const exp_list = props.activeExpiries()[sym] || [];
    
    const data = {};
    exp_list.forEach(exp => {
      const book = props.books[`${sym}_${exp}`];
      if (!book) return;

      let finalFut = book.future;
      const ovr = props.overridesStore.futures[exp];
      if (ovr) {
          if (ovr.base_expiry) {
              const baseBook = props.books[`${sym}_${ovr.base_expiry}`];
              if (baseBook && baseBook.future != null) finalFut = baseBook.future + ovr.offset;
          } else if (ovr.offset != null && !isNaN(ovr.offset)) {
              finalFut = ovr.offset;
          }
      }
      
      let atm = null;
      if (finalFut && book.rows.length > 0) {
         atm = book.rows.reduce((p, c) => Math.abs(c.k - finalFut) < Math.abs(p.k - finalFut) ? c : p).k;
      }

      const options = [];
      book.rows.forEach(r => {
        options.push({ strike: r.k, OptionType: 'CE', iv_bid: r.ce_bid_iv !== undefined ? r.ce_bid_iv : r.ce_iv, iv_ask: r.ce_ask_iv !== undefined ? r.ce_ask_iv : r.ce_iv, iv_ltp: r.ce_iv, chg1d: r.ce_chg1d });
        options.push({ strike: r.k, OptionType: 'PE', iv_bid: r.pe_bid_iv !== undefined ? r.pe_bid_iv : r.pe_iv, iv_ask: r.pe_ask_iv !== undefined ? r.pe_ask_iv : r.pe_iv, iv_ltp: r.pe_iv, chg1d: r.pe_chg1d });
      });
      
      const gap = props.overridesStore.gaps[exp] || 100;
      data[book.expiry] = {
        atm_strike: props.overridesStore.strikes[exp] || atm,
        next_m_ltp: finalFut,
        options: options,
        gap: gap
      };
    });
    
    return { 
      expiry_list: exp_list, data: data,
      overrides: props.overridesStore.futures || {}, 
      strike_overrides: props.overridesStore.strikes || {},
      gap_overrides: props.overridesStore.gaps || {}, 
      future_values: {}
    };
  });

  const sourceData = () => localData();
  const expiries = () => sourceData()?.expiry_list || [];

  const inputStyle = {
      padding: '4px 8px', "border-radius": '4px', border: '1px solid var(--border)',
      background: 'var(--panel)', color: 'var(--text)', width: '100px',
      "font-size": '13px', outline: 'none'
  };

  const handleActualToggle = (e) => {
      props.updateConfig({ useActual: e.target.checked });
  };

  return (
    <div style={{ "min-width": '950px', background: 'var(--bg)', border: '1px solid var(--border)', "border-radius": '8px', overflow: 'visible' }}>
      <div style={{ padding: '12px 15px', background: 'var(--panel2)', "border-bottom": '1px solid var(--border)', display: 'flex', gap: '15px', "align-items": 'center' }}>
        <label for={`summary-symbol-${props.id}`} style={{ "font-weight": 'bold', "font-size": '13px' }}>Symbol:</label>
        <select
          id={`summary-symbol-${props.id}`}
          name="symbol"
          style={{ ...inputStyle }}
          value={currentSymbol()}
          onChange={e => {
            const newSymbol = e.target.value;
            const isNewSymbolIndex = INDICES.has(newSymbol);
            const newUseActual = !isNewSymbolIndex;
            props.updateConfig({ symbol: newSymbol, useActual: newUseActual });
          }}
        >
            <For each={props.availableSymbols()}>{s => <option value={s}>{s}</option>}</For>
        </select>
        <label style={{ display: 'flex', "align-items": 'center', gap: '5px', "font-size": '13px', cursor: isIndex() ? 'not-allowed' : 'pointer', "font-weight": 'bold', "margin-left": '10px' }}>
            <input type="checkbox" checked={useActual()} onChange={handleActualToggle} disabled={isIndex()} /> Actual Futures (Stocks)
        </label>
        <label style={{ display: 'flex', "align-items": 'center', gap: '5px', "font-size": '13px', cursor: 'pointer', "font-weight": 'bold', "margin-left": '10px' }}>
            <input type="checkbox" checked={showEod()} onChange={e => props.updateConfig({ showEod: e.target.checked })} /> Show EOD Data
        </label>
        <button onClick={props.removeTable} style={{ "margin-left": 'auto', background: 'var(--red)', color: '#fff', border: 'none', "border-radius": '4px', padding: '4px 10px', cursor: 'pointer', "font-weight": 'bold' }}>✕ Remove</button>
      </div>
      <div class="tableWrap" style={{ border: "none", "border-radius": 0, resize: "vertical", overflow: "auto", "min-height": "200px" }}>
      <table style={{ width: '100%', "border-collapse": 'collapse' }}>
        <thead>
          <tr>
            <th style={{ "text-align": 'left' }}>Expiry</th>
            <th>Futures Price</th>
            <th style={{ "text-align": 'center' }}>Gap</th>
            <th style={{ "text-align": 'center' }}>ATM Strike</th>
            <th>CE Bid IV</th>
            <th>CE Ask IV</th>
            <th>CE LTP IV</th>
            <Show when={showEod()}><th>CE EOD IV</th></Show>
            <th>PE LTP IV</th>
            <Show when={showEod()}><th>PE EOD IV</th></Show>
            <th>PE Bid IV</th>
            <th>PE Ask IV</th>
            <th style={{ "text-align": 'center' }}>Overrides</th>
          </tr>
        </thead>
        <tbody>
          <Show when={expiries().length > 0} fallback={<tr><td colspan="13" class="empty" style={{ "text-align": 'center', padding: '20px' }}>No data available</td></tr>}>
          <For each={expiries()}>
            {(expiryStr) => <SummaryRow 
               expiryStr={expiryStr}
               sourceData={sourceData}
               currentSymbol={currentSymbol()}
               showEod={showEod}
               dte={() => getDTE(expiryStr)}
               handleGapUpdate={(exp, val) => {
                 props.setOverridesStore("gaps", exp, val);
                 props.sendMessage({ action: 'set_gap_override', target: exp, value: val, symbol: currentSymbol(), user_id: getUserId() });
               }}
               handleStrikeUpdate={(exp, val) => {
                 props.setOverridesStore("strikes", exp, val);
                 props.sendMessage({ action: 'set_override', target: exp, value: val, symbol: currentSymbol(), user_id: getUserId() });
               }}
               hoveredExpiry={hoveredExpiry}
               setHoveredExpiry={setHoveredExpiry}
               sendMessage={props.sendMessage}
               eodStore={props.eodStore}
               setOverridesStore={props.setOverridesStore}
            />}
          </For>
          </Show>
        </tbody>
      </table>
      </div>
    </div>
  );
}

function IVSummaryView(props) {
  // ✅ FIX: Initialize with a default table immediately — never null
  const defaultTable = () => [{ id: Date.now(), symbol: 'NIFTY' }];
  const [tables, setTables] = createSignal(defaultTable());

  // Ping backend to init session
  createEffect(() => {
    props.sendMessage({ action: 'initsession', userid: getUserId() });
  });

  // Load saved config from backend (or localStorage fallback), overwrite default if found
  createEffect(() => {
    fetch(`${API_BASE}/api/config?key=summultitables&userid=${getUserId()}&t=${Date.now()}`)
      .then(res => {
        if (res.ok) return res.text();
        const saved = localStorage.getItem('summultitables');
        if (saved) return saved;
        return null;
      })
      .then(text => {
        if (!text || text.trim() === '' || text.trim() === 'null') return null;
        try { return JSON.parse(text); } catch { return null; }
      })
      .then(savedConfig => {
        if (savedConfig) {
          // Handle DB-wrapped payload: { value: [...] }
          if (savedConfig.value) savedConfig = savedConfig.value;
          if (Array.isArray(savedConfig) && savedConfig.length > 0) {
            setTables(savedConfig);
          }
          // else: keep the default already set — do NOT overwrite with empty
        }
      })
      .catch(() => {
        // Backend unreachable — keep the default already set, try localStorage
        const saved = localStorage.getItem('summultitables');
        if (saved) {
          try {
            const parsed = JSON.parse(saved);
            if (Array.isArray(parsed) && parsed.length > 0) setTables(parsed);
          } catch { /* keep default */ }
        }
      });
  });

  // Persist config to backend + localStorage on change
  createEffect(() => {
    const t = tables();
    if (t) {
      const configPayload = { key: 'summultitables', value: t, userid: getUserId() };
      fetch(`${API_BASE}/api/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(configPayload),
      }).catch(err => console.error('Failed to save summary config', err));
      localStorage.setItem('summultitables', JSON.stringify(t));
    }
  });

  const addTable = () => {
    const t = tables();
    setTables([...t, { id: Date.now(), symbol: t[0]?.symbol || 'NIFTY' }]);
  };

  const updateTable = (id, newConfig) =>
    setTables(tables().map(t => t.id === id ? { ...t, ...newConfig } : t));

  const removeTable = (id) => {
    if (tables().length <= 1) {
      alert('Cannot remove the last table.');
      return;
    }
    setTables(tables().filter(t => t.id !== id));
  };

  return (
    <div style={{ display: 'flex', 'flex-direction': 'column', flex: 1, 'min-height': 0, 'overflow-y': 'auto', padding: '15px' }}>
      <div style={{ display: 'flex', 'justify-content': 'space-between', 'align-items': 'center', 'margin-bottom': '20px' }}>
        <h2 style={{ margin: 0, color: 'var(--text)' }}>IV Summary Dashboard</h2>
        <button onClick={addTable} class="addBtn">+ Add Comparison Table</button>
      </div>

      {/* ✅ No more "Loading dashboard configuration..." — tables is always truthy */}
      <div style={{ display: 'flex', gap: '20px', 'padding-bottom': '20px', 'flex-wrap': 'wrap', 'align-items': 'flex-start' }}>
        <For each={tables()}>
          {(t) => (
            <SummaryTableInstance
              id={t.id}
              config={t}
              updateConfig={(c) => updateTable(t.id, c)}
              removeTable={() => removeTable(t.id)}
              availableSymbols={props.symbols}
              books={props.books}
              activeExpiries={props.activeExpiries}
              sendMessage={props.sendMessage}
              eodStore={props.eodStore}
              setEodStore={props.setEodStore}
              overridesStore={props.overridesStore}
              setOverridesStore={props.setOverridesStore}
            />
          )}
        </For>
      </div>

      {/* CSV Overrides Manager */}
      <OverridesPanel
        symbol={tables()[0]?.symbol || 'NIFTY'}
        sendMessage={props.sendMessage}
        setEodStore={props.setEodStore}
        setOverridesStore={props.setOverridesStore}
      />
    </div>
  );
}

function TypeToggler(props) {
    const currentVal = () => props.typeOverrides()[`${props.strike}_${props.leg}`] || "Auto";
    
    const cycle = () => {
        props.setTypeOverrides(prev => {
            const next = {...prev};
            const c = currentVal();
            if (c === "Auto") next[`${props.strike}_${props.leg}`] = "CE";
            else if (c === "CE") next[`${props.strike}_${props.leg}`] = "PE";
            else delete next[`${props.strike}_${props.leg}`];
            return next;
        });
    };
    
    const display = () => currentVal() === "Auto" ? `A(${props.autoVal.charAt(0)})` : currentVal();
    
    return (
        <div 
            onClick={cycle}
            style={{
                "font-size": '10px', padding: '2px 4px', border: '1px solid var(--border)', 
                "border-radius": '3px', background: currentVal() === 'Auto' ? 'transparent' : 'var(--btn-active-bg, rgba(0,0,0,0.1))',
                color: currentVal() === 'CE' ? 'var(--green)' : currentVal() === 'PE' ? 'var(--red)' : 'var(--muted)',
                cursor: 'pointer', "user-select": 'none', "font-weight": currentVal() !== 'Auto' ? 'bold' : 'normal',
                "text-align": 'center', "min-width": '24px'
            }}
            title={`Exp ${props.leg}: Click to toggle Auto -> CE -> PE`}
        >
            {display()}
        </div>
    );
}

function FwdRow(props) {
  const strike = props.strike;
  const isAtm = createMemo(() => props.baseAtm() !== null && strike === props.baseAtm());

  const isCall1 = props.d1()?.next_m_ltp <= strike;
  const autoOt1 = isCall1 ? 'CE' : 'PE';
  const ot1 = createMemo(() => props.typeOverrides()[`${strike}_1`] || autoOt1);
  const o1 = createMemo(() => getOpt(props.d1(), strike, ot1()));

  const isCall2 = props.d2()?.next_m_ltp <= strike;
  const autoOt2 = isCall2 ? 'CE' : 'PE';
  const ot2 = createMemo(() => props.typeOverrides()[`${strike}_2`] || autoOt2);
  const o2 = createMemo(() => getOpt(props.d2(), strike, ot2()));

  const fwd = createMemo(() => calculateForwardIV(props.dte1(), o1()?.iv_ask, o1()?.iv_bid, props.dte2(), o2()?.iv_bid, o2()?.iv_ask, strike));

  const getEodIv = (sym, exp, type) => {
      const precomputedIv = getEodVal(props.eodStore, sym, exp, strike, type, true);
      return precomputedIv != null ? precomputedIv : null;
  };

  const eodFwd = createMemo(() => {
      const t1 = ot1(), t2 = ot2();
      return untrack(() => {
          const eodIv1 = getEodIv(props.sym, props.exp1, t1);
          const eodIv2 = getEodIv(props.sym, props.exp2, t2);
          if (eodIv1 == null || eodIv2 == null) return { short: null, long: null };
          const staticDte1 = Math.floor(getDTE(props.exp1)) + 1;
          const staticDte2 = Math.floor(getDTE(props.exp2)) + 1;
          return calculateForwardIV(staticDte1, eodIv1, eodIv1, staticDte2, eodIv2, eodIv2, strike);
      });
  });

  return (
    <tr class={isAtm() ? "atmRow" : ""}>
      <td style={{ "text-align": 'center', "font-weight": 'bold', "border-right": '1px solid var(--border)', padding: '6px' }}>
        {strike} {isAtm() && <span style={{ "font-size": '10px', color: 'var(--muted)' }}><br/>(ATM)</span>}
        <div style={{ display: 'flex', "justify-content": 'center', gap: '4px', "margin-top": '4px' }}>
            <TypeToggler leg={1} strike={strike} typeOverrides={props.typeOverrides} setTypeOverrides={props.setTypeOverrides} autoVal={autoOt1} />
            <TypeToggler leg={2} strike={strike} typeOverrides={props.typeOverrides} setTypeOverrides={props.setTypeOverrides} autoVal={autoOt2} />
        </div>
      </td>
      <PriceCell value={fwd().short} format={v => v.toFixed(2)} />
      <PriceCell value={fwd().long} format={v => v.toFixed(2)} style={{ "border-right": props.showEod() ? '1px solid var(--border)' : (props.showLegDetails() ? '1px solid var(--border)' : 'none') }} />
      
      <Show when={props.showEod()}>
        <PriceCell value={eodFwd().short} format={v => v.toFixed(2)} />
        <PriceCell value={eodFwd().long} format={v => v.toFixed(2)} style={{ "border-right": props.showLegDetails() ? '1px solid var(--border)' : 'none' }} />
      </Show>

      <Show when={props.showLegDetails()}>
          <td style={{ "text-align": 'center', color: ot1() === 'CE' ? 'var(--green)' : 'var(--red)' }}>{ot1()}</td>
          <FlashTd val={normIV(o1()?.iv_bid)} format={v => v.toFixed(2)} style={{ "text-align": 'right' }} />
          <FlashTd val={normIV(o1()?.iv_ask)} format={v => v.toFixed(2)} style={{ "text-align": 'right', "border-right": '1px solid var(--border)' }} />
          
          <td style={{ "text-align": 'center', color: ot2() === 'CE' ? 'var(--green)' : 'var(--red)' }}>{ot2()}</td>
          <FlashTd val={normIV(o2()?.iv_bid)} format={v => v.toFixed(2)} style={{ "text-align": 'right' }} />
          <FlashTd val={normIV(o2()?.iv_ask)} format={v => v.toFixed(2)} style={{ "text-align": 'right' }} />
      </Show>
    </tr>
  );
}

function FwdTableInstance(props) {
  const currentSymbol = () => props.config.symbol || 'NIFTY';
  const exp1 = () => props.config.exp1;
  const exp2 = () => props.config.exp2;
  const gap = () => props.config.gap || 100;
  const numStrikes = () => props.config.numStrikes || 10;
  const showLegDetails = () => props.config.showLegDetails ?? true;
  const showEod = () => props.config.showEod ?? true;

  const [typeOverrides, setTypeOverrides] = createSignal({});

  const localData = createMemo(() => {
    const sym = currentSymbol();
    const exp_list = props.activeExpiries()[sym] || [];
    
    const data = {};
    exp_list.forEach(exp => {
      const book = props.books[`${sym}_${exp}`];
      if (!book) return;

      let finalFut = book.future;
      const ovr = props.overridesStore?.futures?.[exp];
      if (ovr) {
          if (ovr.base_expiry) {
              const baseBook = props.books[`${sym}_${ovr.base_expiry}`];
              if (baseBook && baseBook.future != null) finalFut = baseBook.future + ovr.offset;
          } else if (ovr.offset != null && !isNaN(ovr.offset)) {
              finalFut = ovr.offset;
          }
      }
      
      let atm = null;
      if (finalFut && book.rows.length > 0) {
         atm = book.rows.reduce((p, c) => Math.abs(c.k - finalFut) < Math.abs(p.k - finalFut) ? c : p).k;
      }

      const options = [];
      book.rows.forEach(r => {
        options.push({ strike: r.k, OptionType: 'CE', iv_bid: r.ce_bid_iv !== undefined ? r.ce_bid_iv : r.ce_iv, iv_ask: r.ce_ask_iv !== undefined ? r.ce_ask_iv : r.ce_iv, chg1d: r.ce_chg1d });
        options.push({ strike: r.k, OptionType: 'PE', iv_bid: r.pe_bid_iv !== undefined ? r.pe_bid_iv : r.pe_iv, iv_ask: r.pe_ask_iv !== undefined ? r.pe_ask_iv : r.pe_iv, chg1d: r.pe_chg1d });
      });
      
      data[book.expiry] = {
        atm_strike: props.overridesStore?.strikes?.[exp] || atm,
        next_m_ltp: finalFut,
        options: options
      };
    });
    
    return { expiry_list: exp_list, data: data };
  });

  const sourceData = () => localData();
  const expiries = () => sourceData()?.expiry_list || [];
  
  const d1 = () => sourceData()?.data?.[exp1()];
  const d2 = () => sourceData()?.data?.[exp2()];

  // Auto-update expiries if the symbol changes and current expiries are invalid
  createEffect(() => {
      const exps = expiries();
      const e1 = exp1();
      const e2 = exp2();
      if (exps.length > 0) {
          let changed = false;
          let nextConfig = {};
          if (!exps.includes(e1)) { nextConfig.exp1 = exps[0]; changed = true; }
          if (!exps.includes(e2)) { nextConfig.exp2 = exps[1] || exps[0]; changed = true; }
          if (changed) props.updateConfig(nextConfig);
      }
  });

  const validSelection = () => exp1() && exp2() && exp1() !== exp2();

  const strikesWindow = createMemo(() => {
    if (!validSelection() || !d1() || !d2()) return [];
    const s1 = d1().options?.map(o => Number(o.strike)) || [];
    const s2 = d2().options?.map(o => Number(o.strike)) || [];
    const allStrikes = new Set([...s1, ...s2]);
    
    const atm = d1().atm_strike;
    if (!atm) return [];
    
    const baseAtm = Math.round(atm / gap()) * gap();
    const half = Math.floor(numStrikes() / 2);
    const window = [];
    for (let i = -half; i < numStrikes() - half; i++) {
      const s = baseAtm + i * gap();
      if (allStrikes.has(s)) window.push(s);
    }
    return window;
  });

  const getOpt = (d, strike, type) => d?.options?.find(o => Number(o.strike) === strike && o.OptionType === type);

  const dte1 = () => getDTE(exp1());
  const dte2 = () => getDTE(exp2());

  const inputStyle = { 
      padding: '4px 8px', "border-radius": '4px', border: '1px solid var(--border)', 
      background: 'var(--panel)', color: 'var(--text)', width: '90px',
      "font-size": '13px', outline: 'none'
  };
  
  const totalCols = () => showLegDetails() ? (showEod() ? 11 : 9) : (showEod() ? 5 : 3);

  return (
      <div style={{ "min-width": '900px', background: 'var(--bg)', border: '1px solid var(--border)', "border-radius": '8px', overflow: 'visible' }}>
      <div style={{ padding: '12px 15px', background: 'var(--panel2)', "border-bottom": '1px solid var(--border)', display: 'flex', gap: '15px', "align-items": 'center', "flex-wrap": 'wrap' }}>
          <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Symbol:</label>
          <select style={{ ...inputStyle, width: '100px' }} value={currentSymbol()} onChange={e => props.updateConfig({ symbol: e.target.value })}>
              <For each={props.availableSymbols()}>
                  {s => <option value={s}>{s}</option>}
              </For>
          </select>

          <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Exp 1:</label>
          <select style={{ ...inputStyle, width: '120px' }} value={exp1()} onChange={e => props.updateConfig({ exp1: e.target.value })}>
              <For each={expiries()}>
                  {exp => <option value={exp}>{exp}</option>}
              </For>
          </select>
          
          <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Exp 2:</label>
          <select style={{ ...inputStyle, width: '120px' }} value={exp2()} onChange={e => props.updateConfig({ exp2: e.target.value })}>
              <For each={expiries()}>
                  {exp => <option value={exp}>{exp}</option>}
              </For>
          </select>

          <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Gap:</label>
          <input type="number" style={{ ...inputStyle, width: '70px' }} value={gap()} onChange={e => props.updateConfig({ gap: Number(e.target.value) })} />
          
          <label style={{ "font-weight": 'bold', "font-size": '13px' }}>Strikes:</label>
          <input type="number" style={{ ...inputStyle, width: '70px' }} value={numStrikes()} onChange={e => props.updateConfig({ numStrikes: Number(e.target.value) })} />

          <label style={{ display: 'flex', "align-items": 'center', gap: '5px', "font-size": '13px', cursor: 'pointer', "font-weight": 'bold', "margin-left": '5px' }}>
            <input type="checkbox" checked={showLegDetails()} onChange={e => props.updateConfig({ showLegDetails: e.target.checked })} /> Show Legs
          </label>
          <label style={{ display: 'flex', "align-items": 'center', gap: '5px', "font-size": '13px', cursor: 'pointer', "font-weight": 'bold', "margin-left": '5px' }}>
            <input type="checkbox" checked={showEod()} onChange={e => props.updateConfig({ showEod: e.target.checked })} /> Show EOD
          </label>

          <button onClick={props.removeTable} style={{ "margin-left": 'auto', background: 'var(--red)', color: '#fff', border: 'none', "border-radius": '4px', padding: '4px 10px', cursor: 'pointer', "font-weight": 'bold' }}>
             ✕ Remove
          </button>
      </div>

      <Show when={validSelection() && d1() && d2()}>
          <div style={{ padding: '8px 15px', "border-bottom": '1px solid var(--border)', display: 'flex', gap: '20px', "font-size": '13px' }}>
              <div><strong>Exp 1:</strong> Fut <strong>{d1()?.next_m_ltp?.toFixed(2) || '-'}</strong> ATM <strong>{d1()?.atm_strike || '-'}</strong></div>
              <div><strong>Exp 2:</strong> Fut <strong>{d2()?.next_m_ltp?.toFixed(2) || '-'}</strong> ATM <strong>{d2()?.atm_strike || '-'}</strong></div>
          </div>
      </Show>

      <div class="tableWrap" style={{ border: "none", "border-radius": 0, resize: "vertical", overflow: "auto", "min-height": "200px" }}>
          <table style={{ width: '100%', "border-collapse": 'collapse' }}>
          <thead>
            <tr>
              <th rowspan="2" style={{ "text-align": 'center', "border-right": '1px solid var(--border)' }}>Strike</th>
              <th colspan="2" style={{ "text-align": 'center', "border-right": '1px solid var(--border)' }}>Forward IV</th>
              <Show when={showEod()}>
                 <th colspan="2" style={{ "text-align": 'center', "border-right": showLegDetails() ? '1px solid var(--border)' : 'none' }}>EOD Fwd IV</th>
              </Show>
              <Show when={showLegDetails()}>
                 <th colspan="3" style={{ "text-align": 'center', "border-right": '1px solid var(--border)' }}>Expiry 1</th>
                 <th colspan="3" style={{ "text-align": 'center' }}>Expiry 2</th>
              </Show>
            </tr>
            <tr>
              <th style={{ "text-align": 'right' }}>Short</th>
              <th style={{ "text-align": 'right', "border-right": showEod() ? '1px solid var(--border)' : (showLegDetails() ? '1px solid var(--border)' : 'none') }}>Long</th>
              <Show when={showEod()}>
                 <th style={{ "text-align": 'right' }}>EOD S</th>
                 <th style={{ "text-align": 'right', "border-right": showLegDetails() ? '1px solid var(--border)' : 'none' }}>EOD L</th>
              </Show>
              <Show when={showLegDetails()}>
                 <th style={{ "text-align": 'center' }}>Type</th>
                 <th style={{ "text-align": 'right' }}>Bid IV</th>
                 <th style={{ "text-align": 'right', "border-right": '1px solid var(--border)' }}>Ask IV</th>
                 <th style={{ "text-align": 'center' }}>Type</th>
                 <th style={{ "text-align": 'right' }}>Bid IV</th>
                 <th style={{ "text-align": 'right' }}>Ask IV</th>
              </Show>
            </tr>
          </thead>
          <tbody>
            <Show when={!validSelection()}>
              <tr><td colspan={totalCols()} style={{ "text-align": 'center', padding: '20px' }} class="empty">Select two different expiries</td></tr>
            </Show>
            <Show when={validSelection() && strikesWindow().length === 0}>
              <tr><td colspan={totalCols()} style={{ "text-align": 'center', padding: '20px' }} class="empty">No strikes available</td></tr>
            </Show>
            <Show when={validSelection() && strikesWindow().length > 0}>
              <For each={strikesWindow()}>
                {strike => <FwdRow 
                    sym={currentSymbol()}
                    exp1={exp1()} exp2={exp2()}
                    eodStore={props.eodStore}
                    strike={strike} 
                    d1={d1} d2={d2} 
                    dte1={dte1} dte2={dte2} 
                    typeOverrides={typeOverrides} setTypeOverrides={setTypeOverrides}
                    baseAtm={() => d1()?.atm_strike ? Math.round(d1().atm_strike / gap()) * gap() : null}
                    showEod={showEod} showLegDetails={showLegDetails}
                />}
              </For>
            </Show>
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ==========================================================
   CSV OVERRIDES PANEL
========================================================== */
function OverridesPanel(props) {
  const [futTarget, setFutTarget] = createSignal('');
  const [futBase, setFutBase] = createSignal('');
  const [futOffset, setFutOffset] = createSignal('');

  const [strikeTarget, setStrikeTarget] = createSignal('');
  const [strikeVal, setStrikeVal] = createSignal('');

  const [networkPath, setNetworkPath] = createSignal('');

  const handleAddFuture = () => {
    if (!futTarget() || futOffset() === '') return alert("Fill target expiry and offset");
    props.sendMessage({
      action: 'set_future_override',
      target: futTarget(),
      base: futBase() || null,
      offset: parseFloat(futOffset()),
      symbol: props.symbol,
      user_id: getUserId()
    });
    setFutTarget(''); setFutBase(''); setFutOffset('');
  };

  const handleAddStrike = () => {
    if (!strikeTarget() || !strikeVal()) return alert("Fill all strike fields");
    props.sendMessage({
      action: 'set_override',
      target: strikeTarget(),
      value: parseFloat(strikeVal()),
      symbol: props.symbol,
      user_id: getUserId()
    });
    setStrikeTarget(''); setStrikeVal('');
  };

  const handleCSVUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (evt) => {
      const lines = evt.target.result.split('\n');
      if (lines.length < 2) return;
      const headers = lines[0].split(',').map(h => h.trim().toLowerCase());
      const tIdx = headers.indexOf('target_expiry');
      const bIdx = headers.indexOf('base_expiry');
      const oIdx = headers.indexOf('offset');
      
      if (tIdx === -1 || bIdx === -1 || oIdx === -1) {
        return alert("CSV must contain headers: target_expiry, base_expiry, offset");
      }
      
      for (let i = 1; i < lines.length; i++) {
        const cols = lines[i].split(',');
        if (cols.length < 3) continue;
        const target = cols[tIdx].trim();
        const base = cols[bIdx].trim();
        const offset = parseFloat(cols[oIdx].trim());
        if (target && !isNaN(offset)) {
          props.setOverridesStore("futures", target, { base_expiry: base || null, offset });
          props.sendMessage({ action: 'set_future_override', target, base: base || null, offset, symbol: props.symbol, user_id: getUserId() });
        }
      }
      alert("CSV overrides sent successfully!");
      e.target.value = '';
    };
    reader.readAsText(file);
  };

  return (
    <div style={{ "margin-top": '20px', "border-top": "1px solid var(--border)", "padding-top": "20px" }}>
        <label for="future-overrides-upload" style={{ margin: "0 0 15px 0", color: "var(--text)", "font-size": "1.17em", "font-weight": "bold", display: "block" }}>
          Upload Future Overrides CSV
        </label>
        <p style={{ "font-size": "12px", color: "var(--muted)" }}>Headers required: target_expiry, base_expiry, offset</p>
        <input id="future-overrides-upload"
               name="future_overrides_csv"
               type="file"
               accept=".csv" onChange={handleCSVUpload}
               class="select" style={{ padding: "8px" }} />
    </div>
  );
}

function ForwardIVView(props) {
  const [tables, setTables] = createSignal(null);

  // Load config from backend on mount, with localStorage fallback
  createEffect(() => {
    fetch(`${API_BASE}/api/config?key=fwd_multi_tables&user_id=${getUserId()}&t=${Date.now()}`)
      .then(res => {
        if (res.ok) return res.json();
        const saved = localStorage.getItem('fwd_multi_tables');
        if (saved) return JSON.parse(saved);
        return null;
      })
      .then(savedConfig => {
        if (savedConfig && savedConfig.value) savedConfig = savedConfig.value;
        if (savedConfig?.length > 0) {
          setTables(savedConfig);
        } else {
          setTables([{ id: Date.now(), exp1: '', exp2: '', gap: 100, numStrikes: 10, symbol: 'NIFTY', showLegDetails: true }]);
        }
      })
      .catch(() => {
        setTables([{ id: Date.now(), exp1: '', exp2: '', gap: 100, numStrikes: 10, symbol: 'NIFTY', showLegDetails: true }]);
      });
  });

  // Save config to backend and localStorage on change
  createEffect(() => {
    const currentTables = tables();
    if (currentTables) {
      const configPayload = { key: 'fwd_multi_tables', value: currentTables, user_id: getUserId() };
      fetch(`${API_BASE}/api/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(configPayload)
      }).catch(err => console.error("Failed to save forwards config to backend", err));
      localStorage.setItem('fwd_multi_tables', JSON.stringify(currentTables));
    }
  });

  const addTable = () => {
      const t = tables();
      setTables([...t, { 
          id: Date.now(), 
          exp1: t[0]?.exp1 || '', 
          exp2: t[0]?.exp2 || '', 
          gap: t[0]?.gap || 100, 
          numStrikes: t[0]?.numStrikes || 10,
          symbol: t[0]?.symbol || 'NIFTY',
          showLegDetails: t[0]?.showLegDetails ?? true
      }]);
  };

  const updateTable = (id, newConfig) => {
      setTables(tables().map(t => t.id === id ? { ...t, ...newConfig } : t));
  };

  const removeTable = (id) => {
      if (tables().length > 1) setTables(tables().filter(t => t.id !== id));
      else alert("Cannot remove the last table.");
  };

  return (
      <div style={{ display: 'flex', "flex-direction": 'column', flex: 1, minHeight: 0, overflowY: 'auto', padding: '15px' }}>
          <div style={{ display: 'flex', "justify-content": 'space-between', "align-items": 'center', "margin-bottom": '20px' }}>
              <h2 style={{ margin: 0, color: 'var(--text)' }}>Forward IV Dashboard</h2>
              <button onClick={addTable} class="addBtn">
                  + Add Comparison Table
              </button>
          </div>
          <Show when={tables()} fallback={<div class="empty">Loading dashboard configuration...</div>}>
          <div style={{ display: 'flex', gap: '20px', "padding-bottom": '20px', "flex-wrap": 'wrap', "align-items": 'flex-start' }}>
                  <For each={tables()}>
                      {t => (
                          <FwdTableInstance 
                              id={t.id} 
                              config={t} 
                              updateConfig={(c) => updateTable(t.id, c)} 
                              removeTable={() => removeTable(t.id)} 
                              availableSymbols={props.symbols}
                              books={props.books}
                              eodStore={props.eodStore}
                              activeExpiries={props.activeExpiries}
                              overridesStore={props.overridesStore}
                          />
                      )}
                  </For>
              </div>
          </Show>
      </div>
  );
}

/* ==========================================================
   Helpers
========================================================== */

function fmt(v) {
  if (
    v === null ||
    v === undefined ||
    v === "" ||
    v === "NaN"
  ) return "-";

  if (typeof v === 'string' && v.endsWith('%')) {
    const num = parseFloat(v);
    if (Number.isNaN(num)) return "-";
    return num.toFixed(2) + '%';
  }

  const num = Number(v);
  if (Number.isNaN(num)) return "-";

  return num.toFixed(2);
}

function greek(v, d = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return Number(v).toFixed(d);
}

// Safely normalize huge IV values pushed directly by backends avoiding double-multiplier issues
function normIV(v) {
  if (v == null || Number.isNaN(v)) return null;
  const num = Number(v);
  // Most real IVs won't be > 300%. If they are 2800, they are pre-multiplied by 100 on the server.
  return Math.abs(num) > 300 ? num / 100 : num;
}

function iv(v) {
  if (
    v === null ||
    v === undefined ||
    v <= 0 ||
    Number.isNaN(v) ||
    v === 25.0 // Catch the backend's hardcoded fallback that causes 2500.00%
  ) return "-";

  return normIV(v).toFixed(2) + "%";
}

/* ==========================================================
   TICKER TAPE (Component)
========================================================== */

function TickerTape(props) {
  const items = createMemo(() => {
    props.tick(); // Bind memo evaluation to global tick safely
    return untrack(() => {
      const res = [];
      const combinedIndices = new Set(['NIFTY', 'MIDCPNIFTY', 'SENSEX', 'BANKNIFTY', 'BANKEX', 'FINNIFTY', ...props.watchlist()]);
      for (const sym of combinedIndices) {
        const exps = props.activeExpiries()[sym];
        if (!exps || !exps.length) continue;
        const book = props.books[`${sym}_${exps[0]}`];
        if (book && book.future) {
          let atmRow = null;
          let diff = Infinity;
          for (const r of book.rows) {
            const d = Math.abs(r.k - book.future);
            if (d < diff) {
              diff = d;
              atmRow = r;
            }
          }
          let ivVal = "-";
          if (atmRow) {
            const ceIv = parseFloat(atmRow.ce_iv) || 0;
            const peIv = parseFloat(atmRow.pe_iv) || 0;
            let blended = (ceIv > 0 && peIv > 0) ? (normIV(ceIv) + normIV(peIv)) / 2 : (normIV(ceIv) || normIV(peIv));
            if (blended > 0) ivVal = blended.toFixed(2) + "%";
          }
          res.push({ sym, price: book.future, iv: ivVal });
        }
      }
      return res;
    });
  });

  return (
    <div class="ticker-tape" style={{ flex: "0 0 36px", height: "36px", "min-height": "36px", "max-height": "36px", display: "flex", "align-items": "center", background: "var(--panel2)", border: "1px solid var(--border)", "border-left": "none", "border-right": "none", "white-space": "nowrap", "overflow-x": "auto" }}>
      <div class="ticker-content">
        <For each={items()}>
          {(item) => (
            <div class="ticker-item">
              <span class="ticker-sym">{item.sym}</span>
              <span class="ticker-price">{fmt(item.price)}</span>
              <span class="ticker-lbl">IV:</span>
              <span class="ticker-iv">{item.iv}</span>
            </div>
          )}
        </For>
      </div>
    </div>
  );
}

/* ==========================================================
   LIVE SCREENER VIEW (Replaces Flask Backend)
========================================================== */

const BLUE_CHIPS = new Set([
  'ITC', 'UNITDSPR', 'ADANIENT', 'TVSMOTOR', 'AMBUJACEM', 'ULTRACEMCO', 'HINDUNILVR', 
  'ASIANPAINT', 'COALINDIA', 'IRCTC', 'BHARTIARTL', 'BPCL', 'TITAN', 'M&M', 'TATAPOWER', 
  'BANKBARODA', 'BANDHANBNK', 'RBLBANK', 'CIPLA', 'ADANIPORTS', 'JUBLFOOD', 'SBIN', 'DLF', 
  'BAJFINANCE', 'TATAMOTORS', 'DRREDDY', 'MARUTI', 'HEROMOTOCO', 'AUROPHARMA', 'KOTAKBANK', 
  'LT', 'BAJAJ-AUTO', 'TCS', 'ONGC', 'AXISBANK', 'HDFCBANK', 'ICICIBANK', 'SUNPHARMA', 
  'TATASTEEL', 'RELIANCE', 'INDUSINDBK', 'TECHM', 'TATACONSUM', 'HCLTECH', 'HDFCLIFE', 
  'INFY', 'LUPIN', 'WIPRO', 'BRITANNIA', 'DMART', 'DABUR', 'HAVELLS', 'HINDALCO', 'INDIGO'
]);

const INDICES = new Set(['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX', 'BANKEX', 'NIFTYNXT50']);

const MONTHS = { "JAN": 0, "FEB": 1, "MAR": 2, "APR": 3, "MAY": 4, "JUN": 5, "JUL": 6, "AUG": 7, "SEP": 8, "OCT": 9, "NOV": 10, "DEC": 11 };

function sortExpiries(a, b) {
  const parse = (s) => {
    let day = 0, mon = -1, yr = 0;
    if (s.includes('-')) {
      const parts = s.split('-');
      day = parseInt(parts[0], 10) || 0;
      mon = MONTHS[parts[1].substring(0, 3).toUpperCase()] ?? -1;
      yr = parseInt(parts[2], 10) || 0;
    } else {
      day = parseInt(s.substring(0, 2), 10) || 0;
      mon = MONTHS[s.substring(2, 5).toUpperCase()] ?? -1;
      yr = s.length > 5 ? parseInt(s.substring(5), 10) : 0;
    }
    if (yr > 0 && yr < 100) yr += 2000;
    return { yr, mon, day };
  };
  const dA = parse(a), dB = parse(b);
  if (dA.yr !== dB.yr) return dA.yr - dB.yr;
  if (dA.mon !== dB.mon) return dA.mon - dB.mon;
  return dA.day - dB.day;
}

// Institutional Blended IV Calculation ported from Excel
function getBlendedIV(ce_iv, pe_iv, future, strike) {
  // Return pure raw OTM IV, completely removing smoothing algorithms
  if (future > strike) {
    return normIV(parseFloat(pe_iv)) || 0; // OTM Put
  } else {
    return normIV(parseFloat(ce_iv)) || 0; // OTM Call
  }
}

function parseResultDate(dateStr) {
  if (!dateStr) return null;
  const months = { "jan": 0, "feb": 1, "mar": 2, "apr": 3, "may": 4, "jun": 5, "jul": 6, "aug": 7, "sep": 8, "oct": 9, "nov": 10, "dec": 11 };
  const parts = dateStr.trim().split(/[\s\-\/]+/);
  if (parts.length >= 3) {
    if (parts[0].length === 4) {
       return new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
    } else {
       const d = parseInt(parts[0], 10);
       const m = months[parts[1].toLowerCase().substring(0, 3)];
       const y = parseInt(parts[2], 10);
       if (!isNaN(d) && m !== undefined && !isNaN(y)) {
         return new Date(y, m, d);
       }
    }
  }
  return null;
}

function getScreenerData(sym, booksStore, activeExps, cAdj, eodStore, overridesStore, targetExpiry) {
  const expiries = activeExps[sym];
  if (!expiries || !expiries.length) return null;
  
  const currExp = (targetExpiry && expiries.includes(targetExpiry)) ? targetExpiry : expiries[0];
  const currIdx = expiries.indexOf(currExp);
  const nextExp = currIdx !== -1 && currIdx + 1 < expiries.length ? expiries[currIdx + 1] : null;
  
  const bookC = booksStore[`${sym}_${currExp}`];
  const bookN = nextExp ? booksStore[`${sym}_${nextExp}`] : null;
  
  if (!bookC || !bookC.future || !bookC.rows.length) return null;
  
  let futC = bookC.future;
  if (overridesStore?.futures?.[currExp]) {
      const o = overridesStore.futures[currExp];
      if (o.base_expiry && booksStore[`${sym}_${o.base_expiry}`]?.future != null) futC = booksStore[`${sym}_${o.base_expiry}`].future + o.offset;
      else if (o.offset != null && !isNaN(o.offset)) futC = o.offset;
  }

  let futN = bookN ? bookN.future : null;
  if (bookN && overridesStore?.futures?.[nextExp]) {
      const o = overridesStore.futures[nextExp];
      if (o.base_expiry && booksStore[`${sym}_${o.base_expiry}`]?.future != null) futN = booksStore[`${sym}_${o.base_expiry}`].future + o.offset;
      else if (o.offset != null && !isNaN(o.offset)) futN = o.offset;
  }

  const findAtm = (book, fPx, exp) => {
      const ovrStrike = overridesStore?.strikes?.[exp];
      const targetK = ovrStrike || book.atm_strike;
      if (targetK) {
          const row = book.rows.find(r => r.k === targetK);
          if (row) return row;
          return { k: targetK }; // Dummy row strictly enforces true ATM to avoid far-OTM jumps
      }
      if (book.rows.length) return book.rows.reduce((p, c) => Math.abs(c.k - fPx) < Math.abs(p.k - fPx) ? c : p);
      return { k: Math.round(fPx) };
  };

  const atmC = findAtm(bookC, futC, currExp);
  const atmN = bookN && bookN.rows.length ? findAtm(bookN, futN, nextExp) : null;

  const dte = getDTE(currExp);
  const cFactor = calculateAdjFactor(dte, cAdj);
  
  const rawCIV = getBlendedIV(atmC.ce_iv, atmC.pe_iv, futC, atmC.k);
  const rawNIV = atmN ? getBlendedIV(atmN.ce_iv, atmN.pe_iv, futN, atmN.k) : 0;

  const cIV = rawCIV * cFactor;
  const nIV = rawNIV * cFactor;

  const getEodBlended = (fPx, exp, strike) => {
      if (!strike) return null;
      let type = fPx > strike ? 'PE' : 'CE';
      let val = getEodVal(eodStore, sym, exp, strike, type, true);
      if (val == null) {
          type = type === 'CE' ? 'PE' : 'CE';
          val = getEodVal(eodStore, sym, exp, strike, type, true);
      }
      return val != null ? normIV(val) : null;
  };

  const rawYestCIV = getEodBlended(futC, currExp, atmC.k);
  const rawYestNIV = atmN ? getEodBlended(futN, nextExp, atmN.k) : null;

  const yestCIV = rawYestCIV != null ? rawYestCIV * cFactor : null;
  const yestNIV = rawYestNIV != null ? rawYestNIV * cFactor : null;

  const chg1DC = (cIV != null && yestCIV != null && yestCIV > 0) ? cIV - yestCIV : (atmC.ce_chg1d != null && atmC.ce_chg1d > -9000 ? normIV(atmC.ce_chg1d) * cFactor : null);
  const chg1DN = (nIV != null && yestNIV != null && yestNIV > 0) ? nIV - yestNIV : (atmN?.ce_chg1d != null && atmN.ce_chg1d > -9000 ? normIV(atmN.ce_chg1d) * cFactor : null);

  return {
    sym,
    future: futC,
    futureN: futN,
    atmK: atmC.k,
    atmKN: atmN ? atmN.k : null,
    cIV: cIV,
    yestCIV: yestCIV,
    nIV: nIV,
    yestNIV: yestNIV,
    chg5C: atmC.ce_chg5 != null ? normIV(atmC.ce_chg5) * cFactor : null,
    chg15C: atmC.ce_chg15 != null ? normIV(atmC.ce_chg15) * cFactor : null,
    chg30C: atmC.ce_chg30 != null ? normIV(atmC.ce_chg30) * cFactor : null,
    chg60C: atmC.ce_chg60 != null ? normIV(atmC.ce_chg60) * cFactor : null,
    chg1DC: chg1DC,
    chg5N: atmN?.ce_chg5 != null ? normIV(atmN.ce_chg5) * cFactor : null,
    chg15N: atmN?.ce_chg15 != null ? normIV(atmN.ce_chg15) * cFactor : null,
    chg30N: atmN?.ce_chg30 != null ? normIV(atmN.ce_chg30) * cFactor : null,
    chg60N: atmN?.ce_chg60 != null ? normIV(atmN.ce_chg60) * cFactor : null,
    chg1DN: chg1DN,
    maxC: atmC.ce_max_iv != null ? atmC.ce_max_iv * cFactor : cIV,
    minC: atmC.ce_min_iv != null ? atmC.ce_min_iv * cFactor : cIV,
    oi: atmC.ce_oi
  };
}

function ScreenerRow(props) {
  const r = createMemo(() => {
     return getScreenerData(props.sym, props.books, props.activeExpiries(), props.currAdj(), props.eodStore, props.overridesStore, null);
  });
  
  const resInfo = createMemo(() => props.resultsMap().get(props.sym));

  const colorClass = (val) => {
    if (val === null || isNaN(val)) return "";
    return val > 0 ? "color-up" : val < 0 ? "color-down" : "";
  };

  return (
    <Show when={r()}>
      <tr class={BLUE_CHIPS.has(props.sym.toUpperCase()) ? "blue-chip-row" : ""}>
        <td 
          onClick={(e) => { e.stopPropagation(); props.toggleWatchlist(props.sym); }} 
          style={{ cursor: "pointer", color: props.watchlist().has(props.sym) ? "var(--yellow)" : "var(--text)", "text-align": "center" }}
        >
          {props.watchlist().has(props.sym) ? "★" : "☆"}
        </td>
        <For each={props.colOrder().filter(id => props.colVis()[id] !== false)}>
          {(colId) => {
            const val = () => r()[colId];
            const cssClass = () => {
            if (colId === "atmK" || colId === "atmKN") return "strike";
            if (colId === "cIV" || colId === "nIV") return "highlight-col";
              if (colId.startsWith("chg")) return colorClass(val());
              return "";
            };
            const style = () => {
              if (colId === "sym") return { "font-weight": "bold", color: "var(--blue)", "vertical-align": "middle" };
              if (colId === "cIV") return { "font-weight": "bold", color: "var(--green)" };
              return {};
            };
            const display = () => {
              if (colId === "sym") {
                return (
                  <div style={{ display: "flex", "flex-direction": "column", "align-items": "flex-start", gap: "2px" }}>
                    <span 
                      style={{ cursor: "pointer", "text-decoration": "underline" }}
                      onClick={(e) => {
                         e.stopPropagation();
                         props.setHistorySymbol(props.sym);
                         props.setActiveTab("History");
                      }}
                    >
                      {props.sym}
                    </span>
                    <Show when={resInfo() && resInfo().date}>
                      <span style={{ "font-size": "10px", color: "var(--yellow)", "font-weight": "normal", "margin-top": "2px" }}>
                        {resInfo().date}
                      </span>
                    </Show>
                  </div>
                );
              }
              const v = val();
              if (colId === "cIV" || colId === "nIV" || colId === "yestCIV" || colId === "yestNIV" || colId === "maxC" || colId === "minC") {
                return v != null ? fmt(v) + '%' : '-';
              }
              if (colId.startsWith("chg")) {
                return v != null ? fmt(v) + '%' : '-';
              }
            if (colId === "atmK" || colId === "atmKN" || colId === "future" || colId === "futureN" || colId === "oi") {
                return v != null ? fmt(v) : '-';
              }
              return v;
            };
            return <FlashTd class={cssClass()} style={style()} val={val()} display={display()} />;
          }}
        </For>
      </tr>
    </Show>
  );
}

function LiveScreener(props) {
  const [blueChipOnly, setBlueChipOnly] = createSignal(false);
  const [showResultsAfterExpiry, setShowResultsAfterExpiry] = createSignal(JSON.parse(localStorage.getItem('iv_results_filter') || 'false'));
  const [currAdj, setCurrAdj] = createSignal(0);
  const [prevAdj, setPrevAdj] = createSignal(0);

  const [sortCol, setSortCol] = createSignal("cIV");
  const [sortAsc, setSortAsc] = createSignal(false);
  
  const resultsMap = createMemo(() => new Map((props.resultsData ? props.resultsData() : []).map(r => [r.stock, r])));

  const toggleResultsFilter = () => {
    setShowResultsAfterExpiry(!showResultsAfterExpiry());
    localStorage.setItem('iv_results_filter', JSON.stringify(showResultsAfterExpiry()));
  };

  const defaultCols = [
    { id: "sym", label: "Scrip" },
    { id: "future", label: "C Fut" },
    { id: "atmK", label: "C ATM" },
    { id: "cIV", label: "C IV" },
    { id: "yestCIV", label: "Yest C IV" },
    { id: "chg1DC", label: "1D C" },
    { id: "futureN", label: "N Fut" },
    { id: "atmKN", label: "N ATM" },
    { id: "nIV", label: "N IV" },
    { id: "yestNIV", label: "Yest N IV" },
    { id: "chg1DN", label: "1D N" },
    { id: "chg5C", label: "5m C" },
    { id: "chg15C", label: "15m C" },
    { id: "chg30C", label: "30m C" },
    { id: "chg60C", label: "60m C" },
    { id: "chg5N", label: "5m N" },
    { id: "chg15N", label: "15m N" },
    { id: "chg30N", label: "30m N" },
    { id: "chg60N", label: "60m N" },
    { id: "maxC", label: "Max C IV" },
    { id: "minC", label: "Min C IV" },
    { id: "oi", label: "OI" }
  ];

  const [colOrder, setColOrder] = createSignal(JSON.parse(localStorage.getItem('iv_col_order')) || defaultCols.map(c => c.id));
  const [colVis, setColVis] = createSignal(JSON.parse(localStorage.getItem('iv_col_vis')) || {});
  const [draggedCol, setDraggedCol] = createSignal(null);

  createEffect(() => {
    const current = colOrder();
    const missing = defaultCols.map(c => c.id).filter(id => !current.includes(id));
    if (missing.length) setColOrder([...current, ...missing]);
  });

  const toggleCol = (id) => {
    setColVis(p => {
       const next = {...p, [id]: p[id] === false ? true : false};
       localStorage.setItem('iv_col_vis', JSON.stringify(next));
       return next;
    });
  };

  const handleDragStart = (e, id) => {
    setDraggedCol(id);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDrop = (e, targetId) => {
    e.preventDefault();
    const sourceId = draggedCol();
    if (!sourceId || sourceId === targetId) return;
    setColOrder(prev => {
      const next = [...prev];
      next.splice(next.indexOf(sourceId), 1);
      next.splice(next.indexOf(targetId), 0, sourceId);
      localStorage.setItem('iv_col_order', JSON.stringify(next));
      return next;
    });
    setDraggedCol(null);
  };

  const handleSort = (col) => {
    if (sortCol() === col) setSortAsc(!sortAsc());
    else { setSortCol(col); setSortAsc(false); }
  };

  const sortedSymbols = createMemo(() => {
    props.tick(); // Heartbeat evaluation for fast sorts
    return untrack(() => {
      const b = props.books;
      const activeExps = props.activeExpiries();
      const cA = currAdj();
      const eStore = props.eodStore;
      
      const list = [];
      for (const sym of props.symbols()) {
        if (blueChipOnly() && !BLUE_CHIPS.has(sym.toUpperCase())) continue;
        
        if (showResultsAfterExpiry()) {
          const info = resultsMap().get(sym);
          if (!info || !info.date) continue;
          const expStr = activeExps[sym] && activeExps[sym][0];
          if (!expStr) continue;
          const dte = getDTE(expStr);
          const rDate = parseResultDate(info.date);
          if (!rDate || rDate.getTime() <= (Date.now() + dte * 86400000)) continue;
        }

        const data = getScreenerData(sym, b, activeExps, cA, eStore, props.overridesStore, null);
        if (data) list.push(data);
      }
  
      list.sort((a, b) => {
        const aW = props.watchlist().has(a.sym);
        const bW = props.watchlist().has(b.sym);
        if (aW && !bW) return -1;
        if (!aW && bW) return 1;
        
        if (!sortCol()) return 0;
        const va = a[sortCol()];
        const vb = b[sortCol()];
        if (va === vb) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        return va < vb ? (sortAsc() ? -1 : 1) : (sortAsc() ? 1 : -1);
      });
      
      return list.map(x => x.sym);
    });
  });

  return (
    <div style={{ padding: "20px", display: "flex", "flex-direction": "column", gap: "20px", flex: 1, minHeight: 0, overflowY: "auto" }}>
      <div class="toolbar" style={{ "justify-content": "flex-start", padding: "10px 20px", "border-radius": "6px", gap: "20px" }}>
        <button 
          class={blueChipOnly() ? "expTab active" : "expTab"} 
          onClick={() => setBlueChipOnly(!blueChipOnly())}
        >
          Blue Chip Stocks Only
        </button>

        <button 
          class={showResultsAfterExpiry() ? "expTab active" : "expTab"} 
          onClick={toggleResultsFilter}
        >
          {showResultsAfterExpiry() ? "Show All Stocks" : "Show Results After Expiry"}
        </button>
        
        <div style={{ display: "flex", gap: "15px", "align-items": "center", "margin-left": "auto" }}>
          <div style={{ display: "flex", gap: "10px", "align-items": "center" }}>
            <label for="curr-adj-input-screener" style={{ "font-weight": "bold", "font-size": "12px" }}>Curr Adj (days):</label>
            <input id="curr-adj-input-screener" name="curr_adj_screener" class="select" type="number" value={currAdj()} onChange={e => setCurrAdj(parseFloat(e.target.value) || 0)} style={{ width: "80px", padding: "4px" }} />
          </div>
          <div style={{ display: "flex", gap: "10px", "align-items": "center" }}>
            <label for="prev-adj-input-screener" style={{ "font-weight": "bold", "font-size": "12px" }}>Prev Adj (days):</label>
            <input id="prev-adj-input-screener" name="prev_adj_screener" class="select" type="number" value={prevAdj()} onChange={e => setPrevAdj(parseFloat(e.target.value) || 0)} style={{ width: "80px", padding: "4px" }} />
          </div>
        </div>
      </div>

      <div style={{ display: "flex", "flex-wrap": "wrap", gap: "8px", margin: "0 0 10px 0" }}>
        <For each={defaultCols}>
          {(col) => (
            <button
              class={colVis()[col.id] !== false ? "expTab active" : "expTab"}
              style={{ padding: "4px 8px", "font-size": "11px" }}
              onClick={() => toggleCol(col.id)}
            >
              {colVis()[col.id] !== false ? "Hide " : "Show "} {col.label}
            </button>
          )}
        </For>
      </div>

      <div class="tableWrap" style={{ border: "1px solid var(--border)", "border-radius": "6px", resize: "vertical", overflow: "auto", "min-height": "200px" }}>
        <table>
          <thead>
            <tr>
              <th style={{ width: "30px", "text-align": "center" }}>★</th>
              <For each={colOrder().filter(id => colVis()[id] !== false)}>
                {(colId) => {
                  const colDef = defaultCols.find(c => c.id === colId);
                  if (!colDef) return null;
                  return (
                    <th
                      draggable="true"
                      onDragStart={(e) => handleDragStart(e, colId)}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={(e) => handleDrop(e, colId)}
                      onClick={() => handleSort(colId)}
                      style={{ cursor: "pointer", "user-select": "none", "white-space": "nowrap" }}
                      class={colId === "atmK" ? "strike" : ""}
                    >
                      <div style={{ display: "flex", "align-items": "center", gap: "5px" }}>
                        <span style={{ cursor: "grab", opacity: 0.5, "font-size": "10px" }}>☰</span>
                        <span>{colDef.label}</span>
                        <span style={{ color: sortCol() === colId ? "var(--blue)" : "var(--muted)", "font-size": "10px", "margin-left": "auto" }}>
                          {sortCol() === colId ? (sortAsc() ? "▲" : "▼") : "↕"}
                        </span>
                      </div>
                    </th>
                  );
                }}
              </For>
            </tr>
          </thead>
          <tbody>
            <For each={sortedSymbols()}>
              {(sym) => <ScreenerRow 
                sym={sym} 
                books={props.books} 
                activeExpiries={props.activeExpiries}
                currAdj={currAdj} 
                eodStore={props.eodStore}
                tick={props.tick}
                colOrder={colOrder} 
                colVis={colVis} 
                watchlist={props.watchlist} 
                toggleWatchlist={props.toggleWatchlist} 
                resultsMap={resultsMap}
                setActiveTab={props.setActiveTab}
                setHistorySymbol={props.setHistorySymbol}
              />}
            </For>
            <Show when={sortedSymbols().length === 0}>
              <tr>
                <td colspan="13" class="empty" style={{ "text-align": "center", padding: "20px" }}>
                  Waiting for market data stream to populate screener...
                </td>
              </tr>
            </Show>
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ==========================================================
   STOCK HISTORY VIEW (Modular Advanced Dashboard)
========================================================== */

function StockHistory(props) {
  const [data, setData] = createSignal([]);
  const [loading, setLoading] = createSignal(false);
  
  // Advanced Feature States
  const [currAdj, setCurrAdj] = createSignal(0);
  const [prevAdj, setPrevAdj] = createSignal(0);
  const [panelConfig, setPanelConfig] = createSignal({ expiry: "", strikes: 10 });
  const [showChain, setShowChain] = createSignal(true);

  // Fetch historical data from the C++ Backend (Port 9001)
  createEffect(() => {
    const sym = props.historySymbol();
    if (!sym) return;
    
    setLoading(true);
    fetch(`${API_BASE}/get-historical-data/${sym}`)
      .then(r => r.json())
      .then(d => {
        // Reverse the array so the newest rows are at the top!
        if (Array.isArray(d)) setData(d.reverse());
        else setData([]);
        setLoading(false);
      })
      .catch(e => {
        console.error("History fetch error:", e);
        setData([]);
        setLoading(false);
      });
  });

  return (
    <div style={{ padding: "20px", display: "flex", "flex-direction": "column", gap: "20px", flex: 1, minHeight: 0, overflowY: "auto" }}>
      
      {/* 1. Modualized Controls */}
      <HistoryControls 
         symbols={props.symbols} 
         selected={props.historySymbol} setSelected={props.setHistorySymbol}
         currAdj={currAdj} setCurrAdj={setCurrAdj}
         prevAdj={prevAdj} setPrevAdj={setPrevAdj}
      />
      
      {/* Stock Details Intraday Changes Summary */}
      <StockDetailsSummary 
        selected={props.historySymbol} 
        books={props.books} 
        activeExpiries={props.activeExpiries} 
        currAdj={currAdj} 
        eodStore={props.eodStore} 
        overridesStore={props.overridesStore}
        tick={props.tick} 
        expiry={panelConfig().expiry}
        historyData={data}
      />

      {/* 2. Embedded Real-Time Option Chain */}
      <div style={{ "flex-shrink": 0 }}>
        <Show when={props.historySymbol()}>
          <div style={{ display: "flex", "justify-content": "space-between", "align-items": "center", margin: "0 0 10px 0" }}>
            <h3 style={{ margin: 0, color: "var(--text)", "font-family": "sans-serif" }}>Real-Time Option Chain</h3>
            <button class="addBtn" onClick={() => setShowChain(!showChain())}>
              {showChain() ? "Hide Chain" : "Show Chain"}
            </button>
          </div>
          
          <Show when={showChain()}>
            <div style={{ border: "1px solid var(--border)", "border-radius": "6px" }}>
              <Panel 
                panel={{ id: 'hist', symbol: props.historySymbol(), expiry: panelConfig().expiry, strikes: panelConfig().strikes }} 
                books={props.books} 
                activeExpiries={props.activeExpiries}
                symbols={props.symbols} 
                onUpdate={(patch) => { 
                   if (patch.symbol) props.setHistorySymbol(patch.symbol); 
                   setPanelConfig(p => ({ ...p, ...patch })); 
                }}
                hideClose={true}
              />
            </div>
          </Show>
        </Show>
      </div>

      {/* 3. Modularized History Table */}
      <HistoryTable data={data} loading={loading} selected={props.historySymbol} currAdj={currAdj} prevAdj={prevAdj} />
      
    </div>
  );
}

/* ==========================================================
   HISTORY CONTROLS (Component)
========================================================== */
function HistoryControls(props) {
  return (
    <div class="toolbar" style={{ "justify-content": "space-between", padding: "10px 20px", "border-radius": "6px" }}>
      <div style={{ display: "flex", gap: "10px", "align-items": "center" }}>
        <span style={{ "font-weight": "bold" }}>Select Symbol:</span>
        <select class="select" value={props.selected()} onChange={e => props.setSelected(e.target.value)}>
          <For each={props.symbols()}>
            {(s) => <option value={s}>{s}</option>}
          </For>
        </select>
      </div>
      <div style={{ display: "flex", gap: "15px", "align-items": "center" }}>
        <label for="curr-adj-input-hist" style={{ "font-weight": "bold", "font-size": "12px" }}>Curr Adj (days):</label>
        <input id="curr-adj-input-hist" name="curr_adj_hist" class="select" type="number" value={props.currAdj()} onChange={e => props.setCurrAdj(parseFloat(e.target.value) || 0)} style={{ width: "80px" }} />
        
        <label for="prev-adj-input-hist" style={{ "font-weight": "bold", "font-size": "12px" }}>Prev Adj (days):</label>
        <input id="prev-adj-input-hist" name="prev_adj_hist" class="select" type="number" value={props.prevAdj()} onChange={e => props.setPrevAdj(parseFloat(e.target.value) || 0)} style={{ width: "80px" }} />
      </div>
    </div>
  );
}

/* ==========================================================
   STOCK DETAILS SUMMARY (Component)
========================================================== */
function StockDetailsSummary(props) {
  const liveData = createMemo(() => {
     props.tick();
     return untrack(() => {
         if (!props.selected()) return null;
         const targetExp = props.expiry || (props.activeExpiries()[props.selected()]?.[0]);
         return getScreenerData(props.selected(), props.books, props.activeExpiries(), props.currAdj(), props.eodStore, props.overridesStore, targetExp);
     });
  });

  const r = () => liveData() || {};

  const cFactorVal = createMemo(() => {
      const sym = props.selected();
      const exps = props.activeExpiries()[sym];
      const exp = props.expiry || (exps && exps[0]);
      return calculateAdjFactor(getDTE(exp), props.currAdj());
  });

  const yestIVFromEOD = createMemo(() => {
     const sym = props.selected();
     const exps = props.activeExpiries()[sym];
     const exp = props.expiry || (exps && exps[0]);
     if (!sym || !exp) return null;
     
     const cF = cFactorVal();

     // 1. If it's the near expiry and we have history, USE HISTORY!
     if (exps && exp === exps[0] && props.historyData && props.historyData().length > 0) {
         const histIV = parseFloat(props.historyData()[0].IV);
         if (!isNaN(histIV)) return histIV * cF;
     }

     const book = props.books[`${sym}_${exp}`];
     if (!book || !book.rows.length || !book.future) return null;
     
     const atm = (book.atm_strike && book.rows.find(r => r.k === book.atm_strike))
                 ? book.rows.find(r => r.k === book.atm_strike)
                 : book.rows.reduce((p, c) => Math.abs(c.k - book.future) < Math.abs(p.k - book.future) ? c : p);

     const ceIvEod = getEodVal(props.eodStore, sym, exp, atm.k, 'CE', true);
     const peIvEod = getEodVal(props.eodStore, sym, exp, atm.k, 'PE', true);

     if (ceIvEod != null || peIvEod != null) {
         return untrack(() => {
             let ivC = ceIvEod || 0;
             let ivP = peIvEod || 0;
             if (ivC > 0 && ivP > 0) return ((ivC + ivP) / 2) * cF;
             if (ivC > 0) return ivC * cF;
             if (ivP > 0) return ivP * cF;
             return null;
         }) || fallbackLive();
     }
     
     // 3. Fallback to Live IV - chg1d
     function fallbackLive() {
         return untrack(() => {
             const ce_iv = normIV(atm.ce_iv);
             const ce_chg = normIV(atm.ce_chg1d);
             const pe_iv = normIV(atm.pe_iv);
             const pe_chg = normIV(atm.pe_chg1d);

             const yestCE = (ce_iv != null && ce_chg != null && ce_chg > -9000) ? ce_iv - ce_chg : null;
             const yestPE = (pe_iv != null && pe_chg != null && pe_chg > -9000) ? pe_iv - pe_chg : null;
             
             if (yestCE != null && yestPE != null) return ((yestCE + yestPE) / 2) * cF;
             if (yestCE != null) return yestCE * cF;
             if (yestPE != null) return yestPE * cF;
             return null;
         });
     }

     return fallbackLive();
  });

  const ivVsYestDiff = createMemo(() => {
      const yest = yestIVFromEOD();
      const curr = r().cIV;
      if (yest != null && curr != null && yest > 0) {
          return curr - yest;
      }
      return null;
  });

  const colorClass = (val) => {
    const num = parseFloat(val);
    if (isNaN(num)) return "";
    return num > 0 ? "color-up" : num < 0 ? "color-down" : "";
  };

  const formatP = (val) => {
     if (val == null) return '-';
     return fmt(val) + '%';
  };

  return (
    <Show when={r().sym}>
      <div style={{ display: "flex", gap: "30px", padding: "15px 20px", background: "var(--panel)", border: "1px solid var(--border)", "border-radius": "6px" }}>
        <div style={{ display: "flex", "flex-direction": "column", gap: "5px" }}>
          <span style={{ "font-size": "12px", color: "var(--muted)", "text-transform": "uppercase", "letter-spacing": "0.5px" }}>5m Δ</span>
          <span class={colorClass(r().chg5C)} style={{ "font-size": "18px", "font-weight": "bold" }}>
            {formatP(r().chg5C)}
          </span>
        </div>
        <div style={{ display: "flex", "flex-direction": "column", gap: "5px" }}>
          <span style={{ "font-size": "12px", color: "var(--muted)", "text-transform": "uppercase", "letter-spacing": "0.5px" }}>15m Δ</span>
          <span class={colorClass(r().chg15C)} style={{ "font-size": "18px", "font-weight": "bold" }}>
            {formatP(r().chg15C)}
          </span>
        </div>
        <div style={{ display: "flex", "flex-direction": "column", gap: "5px" }}>
          <span style={{ "font-size": "12px", color: "var(--muted)", "text-transform": "uppercase", "letter-spacing": "0.5px" }}>30m Δ</span>
          <span class={colorClass(r().chg30C)} style={{ "font-size": "18px", "font-weight": "bold" }}>
            {formatP(r().chg30C)}
          </span>
        </div>
        <div style={{ display: "flex", "flex-direction": "column", gap: "5px" }}>
          <span style={{ "font-size": "12px", color: "var(--muted)", "text-transform": "uppercase", "letter-spacing": "0.5px" }}>60m Δ</span>
          <span class={colorClass(r().chg60C)} style={{ "font-size": "18px", "font-weight": "bold" }}>
            {formatP(r().chg60C)}
          </span>
        </div>
        <div style={{ width: "1px", background: "var(--border)", margin: "0 10px" }}></div>
        <div style={{ display: "flex", "flex-direction": "column", gap: "5px" }}>
          <span style={{ "font-size": "12px", color: "var(--muted)", "text-transform": "uppercase", "letter-spacing": "0.5px" }}>Yesterday IV</span>
          <span style={{ "font-size": "18px", "font-weight": "bold", color: "var(--text)" }}>
            {yestIVFromEOD() != null && yestIVFromEOD() > 0 ? fmt(yestIVFromEOD()) + '%' : '-'}
          </span>
        </div>
        <div style={{ display: "flex", "flex-direction": "column", gap: "5px" }}>
          <span style={{ "font-size": "12px", color: "var(--muted)", "text-transform": "uppercase", "letter-spacing": "0.5px" }}>IV vs Yest Δ</span>
          <span class={colorClass(ivVsYestDiff())} style={{ "font-size": "18px", "font-weight": "bold" }}>
            {formatP(ivVsYestDiff())}
          </span>
        </div>
      </div>
    </Show>
  );
}

/* ==========================================================
   HISTORY TABLE & MATH (Component)
========================================================== */
function calculateAdjFactor(dte, adj) {
  if (!dte || isNaN(dte)) return 1;
  const denom = dte - adj;
  if (denom <= 0) return 1; // Safety fallback against infinity
  return Math.sqrt(dte / denom);
}

function calculatePrevAdjFactor(dte, adj) {
  if (!dte || isNaN(dte)) return 1;
  const prevDTE = dte + 1; // Estimate yesterday's DTE
  return Math.sqrt((prevDTE + adj) / prevDTE);
}

function HistoryTable(props) {
  const [sortCol, setSortCol] = createSignal("");
  const [sortAsc, setSortAsc] = createSignal(true);

  const defaultCols = [
    { id: "Date", label: "Date" },
    { id: "Price", label: "Price" },
    { id: "Strike", label: "Strike" },
    { id: "Call", label: "Call" },
    { id: "Put", label: "Put" },
    { id: "Call IV", label: "Call IV" },
    { id: "Put IV", label: "Put IV" },
    { id: "IV", label: "IV" },
    { id: "Open", label: "Open" },
    { id: "High", label: "High" },
    { id: "Low", label: "Low" },
    { id: "Close", label: "Close" },
    { id: "Straddle", label: "Straddle" },
    { id: "DTE", label: "DTE" },
    { id: "ATR", label: "ATR" },
    { id: "OG", label: "OG" },
    { id: "Abs Returns", label: "Abs Ret" },
    { id: "IDV", label: "IDV" },
    { id: "Returns", label: "Returns" },
    { id: "FV", label: "FV" },
    { id: "ExpiryDate", label: "Expiry Date" },
    { id: "IntradayIDV", label: "Intraday IDV" }
  ];

  const [colOrder, setColOrder] = createSignal(JSON.parse(localStorage.getItem('hist_col_order')) || defaultCols.map(c => c.id));
  const [colVis, setColVis] = createSignal(JSON.parse(localStorage.getItem('hist_col_vis')) || {});
  const [draggedCol, setDraggedCol] = createSignal(null);

  createEffect(() => {
    const current = colOrder();
    const missing = defaultCols.map(c => c.id).filter(id => !current.includes(id));
    if (missing.length) {
      setColOrder([...current, ...missing]);
      localStorage.setItem('hist_col_order', JSON.stringify([...current, ...missing]));
    }
  });

  const toggleCol = (id) => {
    setColVis(p => {
       const next = {...p, [id]: p[id] === false ? true : false};
       localStorage.setItem('hist_col_vis', JSON.stringify(next));
       return next;
    });
  };

  const handleDragStart = (e, id) => {
    setDraggedCol(id);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDrop = (e, targetId) => {
    e.preventDefault();
    const sourceId = draggedCol();
    if (!sourceId || sourceId === targetId) return;
    setColOrder(prev => {
      const next = [...prev];
      next.splice(next.indexOf(sourceId), 1);
      next.splice(next.indexOf(targetId), 0, sourceId);
      localStorage.setItem('hist_col_order', JSON.stringify(next));
      return next;
    });
    setDraggedCol(null);
  };

  const sortedData = createMemo(() => {
    let d = [...props.data()];
    if (!sortCol()) return d;
    
    return d.sort((a, b) => {
      let va = a[sortCol()];
      let vb = b[sortCol()];
      
      let nA = parseFloat(va);
      let nB = parseFloat(vb);
      if (!isNaN(nA) && !isNaN(nB)) { va = nA; vb = nB; }
      
      if (va === vb) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return va < vb ? (sortAsc() ? -1 : 1) : (sortAsc() ? 1 : -1);
    });
  });

  const handleSort = (col) => {
    if (sortCol() === col) setSortAsc(!sortAsc());
    else { setSortCol(col); setSortAsc(true); }
  };

  const Th = (p) => (
    <th onClick={() => handleSort(p.col)} style={{ cursor: "pointer", "user-select": "none", "white-space": "nowrap" }} class={p.class || ""}>
      {p.label}
      <span style={{ "margin-left": "5px", color: sortCol() === p.col ? "var(--blue)" : "var(--muted)" }}>
        {sortCol() === p.col ? (sortAsc() ? "▲" : "▼") : "↕"}
      </span>
    </th>
  );

  const colorClass = (val) => {
    const num = parseFloat(val);
    if (isNaN(num)) return "";
    return num > 0 ? "color-up" : num < 0 ? "color-down" : "";
  };

  return (
    <div style={{ display: "flex", "flex-direction": "column" }}>
      <div style={{ display: "flex", "justify-content": "space-between", "align-items": "center", margin: "0 0 10px 0" }}>
        <h3 style={{ margin: 0, color: "var(--text)", "font-family": "sans-serif" }}>Historical Metrics: {props.selected()}</h3>
      </div>
      
      <div style={{ display: "flex", "flex-wrap": "wrap", gap: "8px", margin: "0 0 10px 0" }}>
        <For each={defaultCols}>
          {(col) => (
            <button
              class={colVis()[col.id] !== false ? "expTab active" : "expTab"}
              style={{ padding: "4px 8px", "font-size": "11px" }}
              onClick={() => toggleCol(col.id)}
            >
              {colVis()[col.id] !== false ? "Hide " : "Show "} {col.label}
            </button>
          )}
        </For>
      </div>

        <div class="tableWrap" style={{ border: "1px solid var(--border)", "border-radius": "6px", resize: "vertical", overflow: "auto", "min-height": "200px" }}>
        <Show when={!props.loading()} fallback={<div class="empty">Loading historical data...</div>}>
          <Show when={sortedData().length > 0} fallback={<div class="empty">No history available for {props.selected()}</div>}>
            <table>
              <thead>
                <tr>
                  <Th col="Date" label="Date" />
                  <Th col="Price" label="Price" />
                  <Th col="Strike" label="Strike" class="strike" />
                  <Th col="Call" label="Call" />
                  <Th col="Put" label="Put" />
                  <Th col="Call IV" label="Call IV" />
                  <Th col="Put IV" label="Put IV" />
                  <Th col="IV" label="IV" />
                  <Th col="Open" label="Open" />
                  <Th col="High" label="High" />
                  <Th col="Low" label="Low" />
                  <Th col="Close" label="Close" />
                  <Th col="Straddle" label="Straddle" />
                  <Th col="DTE" label="DTE" />
                  <Th col="ATR" label="ATR" />
                  <Th col="OG" label="OG" />
                  <Th col="Abs Returns" label="Abs Ret" />
                  <Th col="IDV" label="IDV" />
                  <Th col="Returns" label="Returns" />
                  <Th col="FV" label="FV" />
                  <Th col="ExpiryDate" label="Expiry Date" />
                  <Th col="IntradayIDV" label="Intraday IDV" />
                  <For each={colOrder().filter(id => colVis()[id] !== false)}>
                    {(colId) => {
                      const colDef = defaultCols.find(c => c.id === colId);
                      if (!colDef) return null;
                      return (
                        <th
                          draggable="true"
                          onDragStart={(e) => handleDragStart(e, colId)}
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={(e) => handleDrop(e, colId)}
                          onClick={() => handleSort(colId)}
                          style={{ cursor: "pointer", "user-select": "none", "white-space": "nowrap" }}
                          class={colId === "Strike" ? "strike" : ""}
                        >
                          <div style={{ display: "flex", "align-items": "center", gap: "5px" }}>
                            <span style={{ cursor: "grab", opacity: 0.5, "font-size": "10px" }}>☰</span>
                            <span>{colDef.label}</span>
                            <span style={{ color: sortCol() === colId ? "var(--blue)" : "var(--muted)", "font-size": "10px", "margin-left": "auto" }}>
                              {sortCol() === colId ? (sortAsc() ? "▲" : "▼") : "↕"}
                            </span>
                          </div>
                        </th>
                      );
                    }}
                  </For>
                </tr>
              </thead>
              <tbody>
                <For each={sortedData()}>
                  {(r) => {
                    // Apply adjustment Math
                    const cFactor = calculateAdjFactor(parseFloat(r.DTE), props.currAdj());
                    const pFactor = calculatePrevAdjFactor(parseFloat(r.DTE), props.prevAdj());

                    const adjIV = (val, factor) => {
                      if (typeof val === 'string' && val.endsWith('%')) {
                          const v = parseFloat(val);
                          if (isNaN(v)) return "-";
                          return (v * factor).toFixed(2) + '%';
                      }
                      const v = parseFloat(val);
                      if (isNaN(v)) return "-";
                      return (v * factor).toFixed(2);
                    };

                    return (
                    <tr>
                      <td>{r.Date}</td>
                      <td>{fmt(r.Price)}</td>
                      <td class="strike">{fmt(r.Strike)}</td>
                      <td>{fmt(r.Call)}</td>
                      <td>{fmt(r.Put)}</td>
                      <td>{adjIV(r['Call IV'], cFactor)}</td>
                      <td>{adjIV(r['Put IV'], cFactor)}</td>
                      <td class="highlight-col" style={{ "font-weight": "bold", color: "var(--green)" }}>{adjIV(r.IV, cFactor)}</td>
                      
                      <td>{fmt(r.Open)}</td>
                      <td>{fmt(r.High)}</td>
                      <td>{fmt(r.Low)}</td>
                      <td>{fmt(r.Close)}</td>
                      <td>{fmt(r.Straddle)}</td>
                      <td>{fmt(r.DTE)}</td>
                      <td>{fmt(r.ATR)}</td>
                      <td>{fmt(r.OG)}</td>
                      <td>{fmt(r['Abs Returns'])}</td>
                      <td>{adjIV(r.IDV, cFactor)}</td>
                      <td class={parseFloat(r.Returns) >= 0 ? "color-up" : "color-down"}>{fmt(r.Returns)}</td>
                      <td>{fmt(r.FV)}</td>
                      <td>{r.ExpiryDate}</td>
                      <td>{adjIV(r.IntradayIDV, cFactor)}</td>
                      <For each={colOrder().filter(id => colVis()[id] !== false)}>
                        {(colId) => {
                          let display = "-";
                          let style = {};
                          let cssClass = "";

                          if (colId === "Date") display = r.Date;
                          else if (colId === "ExpiryDate") display = r.ExpiryDate;
                          else if (colId === "Price") display = fmt(r.Price);
                          else if (colId === "Strike") { display = fmt(r.Strike); cssClass = "strike"; }
                          else if (colId === "Call") display = fmt(r.Call);
                          else if (colId === "Put") display = fmt(r.Put);
                          else if (colId === "Call IV") display = adjIV(r['Call IV'], cFactor);
                          else if (colId === "Put IV") display = adjIV(r['Put IV'], cFactor);
                          else if (colId === "IV") {
                            display = adjIV(r.IV, cFactor);
                            style = { "font-weight": "bold", color: "var(--green)" };
                            cssClass = "highlight-col";
                          }
                          else if (colId === "IDV") display = adjIV(r.IDV, cFactor);
                          else if (colId === "IntradayIDV") display = adjIV(r.IntradayIDV, cFactor);
                          else if (colId === "Open") display = fmt(r.Open);
                          else if (colId === "High") display = fmt(r.High);
                          else if (colId === "Low") display = fmt(r.Low);
                          else if (colId === "Close") display = fmt(r.Close);
                          else if (colId === "Straddle") display = fmt(r.Straddle);
                          else if (colId === "DTE") display = fmt(r.DTE);
                          else if (colId === "ATR") display = fmt(r.ATR);
                          else if (colId === "OG") display = fmt(r.OG);
                          else if (colId === "Abs Returns") display = fmt(r['Abs Returns']);
                          else if (colId === "Returns") {
                            display = fmt(r.Returns);
                            cssClass = parseFloat(r.Returns) >= 0 ? "color-up" : "color-down";
                          }
                          else if (colId === "FV") display = fmt(r.FV);

                          return <td class={cssClass} style={style}>{display}</td>;
                        }}
                      </For>
                    </tr>
                    );
                  }}
                </For>
              </tbody>
            </table>
          </Show>
        </Show>
      </div>
    </div>
  );
}

/* ==========================================================
   RESULTS VIEW
========================================================== */
function ResultsView(props) {
  const sortedData = createMemo(() => {
    const raw = props.data() || [];
    if (!Array.isArray(raw)) return [];
    const months = { "jan": 0, "feb": 1, "mar": 2, "apr": 3, "may": 4, "jun": 5, "jul": 6, "aug": 7, "sep": 8, "oct": 9, "nov": 10, "dec": 11 };

    const processed = raw.map(item => {
      let q = item.quarter && item.quarter !== "N/A" ? item.quarter : "N/A";
      let mt = item.month_type && item.month_type !== "N/A" ? item.month_type : "N/A";
      let dateObj = null;

      if (item.date) {
        let y, m, d;
        const parts = item.date.trim().split(/[\s-\/]+/);
        if (parts.length >= 3) {
          if (parts[0].length === 4) { // YYYY-MM-DD format
            y = parseInt(parts[0], 10);
            m = parseInt(parts[1], 10) - 1;
            d = parseInt(parts[2], 10);
          } else { // DD-MMM-YYYY format
            d = parseInt(parts[0], 10);
            m = months[parts[1].toLowerCase().substring(0, 3)];
            y = parseInt(parts[2], 10);
          }
          
          if (!isNaN(d) && m !== undefined && !isNaN(y)) {
            dateObj = new Date(y, m, d);
            const month1 = m + 1;
            
            if (q === "N/A") {
                if ([1, 2, 3].includes(month1)) q = "Q3";
                else if ([4, 5, 6].includes(month1)) q = "Q4";
                else if ([7, 8, 9].includes(month1)) q = "Q1";
                else if ([10, 11, 12].includes(month1)) q = "Q2";
            }
            if (mt === "N/A") {
                mt = [1, 4, 7, 10].includes(month1) ? "First Month" : "Next Month";
            }
          }
        }
      }
      return { ...item, dateStr: item.date, dateObj, quarter: q, month_type: mt };
    });

    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const todayTime = today.getTime();

    return processed.sort((a, b) => {
      if (!a.dateObj && !b.dateObj) return 0;
      if (!a.dateObj) return 1;
      if (!b.dateObj) return -1;

      const aTime = a.dateObj.getTime();
      const bTime = b.dateObj.getTime();

      const catA = aTime === todayTime ? 0 : (aTime > todayTime ? 1 : 2);
      const catB = bTime === todayTime ? 0 : (bTime > todayTime ? 1 : 2);

      if (catA !== catB) return catA - catB;
      if (catA === 0) return a.stock.localeCompare(b.stock);
      if (catA === 1) return aTime - bTime;
      if (catA === 2) return bTime - aTime;
      return 0;
    });
  });

  createEffect(() => {
    fetch(`${API_BASE}/get-results`)
      .then(r => r.json())
      .then(d => {
        props.setData(d || []);
      })
      .catch(e => {
         console.error("Results fetch error:", e);
         props.setData([]);
      });
  });

  const handleResultsCsvUpload = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (evt) => {
      const lines = evt.target.result.split('\n');
      if (lines.length < 2) return;
      const parsed = [];
      for (let i = 1; i < lines.length; i++) {
        const cols = lines[i].split(/,(?=(?:[^"]*"[^"]*")*[^"]*$)/).map(part => part.replace(/^"|"$/g, '').trim());
        if (cols.length >= 2) {
          parsed.push({ stock: cols[0].trim(), date: cols[1].trim() });
        }
      }
      props.setData(parsed);
      e.target.value = '';
    };
    reader.readAsText(file);
  };

  return (
    <div style={{ padding: "20px", display: "flex", "flex-direction": "column", gap: "20px", flex: 1, minHeight: 0, overflowY: "auto" }}>
      <div class="toolbar" style={{ display: "flex", "justify-content": "space-between", padding: "10px 20px", "border-radius": "6px" }}>
        <h3 style={{ margin: 0, color: "var(--text)" }}>Upcoming Stock Results</h3>
        <div style={{ display: "flex", gap: "10px", "align-items": "center" }}>
          <label for="results-csv-upload" style={{ "font-size": "12px", color: "var(--muted)" }}>Upload BSEResults CSV:</label>
          <input id="results-csv-upload" name="results_csv" type="file" accept=".csv" onChange={handleResultsCsvUpload} class="select" style={{ padding: "4px" }} />
        </div>
      </div>

      <div class="tableWrap" style={{ border: "1px solid var(--border)", "border-radius": "6px", resize: "vertical", overflow: "auto", "min-height": "200px" }}>
        <Show when={sortedData()} fallback={<div class="empty" style={{ padding: "20px", "text-align": "center" }}>Loading results data...</div>}>
          <table>
            <thead>
              <tr>
                <th>Stock</th>
                <th>Date</th>
                <th>Quarter</th>
                <th>Month Type</th>
              </tr>
            </thead>
            <tbody>
              <For each={sortedData()}>
                {(row) => (
                  <tr>
                    <td style={{ "font-weight": "bold", color: "var(--blue)" }}>{row.stock}</td>
                    <td>{row.dateStr || 'N/A'}</td>
                    <td>{row.quarter || 'N/A'}</td>
                    <td style={{ color: row.month_type === 'First Month' ? "var(--blue)" : "var(--yellow)" }}>
                      {row.month_type || 'N/A'}
                    </td>
                  </tr>
                )}
              </For>
              <Show when={sortedData().length === 0}>
                <tr><td colspan="4" class="empty" style={{ padding: "20px", "text-align": "center" }}>No upcoming results found.</td></tr>
              </Show>
            </tbody>
          </table>
        </Show>
      </div>
    </div>
  );
}
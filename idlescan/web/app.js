/* idlescan frontend — vanilla ES2020, no dependencies, no build step. */
(() => {
  "use strict";

  // ------------------------------------------------------------------ utils
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const app = $("#app");

  const esc = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));

  const moneyFmt = new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", maximumFractionDigits: 2,
  });
  const moneyFmt0 = new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", maximumFractionDigits: 0,
  });

  const usd = (n, compact = false) =>
    n === null || n === undefined || Number.isNaN(Number(n))
      ? "—"
      : (compact && Math.abs(n) >= 10000 ? moneyFmt0 : moneyFmt).format(Number(n));

  const short = (addr, size = 4) => {
    if (!addr) return "";
    const s = String(addr);
    return s.length > 2 * size + 4 ? `${s.slice(0, size + 2)}…${s.slice(-size)}` : s;
  };

  const safeUrl = (value) => {
    try {
      const url = new URL(String(value));
      return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
    } catch { return null; }
  };

  function formatUnits(raw, decimals = 18, maxFrac = 4) {
    try {
      const value = BigInt(String(raw ?? "0"));
      const base = 10n ** BigInt(decimals);
      const whole = value / base;
      const frac = (value % base).toString().padStart(decimals, "0").slice(0, maxFrac).replace(/0+$/, "");
      return whole.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (frac ? `.${frac}` : "");
    } catch { return String(raw ?? "0"); }
  }

  function timeAgo(iso) {
    if (!iso) return "—";
    const ms = Date.now() - new Date(iso).getTime();
    if (Number.isNaN(ms)) return "—";
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 45) return "just now";
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    if (s < 86400) return `${Math.round(s / 3600)}h ago`;
    if (s < 86400 * 30) return `${Math.round(s / 86400)}d ago`;
    return new Date(iso).toLocaleDateString();
  }

  function fmtDate(iso) {
    if (!iso) return "—";
    const value = String(iso);
    const date = new Date(value.length === 10 ? `${value}T00:00:00Z` : value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  }

  function daysLeft(iso) {
    if (!iso) return null;
    const value = String(iso);
    const date = new Date(value.length === 10 ? `${value}T23:59:59Z` : value);
    if (Number.isNaN(date.getTime())) return null;
    return Math.ceil((date.getTime() - Date.now()) / 86400000);
  }

  function deadlineChip(iso) {
    if (!iso) return `<span class="chip">no deadline</span>`;
    const days = daysLeft(iso);
    if (days === null) return `<span class="chip">${esc(iso)}</span>`;
    if (days < 0) return `<span class="chip" title="${esc(fmtDate(iso))}">expired</span>`;
    if (days === 0) return `<span class="chip chip-urgent" title="${esc(fmtDate(iso))}">due today</span>`;
    if (days <= 14) return `<span class="chip chip-urgent" title="${esc(fmtDate(iso))}">${days}d left</span>`;
    if (days <= 45) return `<span class="chip chip-soon" title="${esc(fmtDate(iso))}">${days}d left</span>`;
    return `<span class="chip chip-ok" title="${esc(fmtDate(iso))}">${fmtDate(iso)}</span>`;
  }

  const statusBadge = (status) => {
    const text = String(status || "unknown").replace(/_/g, " ");
    return `<span class="badge badge-${esc(String(status || "unknown"))}">${esc(text)}</span>`;
  };

  const proofBadge = (value) => {
    if (value === 1 || value === true) return `<span class="badge badge-high">verified</span>`;
    if (value === 0 || value === false) return `<span class="badge badge-critical">mismatch</span>`;
    return `<span class="badge badge-source">unverified</span>`;
  };

  const riskBadge = (risk) => {
    const label = risk?.label || "unknown";
    const text = label === "unknown" ? "unscored" : `${label} confidence`;
    const score = Number(risk?.score || 0);
    const title = `Safety score ${score}/100 — higher is safer: eligibility source, verified proofs, official links, documented deadline`;
    return `<span class="badge badge-${esc(label)}" title="${esc(title)}">${esc(text)}</span>`;
  };

  const riskMeter = (risk) => {
    const score = Number(risk?.score || 0);
    return `<span class="risk-line risk-${esc(risk?.label || "low")}" title="Higher is safer: eligibility source, verified proofs, official links, documented deadline">
      <span class="risk-meter"><i style="width:${Math.max(2, score)}%"></i></span>
      <span>${score}/100</span>
    </span>`;
  };

  const walletLabel = (row) => {
    const label = row.wallet_label || row.label || "";
    return label ? esc(label) : `<span class="dim">unlabelled</span>`;
  };

  const phaseBadge = (phase) => {
    const map = {
      claimable: ["chip-ok", "claimable"],
      closing: ["chip-urgent", "closing soon"],
      expired: ["chip-expired", "expired"],
      completed: ["chip-done", "completed"],
      "no allocation": ["chip", "no allocation"],
      tracking: ["chip", "tracking"],
    };
    const [cls, text] = map[phase] || ["chip", phase || "tracking"];
    return `<span class="chip ${cls}">${esc(text)}</span>`;
  };

  const proofCounts = (proof = {}) =>
    [["verified", proof.verified || 0, "badge-high"],
     ["mismatch", proof.mismatch || 0, "badge-critical"],
     ["unverified", proof.unverified || 0, "badge-source"]]
      .map(([label, count, cls]) => count ? `<span class="badge ${cls}">${count} ${label}</span>` : "")
      .join(" ") || `<span class="dim tiny">no proof checks</span>`;

  function sparkline(values, { label = "history", tone = "accent" } = {}) {
    const series = (values || []).map(Number).filter((value) => Number.isFinite(value));
    if (series.length < 2) {
      return `<div class="spark-empty">Needs at least two snapshots to plot a trend.</div>`;
    }
    const width = 320;
    const height = 64;
    const pad = 5;
    const min = Math.min(...series);
    const max = Math.max(...series);
    const span = max - min || 1;
    const points = series.map((value, index) => {
      const x = pad + (index * (width - pad * 2)) / (series.length - 1);
      const y = height - pad - ((value - min) / span) * (height - pad * 2);
      return [Number(x.toFixed(1)), Number(y.toFixed(1))];
    });
    const line = points.map(([x, y]) => `${x},${y}`).join(" ");
    const area = `M${points[0][0]},${height - pad} L${line.replace(/ /g, " L")} L${points[points.length - 1][0]},${height - pad} Z`;
    const last = points[points.length - 1];
    return `<svg class="spark spark-${esc(tone)}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${esc(label)} trend">
      <path class="spark-area" d="${area}"></path>
      <polyline class="spark-line" points="${line}" vector-effect="non-scaling-stroke"></polyline>
      <circle class="spark-dot" cx="${last[0]}" cy="${last[1]}" r="2.4"></circle>
    </svg>`;
  }

  function barBreakdown(items, { valueKey = "usd_value", labelKey = "chain", empty = "No data yet" } = {}) {
    const rows = (items || []).filter((item) => (item[valueKey] || 0) > 0);
    if (!rows.length) return `<p class="muted small">${esc(empty)}</p>`;
    const total = rows.reduce((sum, item) => sum + (item[valueKey] || 0), 0) || 1;
    return `<div class="breakdown">${rows.map((item) => {
      const value = item[valueKey] || 0;
      const pct = Math.max(1, Math.round((value / total) * 100));
      return `<div class="breakdown-row">
        <span class="breakdown-label">${esc(item[labelKey])}</span>
        <span class="breakdown-track"><i style="width:${pct}%"></i></span>
        <span class="breakdown-value">${usd(value)} <span class="dim">${pct}%</span></span>
      </div>`;
    }).join("")}</div>`;
  }

  const crumb = (label, href) => `<a class="crumb" href="${esc(href)}">← ${esc(label)}</a>`;

  const emptyState = (icon, title, copy, action = "") => `
    <div class="empty">
      <div class="empty-icon">${icon}</div>
      <h3>${esc(title)}</h3>
      <p>${esc(copy)}</p>
      ${action}
    </div>`;

  const errorBlock = (err) => `
    <div class="panel"><div class="empty">
      <div class="empty-icon">!</div>
      <h3>Could not load this view</h3>
      <p>${esc(err?.message || String(err))}</p>
      <button class="btn" id="retry-btn" type="button">Try again</button>
    </div></div>`;

  const skeleton = () => `
    <div class="loading-block">
      <div class="skeleton wide"></div>
      <div class="skeleton"></div>
      <div class="skeleton short"></div>
    </div>`;

  // ------------------------------------------------------------------- http
  async function api(path, { method = "GET", body } = {}) {
    const headers = {};
    if (method !== "GET" && method !== "HEAD") headers["X-Idlescan"] = "1";
    if (body !== undefined) headers["content-type"] = "application/json";
    const response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    let payload = null;
    try { payload = text ? JSON.parse(text) : null; } catch { payload = { error: text }; }
    if (!response.ok) {
      const error = new Error((payload && payload.error) || `${response.status} ${response.statusText}`);
      error.status = response.status;
      error.code = payload && payload.code;
      throw error;
    }
    return payload;
  }

  // ------------------------------------------------------------------ toast
  function toast(kind, title, body = "") {
    const root = $("#toasts");
    const node = document.createElement("div");
    node.className = `toast ${kind === "err" ? "err" : "ok"}`;
    node.innerHTML = `<div class="grow"><strong>${esc(title)}</strong>${body ? `<div class="t-body">${esc(body)}</div>` : ""}</div><button class="toast-x" type="button" aria-label="Dismiss notification">×</button>`;
    root.appendChild(node);
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      node.style.opacity = "0";
      node.style.transition = "opacity .2s";
      setTimeout(() => node.remove(), 210);
    };
    node.querySelector(".toast-x").addEventListener("click", close);
    setTimeout(close, kind === "err" ? 8000 : 4500);
  }

  async function copyText(text, label = "Copied to clipboard") {
    try {
      await navigator.clipboard.writeText(text);
      toast("ok", label);
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try { document.execCommand("copy"); toast("ok", label); }
      catch { toast("err", "Copy failed", "Select the text manually."); }
      area.remove();
    }
  }

  // ------------------------------------------------------------------ modal
  let modalKeyHandler = null;

  function focusable(container) {
    return $$(
      'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      container,
    ).filter((node) => node.offsetParent !== null);
  }

  function trapTab(event, container) {
    if (event.key !== "Tab") return false;
    const items = focusable(container);
    if (!items.length) return false;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
      return true;
    }
    if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
      return true;
    }
    return false;
  }

  function openModal({ title, subtitle = "", body, footer = "", wide = false }) {
    const root = $("#modal-root");
    const previousFocus = document.activeElement;
    root.innerHTML = `
      <div class="modal-backdrop" data-modal-backdrop>
        <div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
          <div class="modal-head">
            <div>
              <h2>${esc(title)}</h2>
              ${subtitle ? `<p>${esc(subtitle)}</p>` : ""}
            </div>
            <button class="modal-close" type="button" aria-label="Close">×</button>
          </div>
          <div class="modal-body">${body}</div>
          ${footer ? `<div class="modal-foot">${footer}</div>` : ""}
        </div>
      </div>`;
    document.body.style.overflow = "hidden";

    const close = () => {
      root.innerHTML = "";
      document.body.style.overflow = "";
      if (modalKeyHandler) {
        document.removeEventListener("keydown", modalKeyHandler);
        modalKeyHandler = null;
      }
      if (previousFocus && typeof previousFocus.focus === "function") previousFocus.focus();
    };
    modalKeyHandler = (event) => {
      if (event.key === "Escape") { close(); return; }
      trapTab(event, $(".modal", root));
    };
    document.addEventListener("keydown", modalKeyHandler);
    $(".modal-close", root).addEventListener("click", close);
    $("[data-modal-backdrop]", root).addEventListener("mousedown", (event) => {
      if (event.target === event.currentTarget) close();
    });
    const firstField = $(".modal-body input, .modal-body textarea, .modal-body select", root);
    if (firstField) firstField.focus();
    else $(".modal-close", root).focus();
    return { root, close };
  }

  // --------------------------------------------------------------- palette
  const PALETTE_NAV = [
    { href: "#/overview", title: "Overview", sub: "Portfolio pulse, action queue and analytics", kind: "Go" },
    { href: "#/check", title: "Check an address", sub: "Read-only eligibility check for any wallet", kind: "Go" },
    { href: "#/drops", title: "Airdrop registry", sub: "Tracked drops, phases and safety scores", kind: "Go" },
    { href: "#/claims", title: "Claims planner", sub: "Net-after-gas queue and proof status", kind: "Go" },
    { href: "#/wallets", title: "Wallets", sub: "Watch-only addresses, idle balances and positions", kind: "Go" },
    { href: "#/alerts", title: "Alerts", sub: "Deadlines, failed proofs and profitability changes", kind: "Go" },
    { href: "#/activity", title: "Activity & audit", sub: "Snapshots, jobs, executions and the audit trail", kind: "Go" },
    { href: "#/settings", title: "Settings", sub: "Read-only toggle, exports and maintenance", kind: "Go" },
  ];

  let paletteItems = [];
  let paletteIndex = 0;
  let paletteTimer = null;
  let palettePreviousFocus = null;

  function closePalette() {
    const root = $("#palette-root");
    if (root) root.innerHTML = "";
    document.body.classList.remove("palette-open");
    if (paletteTimer) { clearTimeout(paletteTimer); paletteTimer = null; }
    if (palettePreviousFocus && typeof palettePreviousFocus.focus === "function") {
      palettePreviousFocus.focus();
    }
    palettePreviousFocus = null;
  }

  function paletteNavigate(href) {
    closePalette();
    if (!href) return;
    if (location.hash === href) renderRoute();
    else location.hash = href;
  }

  function paintPalette(items, note = "") {
    paletteItems = items;
    paletteIndex = Math.min(Math.max(paletteIndex, 0), Math.max(items.length - 1, 0));
    const host = $("#palette-results");
    if (!host) return;
    if (!items.length) {
      host.innerHTML = `<div class="palette-empty">${note || "No matches. Try a drop id, token name, label, tag or address fragment."}</div>`;
      return;
    }
    host.innerHTML = items.map((item, index) => `
      <button class="palette-item ${index === paletteIndex ? "is-active" : ""}" data-palette-index="${index}" type="button">
        <span class="palette-kind">${esc(item.kind || "·")}</span>
        <span class="palette-main">
          <span class="palette-title">${esc(item.title)}</span>
          <span class="palette-sub">${esc(item.sub || "")}</span>
        </span>
        ${item.meta ? `<span class="palette-meta">${esc(item.meta)}</span>` : ""}
        <span class="palette-enter" aria-hidden="true">↵</span>
      </button>`).join("");
    $$("[data-palette-index]", host).forEach((button) => {
      button.addEventListener("mousemove", () => {
        const index = Number(button.dataset.paletteIndex);
        if (index === paletteIndex) return;
        paletteIndex = index;
        $$(".palette-item", host).forEach((node) => node.classList.toggle("is-active", node === button));
      });
      button.addEventListener("click", () => paletteNavigate(paletteItems[Number(button.dataset.paletteIndex)]?.href));
    });
  }

  function paletteMove(step) {
    if (!paletteItems.length) return;
    paletteIndex = (paletteIndex + step + paletteItems.length) % paletteItems.length;
    paintPalette(paletteItems);
    const active = $(".palette-item.is-active");
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  function paletteStatic(query) {
    const needle = query.trim().toLowerCase();
    return PALETTE_NAV.filter((item) => !needle || `${item.title} ${item.sub}`.toLowerCase().includes(needle))
      .map((item) => ({ ...item }));
  }

  function openPalette(seed = "") {
    const restore = document.activeElement;
    if ($("#palette-root").children.length) closePalette();
    palettePreviousFocus = restore;
    const root = $("#palette-root");
    root.innerHTML = `
      <div class="palette-backdrop" data-palette-backdrop>
        <div class="palette" role="dialog" aria-modal="true" aria-label="Command palette">
          <div class="palette-bar">
            <span class="search-glyph" aria-hidden="true">⌕</span>
            <input id="palette-input" type="text" placeholder="Search airdrops, wallets, transactions…" autocomplete="off" spellcheck="false" aria-controls="palette-results" aria-autocomplete="list">
            <kbd>esc</kbd>
          </div>
          <div class="palette-results" id="palette-results"></div>
          <div class="palette-foot">
            <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
            <span><kbd>↵</kbd> open</span>
            <span><kbd>⌘K</kbd> toggle</span>
          </div>
        </div>
      </div>`;
    document.body.classList.add("palette-open");
    const input = $("#palette-input", root);
    paletteIndex = 0;
    paintPalette(paletteStatic(""));

    const search = async (query) => {
      const needle = query.trim();
      if (needle.length < 2) {
        paintPalette(paletteStatic(needle));
        return;
      }
      paintPalette([], "Searching…");
      try {
        const data = await api(`/api/search?q=${encodeURIComponent(needle)}&limit=6`);
        const items = [];
        (data.drops || []).forEach((drop) => items.push({
          href: `#/drop/${encodeURIComponent(drop.id)}`,
          title: drop.name,
          sub: `${drop.chain} · ${drop.phase} · ${drop.unclaimed} unclaimed`,
          meta: usd(drop.net_usd),
          kind: "Drop",
        }));
        (data.wallets || []).forEach((wallet) => items.push({
          href: `#/wallet/${encodeURIComponent(wallet.address)}`,
          title: wallet.label || short(wallet.address, 8),
          sub: `${short(wallet.address, 8)}${wallet.tags ? ` · ${wallet.tags}` : ""}`,
          meta: usd((wallet.idle_usd || 0) + (wallet.unclaimed_usd || 0)),
          kind: "Wallet",
        }));
        (data.transactions || []).forEach((tx) => items.push({
          href: "#/activity",
          title: short(tx.tx_hash, 10),
          sub: `${tx.drop_id} · ${tx.status}`,
          kind: "Tx",
        }));
        paintPalette(items, "No match. Try a drop id, token name, wallet label, tag or address fragment.");
      } catch (err) {
        paintPalette([], err.message);
      }
    };

    input.addEventListener("input", () => {
      if (paletteTimer) clearTimeout(paletteTimer);
      const value = input.value;
      paletteTimer = setTimeout(() => search(value), 160);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Tab") { trapTab(event, $(".palette", root)); return; }
      if (event.key === "ArrowDown") { event.preventDefault(); paletteMove(1); }
      else if (event.key === "ArrowUp") { event.preventDefault(); paletteMove(-1); }
      else if (event.key === "Enter") {
        event.preventDefault();
        const item = paletteItems[paletteIndex];
        if (item) paletteNavigate(item.href);
      } else if (event.key === "Escape") { event.preventDefault(); closePalette(); }
    });
    $("[data-palette-backdrop]", root).addEventListener("mousedown", (event) => {
      if (event.target === event.currentTarget) closePalette();
    });
    input.value = seed;
    input.focus();
    if (seed.trim().length >= 2) search(seed);
  }

  function installShortcuts() {
    document.addEventListener("keydown", (event) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((event.target && event.target.tagName) || "");
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if ($("#palette-root").children.length) closePalette();
        else openPalette();
        return;
      }
      if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        openPalette();
        return;
      }
      if (event.key === "Escape" && $("#palette-root").children.length) {
        closePalette();
      }
    });
  }

  // ------------------------------------------------------------- app state
  const DISMISSED_KEY = "idlescan.dismissed";

  function loadDismissed() {
    try {
      const raw = sessionStorage.getItem(DISMISSED_KEY);
      return new Set(raw ? JSON.parse(raw) : []);
    } catch {
      return new Set();
    }
  }

  function saveDismissed(set) {
    try {
      sessionStorage.setItem(DISMISSED_KEY, JSON.stringify([...set]));
    } catch {
      // Private mode or storage disabled: dismissal still works for this page.
    }
  }

  const state = {
    config: null,
    stats: null,
    route: "overview",
    portfolio: null,
    scanTimer: null,
    dismissed: loadDismissed(),
  };

  const filters = {
    claimStatus: "actionable",
    claimWallet: "",
    hideBelowGas: false,
    alertsAll: false,
    dropQuery: "",
    dropPhase: "all",
    dropSort: "net",
  };

  // --------------------------------------------------------------- chrome
  function updateChrome() {
    const config = state.config;
    const stats = state.stats;
    if (!config) return;
    const pill = $("#mode-pill");
    pill.textContent = config.read_only ? "read-only" : "write mode";
    pill.className = `mode-pill ${config.read_only ? "" : "mode-write"} ${config.demo ? "is-demo" : ""}`;
    pill.title = config.read_only
      ? "Claim hand-off endpoints are locked. Toggle IDLESCAN_READ_ONLY in .env."
      : "Claim hand-off is enabled: the server can return unsigned transactions for your wallet to sign.";

    const count = $("#alert-count");
    const unacked = stats ? stats.unacked_alerts : 0;
    count.hidden = !unacked;
    count.textContent = unacked > 99 ? "99+" : String(unacked);

    const meta = $("#footer-meta");
    if (meta) {
      meta.textContent = stats && stats.snapshot
        ? `v${config.version} · updated ${timeAgo(stats.snapshot.created_at)}`
        : `v${config.version} · no scans yet`;
    }
    renderBanners();
  }

  function renderBanners() {
    const holder = $("#banners");
    if (!state.config) { holder.innerHTML = ""; return; }
    const config = state.config;
    const items = [];

    if (config.insecure_remote) {
      items.push({
        kind: "insecure-remote", cls: "banner-danger", icon: "!",
        text: `<strong>Insecure remote bind.</strong> The server is listening on <code>${esc(config.host)}</code> without an auth token. Anyone who can reach this address can read your database and use the API. Bind to 127.0.0.1 or set <code>IDLESCAN_AUTH_TOKEN</code>.`,
      });
    }
    if (config.demo && !state.dismissed.has("demo")) {
      items.push({
        kind: "demo", cls: "banner-demo", icon: "◈",
        text: `<strong>Demo data.</strong> This instance shows a synthetic portfolio so every screen is populated. Restart without <code>IDLESCAN_DEMO=true</code> to use your own wallets.`,
      });
    }
    if (!config.read_only && !state.dismissed.has("write")) {
      items.push({
        kind: "write", cls: "banner-write", icon: "!",
        text: `<strong>Write mode is on.</strong> The server can now return unsigned claim transactions. It still never holds keys, signs, or broadcasts — your wallet does that. <a href="#/settings" style="color:inherit;text-decoration:underline">Review the security model</a>.`,
      });
    }
    const snapshot = state.stats && state.stats.snapshot;
    if (snapshot) {
      const ageHours = (Date.now() - Date.parse(snapshot.created_at)) / 3_600_000;
      const budget = config.refresh_minutes ? Math.max(config.refresh_minutes / 30, 2) : 24;
      if (Number.isFinite(ageHours) && ageHours > budget && !state.dismissed.has("stale")) {
        items.push({
          kind: "stale", cls: "banner-warn", icon: "⟳",
          text: `<strong>Snapshot ${timeAgo(snapshot.created_at)}.</strong> Balances and claim states may have moved. Run a scan before acting on these numbers.`,
        });
      }
    }
    if (config.read_only && (state.route === "claims" || state.route === "settings") && !state.dismissed.has("read")) {
      items.push({
        kind: "read", cls: "banner-read", icon: "◇",
        text: `<strong>Read-only mode.</strong> Claim preparation is locked. Set <code>IDLESCAN_READ_ONLY=false</code> in your <code>.env</code> and restart to unlock unsigned claim hand-off.`,
      });
    }
    for (const [index, warning] of (config.warnings || []).entries()) {
      if (config.read_only || warning.startsWith("WRITE MODE:")) continue;
      items.push({
        kind: `warn-${index}`, cls: "banner-warn", icon: "!",
        text: esc(warning),
      });
    }
    holder.innerHTML = items.map((item) => `
      <div class="banner ${item.cls}" data-banner="${esc(item.kind)}">
        <span aria-hidden="true">${item.icon}</span>
        <span class="grow">${item.text}</span>
        <button class="banner-x" type="button" data-dismiss="${esc(item.kind)}" aria-label="Dismiss">×</button>
      </div>`).join("");
    $$("[data-dismiss]").forEach((button) => {
      button.addEventListener("click", () => {
        state.dismissed.add(button.dataset.dismiss);
        saveDismissed(state.dismissed);
        renderBanners();
      });
    });
  }

  // ------------------------------------------------------------------ scan
  function setScanBusy(busy, label = "Scan now") {
    const button = $("#scan-btn");
    if (!button) return;
    button.disabled = busy;
    $(".spinner", button).hidden = !busy;
    $(".scan-label", button).textContent = label;
  }

  function stopPolling() {
    if (state.scanTimer) { clearInterval(state.scanTimer); state.scanTimer = null; }
  }

  function pollJob(jobId) {
    stopPolling();
    setScanBusy(true, "Scanning…");
    const tick = async () => {
      let job;
      try { job = (await api(`/api/jobs/${encodeURIComponent(jobId)}`)).job; }
      catch (err) { stopPolling(); setScanBusy(false); toast("err", "Lost scan", err.message); return; }
      if (!job) return;
      const pct = Math.round((job.progress || 0) * 100);
      setScanBusy(true, job.status === "queued" ? "Queued…" : `Scanning ${pct}%`);
      if (job.status === "done") {
        stopPolling();
        setScanBusy(false);
        const result = job.result || {};
        toast("ok", "Scan complete", `${result.holdings ?? 0} balances · ${result.claims ?? 0} claim positions · ${result.alerts ?? 0} new alerts`);
        state.portfolio = null;
        await refreshShell();
        await renderRoute();
      } else if (job.status === "failed") {
        stopPolling();
        setScanBusy(false);
        toast("err", "Scan failed", job.error || job.message || "unknown error");
      }
    };
    state.scanTimer = setInterval(tick, 1100);
    tick();
  }

  async function startScan() {
    setScanBusy(true, "Starting…");
    try {
      const payload = await api("/api/scan", { method: "POST", body: {} });
      toast("ok", "Scan started", "Reading balances and claim state across the configured chains.");
      pollJob(payload.job.id);
    } catch (err) {
      setScanBusy(false);
      if (err.status === 409) toast("err", "A scan is already running", "Wait for it to finish, then try again.");
      else toast("err", "Could not start scan", err.message);
    }
  }

  async function refreshShell() {
    try {
      const [config, stats] = await Promise.all([api("/api/config"), api("/api/stats")]);
      state.config = config;
      state.stats = stats;
      updateChrome();
    } catch { /* views surface their own errors */ }
  }

  // Tables that overflow get a soft edge fade so the affordance is obvious on
  // touch screens, and the fade clears once the user reaches the last column.
  function markScrollables(root = app) {
    $$(".table-wrap", root).forEach((wrap) => {
      if (wrap.dataset.scrollBound === "1") return;
      wrap.dataset.scrollBound = "1";
      const update = () => {
        const scrollable = wrap.scrollWidth > wrap.clientWidth + 1;
        wrap.classList.toggle("is-scrollable", scrollable);
        wrap.classList.toggle("is-at-end", scrollable && wrap.scrollLeft + wrap.clientWidth >= wrap.scrollWidth - 2);
      };
      wrap.addEventListener("scroll", update, { passive: true });
      wrap._updateScrollState = update;
      update();
    });
  }

  function refreshScrollStates() {
    $$(".table-wrap").forEach((wrap) => {
      if (wrap._updateScrollState) wrap._updateScrollState();
      else markScrollables(wrap);
    });
  }

  // --------------------------------------------------------------- overview
  async function viewOverview(root) {
    const [stats, portfolio, airdrops, alerts, analytics] = await Promise.all([
      api("/api/stats"),
      api("/api/portfolio"),
      api("/api/airdrops"),
      api("/api/alerts"),
      api("/api/analytics"),
    ]);
    state.stats = stats;
    state.portfolio = portfolio;
    updateChrome();

    const snapshot = portfolio.snapshot;
    const wallets = portfolio.wallets || [];
    const claims = portfolio.claims || [];
    const holdings = portfolio.holdings || [];
    const write = state.config.write_enabled;

    if (!snapshot) {
      root.innerHTML = `
        <div class="hero">
          <div>
            <div class="eyebrow">Airdrop research desk</div>
            <h1 class="hero-title">Find what you're owed<br><em>before it expires.</em></h1>
            <p class="hero-copy">Track idle balances across ${state.config.chains.length} EVM chains, surface unclaimed allocations, and price every claim against its gas cost. Every proof is verified against the on-chain merkle root.</p>
            <div class="hero-actions">
              ${wallets.length
                ? `<button class="btn btn-primary" id="hero-scan" type="button">Run first scan</button>`
                : `<a class="btn btn-primary" href="#/wallets">Add your first wallet</a>`}
              <a class="btn" href="#/check">Check any address</a>
              <a class="btn btn-ghost" href="#/drops">Browse airdrops</a>
            </div>
          </div>
          <div class="hero-totals">
            <div class="mini-metric"><span>Wallets tracked</span><strong>${wallets.length}</strong></div>
            <div class="mini-metric"><span>Airdrops in registry</span><strong>${airdrops.airdrops.length}</strong></div>
            <div class="hero-meta"><span>No snapshot yet</span><span class="dot"></span><span>read-only RPC calls · no keys, no signing</span></div>
          </div>
        </div>
        ${wallets.length ? "" : `
        <div class="panel mt-lg"><div class="panel-body">
          ${emptyState("＋", "Start with a public address", "Add a wallet you control — or one you are researching. Addresses are public data; never paste a seed phrase or private key.", `<div class="toolbar"><a class="btn btn-primary" href="#/wallets">Add a wallet</a><a class="btn" href="#/check">Check an address first</a></div>`)}
        </div></div>`}`;
      const button = $("#hero-scan", root);
      if (button) button.addEventListener("click", startScan);
      return;
    }

    const actionable = claims.filter((c) => c.status === "unclaimed" && !c.expired);
    const recoverable = actionable.reduce((sum, c) => sum + (c.net_usd ?? 0), 0);
    const gross = actionable.reduce((sum, c) => sum + (c.usd_value ?? 0), 0);
    const gas = actionable.reduce((sum, c) => sum + (c.gas_usd ?? 0), 0);
    const expiring = actionable.filter((c) => {
      const days = daysLeft(c.deadline);
      return days !== null && days >= 0 && days <= 14;
    });
    const belowGas = actionable.filter((c) => c.net_usd !== null && c.net_usd <= 0);
    const profitable = actionable.filter((c) => c.net_usd !== null && c.net_usd > 0).length;
    const unpriced = actionable.filter((c) => c.net_usd === null).length;
    const idleUsd = snapshot.totals.idle_usd;
    const queue = [...actionable].sort((a, b) => (b.net_usd ?? 0) - (a.net_usd ?? 0)).slice(0, 6);
    const watch = [...airdrops.airdrops].filter((a) => a.enabled).sort((a, b) => b.risk.score - a.risk.score).slice(0, 4);
    const alertList = alerts.alerts.slice(0, 3);

    root.innerHTML = `
      <div class="hero">
        <div>
          <div class="eyebrow">Recoverable after gas · snapshot #${snapshot.id}</div>
          <div class="hero-money">${usd(recoverable)} <small>net</small></div>
          <p class="hero-copy">${actionable.length} unclaimed allocation${actionable.length === 1 ? "" : "s"} across ${new Set(actionable.map((c) => c.chain)).size} chain${new Set(actionable.map((c) => c.chain)).size === 1 ? "" : "s"}, priced at current market rates. ${expiring.length ? `<strong style="color:#fecdd3">${expiring.length} expiring within 14 days.</strong>` : "No deadlines inside 14 days."}</p>
          <div class="hero-actions">
            <a class="btn btn-primary" href="#/claims">Open claims planner</a>
            <button class="btn" id="hero-scan" type="button">Rescan portfolio</button>
            <a class="btn btn-ghost" href="#/check">Check an address</a>
            ${write ? `<span class="chip" style="align-self:center">write mode · unsigned tx hand-off</span>` : `<a class="btn btn-ghost" href="#/settings">Enable write mode</a>`}
          </div>
          <div class="hero-meta" style="margin-top:14px">
            <span>snapshot ${timeAgo(snapshot.created_at)}</span><span class="dot"></span>
            <span>${wallets.length} wallet${wallets.length === 1 ? "" : "s"}</span><span class="dot"></span>
            <span>${holdings.length} balance line${holdings.length === 1 ? "" : "s"}</span>
          </div>
        </div>
        <div class="hero-totals">
          <div class="hero-split">
            <div class="mini-metric"><span>Idle balances</span><strong>${usd(idleUsd)}</strong></div>
            <div class="mini-metric"><span>Unclaimed gross</span><strong>${usd(gross)}</strong></div>
            <div class="mini-metric"><span>Gas to claim all</span><strong>${usd(gas)}</strong></div>
            <div class="mini-metric"><span>Below gas</span><strong style="color:${belowGas.length ? "var(--rose)" : "var(--text)"}">${belowGas.length}</strong></div>
          </div>
          <div class="hero-meta"><span>price source: DefiLlama</span><span class="dot"></span><span>proof checks on-chain</span></div>
        </div>
      </div>

      <div class="grid grid-stats mt">
        <div class="stat" style="--stat-accent:var(--accent)">
          <span class="stat-label">Recoverable net</span>
          <span class="stat-value stat-money">${usd(recoverable)}</span>
          <span class="stat-sub">${profitable} profitable position${profitable === 1 ? "" : "s"}${unpriced ? ` · ${unpriced} unpriced` : ""}</span>
        </div>
        <div class="stat" style="--stat-accent:var(--sky)">
          <span class="stat-label">Unclaimed gross</span>
          <span class="stat-value">${usd(gross)}</span>
          <span class="stat-sub">before gas · ${actionable.length} allocation${actionable.length === 1 ? "" : "s"}</span>
        </div>
        <div class="stat" style="--stat-accent:var(--amber)">
          <span class="stat-label">Deadline watch</span>
          <span class="stat-value stat-amber">${expiring.length}</span>
          <span class="stat-sub">expiring within 14 days</span>
        </div>
        <div class="stat" style="--stat-accent:var(--teal)">
          <span class="stat-label">Open alerts</span>
          <span class="stat-value">${stats.unacked_alerts}</span>
          <span class="stat-sub">${belowGas.length} below gas · ${alerts.alerts.filter((a) => a.severity === "critical").length} critical</span>
        </div>
      </div>

      <div class="grid grid-2 grid-2-top mt">
        <section class="panel">
          <div class="panel-head">
            <div>
              <h2>Action queue</h2>
              <p class="panel-note">Highest net value first. Gas-adjusted, not hype-adjusted.</p>
            </div>
            <a class="btn btn-sm btn-ghost" href="#/claims">All positions →</a>
          </div>
          <div class="panel-body flush">
            ${queue.length ? `
            <div class="table-wrap"><table>
              <thead><tr><th>Drop</th><th>Wallet</th><th class="num">Value</th><th class="num">Net</th><th>Deadline</th><th></th></tr></thead>
              <tbody>
                ${queue.map((c) => `
                  <tr>
                    <td><a class="strong link" href="#/drop/${encodeURIComponent(c.drop_id)}">${esc(c.name)}</a><div class="tiny dim">${esc(c.chain)} · ${formatUnits(c.amount_raw, c.decimals, 2)} tokens</div></td>
                    <td><a class="link" href="#/wallet/${encodeURIComponent(c.wallet)}">${walletLabel(c)}</a><div class="addr">${esc(short(c.wallet))}</div></td>
                    <td class="num">${usd(c.usd_value)}</td>
                    <td class="num ${c.net_usd === null ? "" : c.net_usd <= 0 ? "neg" : "pos"}">${usd(c.net_usd)}</td>
                    <td>${deadlineChip(c.deadline)}</td>
                    <td class="num">${write
                      ? `<button class="btn btn-sm btn-primary" data-prepare="${esc(c.drop_id)}" data-wallet="${esc(c.wallet)}" type="button">Prepare</button>`
                      : `<span class="badge badge-source">locked</span>`}</td>
                  </tr>`).join("")}
              </tbody>
            </table></div>` : emptyState("✓", "Nothing actionable", "There are no unclaimed, unexpired allocations in the latest snapshot. Add more wallets or rescan.")}
          </div>
        </section>

        <div class="stack">
          <section class="panel">
            <div class="panel-head">
              <h2>Alert feed</h2>
              <a class="btn btn-sm btn-ghost" href="#/alerts">All alerts</a>
            </div>
            <div class="panel-body">
              ${alertList.length ? `<div class="alert-list">${alertList.map((alert) => `
                <div class="alert-item is-${esc(alert.severity)}">
                  <div class="alert-main">
                    <div class="alert-title">${esc(alert.title)}</div>
                    <div class="alert-body">${esc(alert.body)}</div>
                    <div class="alert-meta">${statusBadge(alert.severity)}<span>${timeAgo(alert.created_at)}</span></div>
                  </div>
                </div>`).join("")}</div>`
                : emptyState("◇", "No open alerts", "Alerts appear when a deadline nears, a claim becomes unprofitable, a proof fails, or a new allocation is detected.")}
            </div>
          </section>

          <section class="panel">
            <div class="panel-head">
              <h2>Watchlist</h2>
              <a class="btn btn-sm btn-ghost" href="#/drops">Registry</a>
            </div>
            <div class="panel-body">
              ${watch.length ? `<div class="feed">${watch.map((drop) => `
                <div class="feed-item">
                  <div class="feed-icon">${drop.risk.score >= 70 ? "◎" : "?"}</div>
                  <div class="feed-main">
                    <div class="feed-title">${esc(drop.name)}</div>
                    <div class="feed-sub">${esc(drop.chain)} · ${drop.unclaimed} unclaimed · ${usd(drop.claimable_net_usd)} net</div>
                    <div class="reasons" style="margin-top:4px">${(drop.risk.reasons || []).slice(0, 2).map((reason) => `<span class="reason ${/MISMATCH|no |errored|missing/i.test(reason) ? "bad" : ""}">${esc(reason)}</span>`).join("")}</div>
                  </div>
                  <div class="feed-time">${riskBadge(drop.risk)}</div>
                </div>`).join("")}</div>`
                : emptyState("◇", "No tracked airdrops", "Add or enable an airdrop to start watching allocations.")}
            </div>
          </section>
        </div>
      </div>

      ${analyticsPanel(analytics)}

      <section class="panel mt">
        <div class="panel-head">
          <div><h2>Recent snapshots</h2><p class="panel-note">Every scan is stored locally so you can see what changed and prove what you claimed.</p></div>
          <button class="btn btn-sm" id="hero-scan-2" type="button">Scan now</button>
        </div>
        <div class="panel-body flush">
          <div class="table-wrap"><table>
            <thead><tr><th>Snapshot</th><th>When</th><th>Trigger</th><th class="num">Idle</th><th class="num">Unclaimed</th><th class="num">Combined</th></tr></thead>
            <tbody id="recent-snapshots"><tr><td colspan="6" class="muted small">loading…</td></tr></tbody>
          </table></div>
        </div>
      </section>`;

    ["#hero-scan", "#hero-scan-2"].forEach((sel) => {
      const button = $(sel, root);
      if (button) button.addEventListener("click", startScan);
    });
    bindPrepareButtons(root);
    loadRecentSnapshots();
    $$("[data-diff]", root).forEach((button) => {
      button.addEventListener("click", () => openDiff(button.dataset.diff === "latest" ? null : button.dataset.diff));
    });
  }

  function analyticsPanel(data) {
    const history = data.history || [];
    const combined = history.map((row) => row.combined_usd || 0);
    const delta = combined.length > 1 ? combined[combined.length - 1] - combined[combined.length - 2] : null;
    const diff = data.diff;
    const rates = data.totals || {};
    const coverage = data.coverage || {};
    const deadlines = data.deadlines || [];
    return `
      <section class="panel mt">
        <div class="panel-head">
          <div>
            <h2>Portfolio pulse</h2>
            <p class="panel-note">Every scan is an immutable snapshot. Trends, coverage and diffs are computed locally from that history.</p>
          </div>
          <div class="toolbar">
            <a class="btn btn-sm" href="/api/calendar.ics" download>Deadlines .ics</a>
            <button class="btn btn-sm btn-ghost" data-diff="latest" type="button" ${diff && diff.previous ? "" : "disabled"}>Latest diff</button>
          </div>
        </div>
        <div class="panel-body">
          <div class="pulse-grid">
            <div class="pulse-chart">
              <div class="row-between">
                <span class="eyebrow">Combined idle + unclaimed</span>
                <span class="tiny ${delta === null ? "dim" : delta >= 0 ? "pos" : "neg"}">${delta === null ? "needs two snapshots" : `${delta >= 0 ? "+" : ""}${usd(delta)} since last scan`}</span>
              </div>
              ${sparkline(combined, { label: "Combined portfolio value" })}
              <div class="spark-scale"><span>${usd(Math.min(...combined, 0))}</span><span>${usd(Math.max(...combined, 0))}</span><span>${history.length} snapshot${history.length === 1 ? "" : "s"}</span></div>
            </div>
            <div>
              <span class="eyebrow">Idle balances by chain</span>
              ${barBreakdown(data.chain_breakdown, { empty: "No idle balances in the latest snapshot." })}
            </div>
            <div>
              <span class="eyebrow">Claim status mix</span>
              <div class="status-mix">${(data.status_breakdown || []).map((item) => `
                <div class="status-mix-row">
                  ${statusBadge(item.status)}
                  <span class="grow dim">${item.count} position${item.count === 1 ? "" : "s"}</span>
                  <span class="mono">${usd(item.usd_value)}</span>
                </div>`).join("") || `<p class="muted small">No claim positions yet.</p>`}</div>
              <div class="divider"></div>
              <dl class="kv kv-tight">
                <dt>Recovered</dt><dd>${rates.claimed_usd ? usd(rates.claimed_usd) : "—"}${rates.recovery_rate !== null && rates.recovery_rate !== undefined ? ` · ${Math.round(rates.recovery_rate * 100)}%` : ""}</dd>
                <dt>Coverage</dt><dd>${coverage.with_positions ?? 0}/${coverage.wallets ?? 0} wallets${coverage.ratio ? ` · ${Math.round(coverage.ratio * 100)}%` : ""}</dd>
                <dt>Positions</dt><dd>${rates.positions ?? 0} across ${data.chain_breakdown.length} chain${data.chain_breakdown.length === 1 ? "" : "s"}</dd>
              </dl>
            </div>
          </div>

          <div class="divider"></div>

          <div class="pulse-grid pulse-bottom">
            <div>
              <span class="eyebrow">Deadline radar</span>
              ${deadlines.length ? `<div class="deadline-list">${deadlines.map((drop) => `
                <a class="deadline-row" href="#/drop/${encodeURIComponent(drop.id)}">
                  <span class="deadline-main"><strong>${esc(drop.name)}</strong><span class="tiny dim">${esc(drop.chain)} · ${drop.unclaimed} unclaimed · ${usd(drop.claimable_net_usd)} net</span></span>
                  ${deadlineChip(drop.deadline)}
                </a>`).join("")}</div>` : `<p class="muted small">No documented deadlines in the registry.</p>`}
            </div>
            <div>
              <span class="eyebrow">Since previous snapshot</span>
              ${diff && diff.previous ? `
                <p class="small muted">#${diff.snapshot.id} compared with #${diff.previous.id} · ${timeAgo(diff.snapshot.created_at)}</p>
                <div class="diff-summary">
                  ${Object.entries(diff.counts || {}).map(([kind, count]) => `<span class="diff-chip kind-${esc(kind)}">${count} ${esc(kind)}</span>`).join("") || `<span class="dim small">No position-level changes.</span>`}
                </div>
                ${diff.delta ? `<dl class="kv kv-tight mt-sm">
                  <dt>Idle</dt><dd class="${diff.delta.idle_usd >= 0 ? "pos" : "neg"}">${diff.delta.idle_usd >= 0 ? "+" : ""}${usd(diff.delta.idle_usd)}</dd>
                  <dt>Unclaimed</dt><dd class="${diff.delta.unclaimed_usd >= 0 ? "pos" : "neg"}">${diff.delta.unclaimed_usd >= 0 ? "+" : ""}${usd(diff.delta.unclaimed_usd)}</dd>
                </dl>` : ""}
                <button class="btn btn-sm btn-ghost mt-sm" data-diff="latest" type="button">Open full diff</button>`
                : `<p class="muted small">Run another scan to unlock snapshot diffs — new, claimed, expired, proof and value changes.</p>`}
            </div>
          </div>
        </div>
      </section>`;
  }

  async function loadRecentSnapshots() {
    const body = $("#recent-snapshots");
    if (!body) return;
    try {
      const { snapshots } = await api("/api/snapshots");
      if (!snapshots.length) {
        body.innerHTML = `<tr><td colspan="6" class="muted small">No snapshots yet.</td></tr>`;
        return;
      }
      body.innerHTML = snapshots.slice(0, 5).map((row) => `
        <tr>
          <td class="mono">#${row.id}</td>
          <td>${esc(timeAgo(row.created_at))}</td>
          <td><span class="tag">${esc(row.trigger)}</span></td>
          <td class="num">${usd(row.totals.idle_usd)}</td>
          <td class="num">${usd(row.totals.unclaimed_usd)}</td>
          <td class="num strong">${usd(row.totals.combined_usd)}</td>
        </tr>`).join("");
    } catch (err) {
      body.innerHTML = `<tr><td colspan="6" class="muted small">${esc(err.message)}</td></tr>`;
    }
  }

  // ------------------------------------------------------------------ drops
  async function viewDrops(root) {
    const [{ airdrops }, portfolio] = await Promise.all([api("/api/airdrops"), api("/api/portfolio")]);
    state.portfolio = portfolio;
    const claims = portfolio.claims || [];

    root.innerHTML = `
      <div class="page-head">
        <div>
          <h1>Airdrop registry</h1>
          <p class="page-sub">Every entry must declare <strong>how eligibility is discovered</strong> and <strong>how claimed state is read</strong>. Risk scores come from verifiable signals only — eligibility source, proof verification, documented links and deadlines.</p>
        </div>
        <div class="page-actions">
          <input type="text" id="drop-search" placeholder="Filter by name, chain, id…" style="width:200px" value="${esc(filters.dropQuery)}">
          <select id="drop-phase" style="width:145px" aria-label="Filter by lifecycle phase">
            ${[["all", "All phases"], ["claimable", "Claimable"], ["closing", "Closing soon"], ["expired", "Expired"], ["completed", "Completed"], ["no allocation", "No allocation"], ["tracking", "Tracking only"]]
              .map(([value, label]) => `<option value="${esc(value)}" ${filters.dropPhase === value ? "selected" : ""}>${esc(label)}</option>`).join("")}
          </select>
          <select id="drop-sort" style="width:150px" aria-label="Sort airdrops">
            ${[["net", "Sort: net value"], ["deadline", "Sort: deadline"], ["safety", "Sort: safety"], ["name", "Sort: name"]]
              .map(([value, label]) => `<option value="${esc(value)}" ${filters.dropSort === value ? "selected" : ""}>${esc(label)}</option>`).join("")}
          </select>
          <button class="btn btn-primary" id="add-drop" type="button">+ Add airdrop</button>
        </div>
      </div>
      <div class="drop-grid" id="drop-grid">${dropCards(airdrops, claims)}</div>`;

    const redraw = () => {
      $("#drop-grid", root).innerHTML = dropCards(airdrops, claims);
      bindDropButtons(root, airdrops);
    };
    $("#drop-search", root).addEventListener("input", (event) => {
      filters.dropQuery = event.target.value.trim().toLowerCase();
      redraw();
    });
    $("#drop-phase", root).addEventListener("change", (event) => {
      filters.dropPhase = event.target.value;
      redraw();
    });
    $("#drop-sort", root).addEventListener("change", (event) => {
      filters.dropSort = event.target.value;
      redraw();
    });
    $("#add-drop", root).addEventListener("click", () => openAddDrop(airdrops));
    bindDropButtons(root, airdrops);
  }

  function dropCards(drops, claims) {
    const query = filters.dropQuery;
    let visible = drops.filter((drop) => {
      if (query && ![drop.name, drop.chain, drop.id, drop.distributor, drop.phase].join(" ").toLowerCase().includes(query)) return false;
      if (filters.dropPhase === "all") return true;
      return drop.phase === filters.dropPhase;
    });
    const sorters = {
      net: (a, b) => b.claimable_net_usd - a.claimable_net_usd,
      name: (a, b) => a.name.localeCompare(b.name),
      safety: (a, b) => b.risk.score - a.risk.score,
      deadline: (a, b) => {
        const da = a.days_left === null || a.days_left === undefined ? Infinity : a.days_left;
        const db = b.days_left === null || b.days_left === undefined ? Infinity : b.days_left;
        return da - db;
      },
    };
    visible = [...visible].sort(sorters[filters.dropSort] || sorters.net);
    if (!visible.length) {
      return `<div class="panel" style="grid-column:1/-1"><div class="panel-body">${emptyState("◇", "No airdrops match", "Adjust the filter or register a new drop. A drop without an eligibility source can be tracked, but its allocations cannot be discovered.")}</div></div>`;
    }
    return visible.map((drop) => {
      const links = Object.entries(drop.links || {})
        .map(([name, url]) => safeUrl(url) ? `<a href="${esc(safeUrl(url))}" target="_blank" rel="noopener noreferrer">${esc(name)}</a>` : "")
        .join("");
      const dataset = drop.has_dataset
        ? `<span class="badge badge-info">dataset</span>`
        : `<span class="badge badge-warning">no dataset</span>`;
      return `
      <article class="drop-card ${drop.enabled ? "" : "is-disabled"}">
        <div class="drop-card-top">
          <div>
            <a class="drop-title link" href="#/drop/${encodeURIComponent(drop.id)}">${esc(drop.name)}</a>
            <div class="drop-sub">
              <span class="tag">${esc(drop.chain)}</span>
              <span class="tag">${esc(drop.eligibility || "no eligibility source")}</span>
              ${dataset}
              ${phaseBadge(drop.phase)}
              ${drop.source === "custom" ? `<span class="tag">custom</span>` : drop.source === "demo" ? `<span class="tag">demo</span>` : `<span class="tag">bundled</span>`}
            </div>
          </div>
          ${riskBadge(drop.risk)}
        </div>
        <div class="drop-nums">
          <div class="drop-num"><span>Unclaimed</span><strong>${drop.unclaimed}<span class="dim" style="font-size:11px"> / ${drop.positions}</span></strong></div>
          <div class="drop-num"><span>Net after gas</span><strong class="${drop.claimable_net_usd > 0 ? "pos" : ""}">${usd(drop.claimable_net_usd)}</strong></div>
          <div class="drop-num"><span>Gross value</span><strong>${usd(drop.unclaimed_usd)}</strong></div>
          <div class="drop-num"><span>Claim deadline</span><strong style="font-size:12px">${deadlineChip(drop.deadline)}</strong></div>
        </div>
        <div class="reasons">${(drop.risk.reasons || []).map((reason) => `<span class="reason ${/MISMATCH|no |errored|missing|disabled/i.test(reason) ? "bad" : ""}">${esc(reason)}</span>`).join("")}</div>
        ${drop.notes ? `<p class="small muted" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${esc(drop.notes)}</p>` : ""}
        <div class="risk-line">${riskMeter(drop.risk)}<span class="dim">safety score</span></div>
        <div class="drop-foot">
          <div class="link-row">${links || `<span class="dim tiny">no links recorded</span>`}</div>
          <div class="toolbar">
            <a class="btn btn-sm btn-ghost" href="#/drop/${encodeURIComponent(drop.id)}">Profile →</a>
            <label class="switch" title="Track this airdrop in scans">
              <input type="checkbox" ${drop.enabled ? "checked" : ""} data-toggle-drop="${esc(drop.id)}">
              <span>track</span>
            </label>
            ${drop.source !== "registry"
              ? `<button class="btn btn-sm btn-danger" data-delete-drop="${esc(drop.id)}" type="button" title="Remove custom entry">Remove</button>`
              : ""}
          </div>
        </div>
      </article>`;
    }).join("");
  }

  function bindDropButtons(root, drops) {
    $$("[data-toggle-drop]", root).forEach((input) => {
      input.addEventListener("change", async () => {
        try {
          await api(`/api/airdrops/${encodeURIComponent(input.dataset.toggleDrop)}`, {
            method: "PATCH", body: { enabled: input.checked },
          });
          toast("ok", input.checked ? "Airdrop enabled" : "Airdrop disabled", "It will be included in the next scan.");
        } catch (err) {
          input.checked = !input.checked;
          toast("err", "Could not update airdrop", err.message);
        }
      });
    });
    $$("[data-delete-drop]", root).forEach((button) => {
      button.addEventListener("click", async () => {
        if (!confirm(`Remove ${button.dataset.deleteDrop} from the registry?`)) return;
        try {
          await api(`/api/airdrops/${encodeURIComponent(button.dataset.deleteDrop)}`, { method: "DELETE" });
          toast("ok", "Airdrop removed");
          await renderRoute();
        } catch (err) { toast("err", "Could not remove", err.message); }
      });
    });
  }

  function openAddDrop(drops) {
    const chains = (state.config?.chains || [])
      .map((chain) => `<option value="${esc(chain.key)}">${esc(chain.name)} (${esc(chain.key)})</option>`)
      .join("");
    const { root, close } = openModal({
      title: "Register an airdrop",
      subtitle: "Be explicit about where eligibility comes from. Without a source, allocations cannot be discovered.",
      wide: true,
      body: `
        <form id="add-drop-form" class="form-grid">
          <div class="field"><label for="d-id">ID *</label><input id="d-id" name="id" required pattern="[A-Za-z0-9._-]+" placeholder="project-season-2" value=""></div>
          <div class="field"><label for="d-name">Name *</label><input id="d-name" name="name" required placeholder="Project (TOKEN)"></div>
          <div class="field"><label for="d-chain">Chain *</label><select id="d-chain" name="chain">${chains}</select></div>
          <div class="field"><label for="d-distributor">Distributor contract *</label><input id="d-distributor" name="distributor" required placeholder="0x…" autocomplete="off" spellcheck="false"></div>
          <div class="field"><label for="d-token">Reward token</label><input id="d-token" name="token" placeholder="0x… (blank = native token)" autocomplete="off" spellcheck="false"></div>
          <div class="field"><label for="d-decimals">Token decimals</label><input id="d-decimals" name="token_decimals" type="number" min="0" max="36" value="18"></div>
          <div class="field"><label for="d-eligibility">Eligibility source</label>
            <select id="d-eligibility" name="eligibility">
              <option value="merkle_dataset">Merkle dataset (local JSON)</option>
              <option value="dataset_url">Dataset URL per address</option>
              <option value="allocation_view">On-chain allocation view</option>
              <option value="none">None — track only</option>
            </select>
          </div>
          <div class="field"><label for="d-dataset">Dataset path</label><input id="d-dataset" name="dataset" placeholder="data/datasets/project.claims.json"><span class="hint">Required for merkle datasets.</span></div>
          <div class="field full"><label for="d-url">Dataset URL template</label><input id="d-url" name="url" placeholder="https://api.example.com/claims/{address}.json"><span class="hint">Must contain <code>{address}</code>. Used for dataset_url sources.</span></div>
          <div class="field"><label for="d-allocation">Allocation view method</label><input id="d-allocation" name="allocation_method" value="allocation(address)"><span class="hint">For on-chain allocation_view sources.</span></div>
          <div class="field"><label for="d-claim">Claim method</label><input id="d-claim" name="claim_method" value="claim(uint256,address,uint256,bytes32[])"></div>
          <div class="field"><label for="d-claimed">Claimed check</label><input id="d-claimed" name="claimed_method" placeholder="auto from eligibility type"></div>
          <div class="field"><label for="d-deadline">Claim deadline</label><input id="d-deadline" name="claim_deadline" type="date"></div>
          <div class="field"><label for="d-website">Official website</label><input id="d-website" name="website" type="url" placeholder="https://…"></div>
          <div class="field"><label for="d-docs">Docs / claim page</label><input id="d-docs" name="docs" type="url" placeholder="https://…"></div>
          <div class="field full"><label for="d-notes">Notes</label><textarea id="d-notes" name="notes" rows="2" placeholder="How eligibility was verified, caveats, official claim instructions…"></textarea></div>
        </form>
        <div class="callout callout-info mt">
          <strong>Safety.</strong> Registering a drop only enables read-only probing of its contract. Cosigners, signatures and approvals are never requested. Verify the distributor address against the project's official docs before trusting any result.
        </div>`,
      footer: `<button class="btn btn-ghost" type="button" data-cancel>Cancel</button><button class="btn btn-primary" type="submit" form="add-drop-form">Register airdrop</button>`,
    });
    root.querySelector("[data-cancel]").addEventListener("click", close);

    const eligibility = $("#d-eligibility", root);
    const sync = () => {
      const kind = eligibility.value;
      $("#d-dataset", root).closest(".field").style.display = kind === "merkle_dataset" ? "" : "none";
      $("#d-url", root).closest(".field").style.display = kind === "dataset_url" ? "" : "none";
      $("#d-allocation", root).closest(".field").style.display = kind === "allocation_view" ? "" : "none";
    };
    eligibility.addEventListener("change", sync);
    sync();

    $("#add-drop-form", root).addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      const value = (key) => String(form.get(key) || "").trim();
      const payload = {
        id: value("id"),
        name: value("name"),
        chain: value("chain"),
        distributor: value("distributor"),
        token: value("token") || null,
        token_decimals: Number(value("token_decimals") || 18),
        claim_method: value("claim_method") || undefined,
        claim_deadline: value("claim_deadline") || null,
        notes: value("notes") || null,
        links: {},
      };
      const claimed = value("claimed_method");
      if (claimed) payload.claimed_method = claimed;
      const website = value("website");
      const docs = value("docs");
      if (website) payload.links.website = website;
      if (docs) payload.links.claim = docs;
      const kind = value("eligibility");
      if (kind === "merkle_dataset") payload.eligibility = { type: "merkle_dataset", dataset: value("dataset") };
      if (kind === "dataset_url") payload.eligibility = { type: "dataset_url", url: value("url") };
      if (kind === "allocation_view") payload.eligibility = { type: "allocation_view", method: value("allocation_method") || "allocation(address)" };
      try {
        await api("/api/airdrops", { method: "POST", body: payload });
        close();
        toast("ok", "Airdrop registered", "It will be probed on the next scan.");
        await renderRoute();
      } catch (err) {
        toast("err", "Could not register airdrop", err.message);
      }
    });
  }

  // ------------------------------------------------------------ drop profile
  async function viewDrop(root, dropId) {
    const drop = await api(`/api/airdrops/${encodeURIComponent(dropId)}`);
    document.title = `${drop.name} · idlescan`;
    const write = state.config.write_enabled;
    const contract = drop.contract || {};
    const positions = drop.positions || [];
    const history = drop.history || [];
    const links = Object.entries(drop.links || {})
      .map(([name, url]) => safeUrl(url) ? `<a class="btn btn-sm btn-ghost" href="${esc(safeUrl(url))}" target="_blank" rel="noopener noreferrer">${esc(name)} ↗</a>` : "")
      .join("");
    const eligibility = drop.eligibility || null;
    const days = daysLeft(drop.deadline);
    const deadlineTone = days === null ? "" : days < 0 ? "neg" : days <= 14 ? "stat-amber" : "";

    root.innerHTML = `
      ${crumb("Airdrop registry", "#/drops")}
      <div class="page-head profile-head">
        <div>
          <div class="row-between" style="justify-content:flex-start;gap:9px;flex-wrap:wrap">
            <h1>${esc(drop.name)}</h1>
            ${phaseBadge(drop.phase)}
            ${riskBadge(drop.risk)}
            ${drop.enabled ? "" : `<span class="badge badge-source">disabled</span>`}
          </div>
          <p class="page-sub">${esc(drop.chain)} · <span class="mono">${esc(drop.id)}</span> · ${esc(drop.source)} entry${drop.verified_on ? ` · verified ${esc(drop.verified_on)}` : ""}</p>
          <div class="link-row mt-sm">${links || `<span class="dim tiny">No official links recorded — treat this entry with extra caution.</span>`}</div>
        </div>
        <div class="page-actions">
          <label class="switch" title="Track this airdrop in scans"><input type="checkbox" id="profile-toggle" ${drop.enabled ? "checked" : ""}><span>track</span></label>
          <div class="export-menu">
            <a class="btn btn-sm" href="${esc(drop.export.json)}" download>JSON</a>
            <a class="btn btn-sm" href="${esc(drop.export.csv)}" download>CSV</a>
            <a class="btn btn-sm" href="${esc(drop.export.md)}" download>Report</a>
          </div>
        </div>
      </div>

      <div class="grid grid-stats">
        <div class="stat" style="--stat-accent:var(--accent)"><span class="stat-label">Recoverable net</span><span class="stat-value stat-money">${usd(drop.claimable_net_usd)}</span><span class="stat-sub">${drop.unclaimed} unclaimed · ${positions.length} position${positions.length === 1 ? "" : "s"}</span></div>
        <div class="stat" style="--stat-accent:var(--sky)"><span class="stat-label">Unclaimed gross</span><span class="stat-value">${usd(drop.unclaimed_usd)}</span><span class="stat-sub">gas ${usd(drop.gas_usd)}</span></div>
        <div class="stat" style="--stat-accent:var(--amber)"><span class="stat-label">Claim deadline</span><span class="stat-value ${deadlineTone}" style="font-size:21px">${drop.deadline ? fmtDate(drop.deadline) : "undocumented"}</span><span class="stat-sub">${days === null ? "no deadline recorded" : days < 0 ? "window closed" : `${days} day${days === 1 ? "" : "s"} left`}</span></div>
        <div class="stat" style="--stat-accent:var(--teal)"><span class="stat-label">Wallets tracked</span><span class="stat-value">${drop.wallet_count}</span><span class="stat-sub">${proofCounts(drop.proof)}</span></div>
      </div>

      <div class="grid grid-2 mt">
        <section class="panel">
          <div class="panel-head"><h2>Contract provenance</h2><span class="panel-note">${contract.chain_id ? `chain id ${contract.chain_id}` : "chain unknown"}</span></div>
          <div class="panel-body">
            <dl class="kv">
              <dt>Distributor</dt>
              <dd class="mono">${contract.distributor_url ? `<a class="link" href="${esc(contract.distributor_url)}" target="_blank" rel="noopener noreferrer">${esc(contract.distributor)} ↗</a>` : esc(drop.distributor)} <button class="copy-inline" data-copy-inline="${esc(drop.distributor)}" type="button">copy</button></dd>
              <dt>Reward token</dt>
              <dd class="mono">${contract.token ? (contract.token_url ? `<a class="link" href="${esc(contract.token_url)}" target="_blank" rel="noopener noreferrer">${esc(contract.token)} ↗</a>` : esc(contract.token)) : "native token"}${contract.token ? ` <span class="dim">· ${esc(contract.token_decimals)} decimals</span>` : ""}</dd>
              <dt>Claim method</dt><dd class="mono small">${esc(contract.claim_method || "not recorded")}</dd>
              <dt>Claimed check</dt><dd class="mono small">${esc(contract.claimed_method || "not recorded")}</dd>
              ${contract.root_method ? `<dt>Root method</dt><dd class="mono small">${esc(contract.root_method)}</dd>` : ""}
              <dt>Explorer</dt><dd>${contract.explorer ? `<a class="link" href="${esc(contract.explorer)}" target="_blank" rel="noopener noreferrer">${esc(contract.explorer)} ↗</a>` : "—"}</dd>
            </dl>
            <div class="divider"></div>
            <span class="eyebrow">Eligibility source</span>
            ${eligibility ? `<dl class="kv kv-tight mt-sm">
              <dt>Type</dt><dd>${esc(eligibility.type)}</dd>
              ${eligibility.dataset ? `<dt>Dataset</dt><dd class="mono small">${esc(eligibility.dataset)}</dd>` : ""}
              ${eligibility.url ? `<dt>URL</dt><dd class="mono small break">${esc(eligibility.url)}</dd>` : ""}
              ${eligibility.method ? `<dt>Method</dt><dd class="mono small">${esc(eligibility.method)}</dd>` : ""}
              ${eligibility.leaf_encoding ? `<dt>Leaf</dt><dd>${esc(eligibility.leaf_encoding)}${eligibility.double_hash ? " · double-hashed" : ""}${eligibility.sort_pairs ? " · sorted pairs" : ""}</dd>` : ""}
            </dl>` : `<p class="muted small mt-sm">No eligibility source. Allocations for this drop cannot be discovered automatically — it can only be tracked manually.</p>`}
            ${drop.notes ? `<div class="callout callout-info mt">${esc(drop.notes)}</div>` : ""}
            <div class="callout callout-warn mt">
              <strong>Verify out-of-band.</strong> Confirm the distributor address against the project's official documentation before signing anything. Safety score ${drop.risk.score}/100 — higher is safer.
            </div>
          </div>
        </section>

        <div class="stack">
          <section class="panel">
            <div class="panel-head"><h2>Value history</h2><span class="panel-note">${history.length} snapshot${history.length === 1 ? "" : "s"}</span></div>
            <div class="panel-body">
              ${sparkline(history.map((row) => row.net_usd || 0), { label: `${drop.name} net value` })}
              <div class="spark-scale"><span>oldest</span><span>now</span><span>${history.length ? timeAgo(history[history.length - 1].created_at) : "no scans"}</span></div>
              <div class="divider"></div>
              <span class="eyebrow">Status breakdown</span>
              <div class="status-mix mt-sm">${(drop.status_breakdown || []).map((item) => `
                <div class="status-mix-row">${statusBadge(item.status)}<span class="grow dim">${item.count} position${item.count === 1 ? "" : "s"}</span><span class="mono">${usd(item.usd_value)}</span></div>`).join("") || `<p class="muted small">No positions in the latest snapshot.</p>`}</div>
            </div>
          </section>

          <section class="panel">
            <div class="panel-head"><h2>Recent activity</h2><a class="btn btn-sm btn-ghost" href="#/activity">Audit →</a></div>
            <div class="panel-body">
              ${(drop.executions || []).length || (drop.alerts || []).length ? `<div class="feed">
                ${(drop.executions || []).map((execution) => `<div class="feed-item">
                  <div class="feed-icon">⇢</div>
                  <div class="feed-main"><div class="feed-title">Execution ${statusBadge(execution.status)}</div><div class="feed-sub mono">${esc(short(execution.wallet, 5))}${execution.tx_hash ? ` · ${esc(short(execution.tx_hash, 6))}` : ""}</div></div>
                  <div class="feed-time">${timeAgo(execution.created_at)}</div>
                </div>`).join("")}
                ${(drop.alerts || []).map((alert) => `<div class="feed-item">
                  <div class="feed-icon">${alert.severity === "critical" ? "!" : "·"}</div>
                  <div class="feed-main"><div class="feed-title">${esc(alert.title)}</div><div class="feed-sub">${esc(alert.body)}</div></div>
                  <div class="feed-time">${timeAgo(alert.created_at)}</div>
                </div>`).join("")}
              </div>` : `<p class="muted small">No claim executions or alerts for this drop yet.</p>`}
            </div>
          </section>
        </div>
      </div>

      <section class="panel mt">
        <div class="panel-head">
          <div><h2>Tracked positions</h2><p class="panel-note">Latest snapshot · proof verified against the distributor's on-chain merkle root.</p></div>
          <a class="btn btn-sm btn-ghost" href="/api/calendar.ics?drop=${encodeURIComponent(drop.id)}" download>Add deadline to calendar</a>
        </div>
        <div class="panel-body flush">
          ${positions.length ? `<div class="table-wrap"><table>
            <thead><tr><th>Wallet</th><th class="num">Amount</th><th class="num">Value</th><th class="num">Gas</th><th class="num">Net</th><th>Deadline</th><th>Proof</th><th>Status</th><th></th></tr></thead>
            <tbody>${positions.map((row) => {
              const actionable = row.status === "unclaimed" && !row.expired;
              const belowGas = row.net_usd !== null && row.net_usd <= 0 && row.status === "unclaimed";
              return `<tr>
                <td><a class="link" href="#/wallet/${encodeURIComponent(row.wallet)}">${walletLabel(row)}</a><div class="addr">${esc(short(row.wallet))}</div></td>
                <td class="num">${formatUnits(row.amount_raw, row.decimals, 2)}</td>
                <td class="num">${usd(row.usd_value)}</td>
                <td class="num muted">${usd(row.gas_usd)}</td>
                <td class="num ${row.net_usd === null ? "" : row.net_usd <= 0 ? "neg" : "pos"}">${usd(row.net_usd)}${belowGas ? `<div class="tiny neg">below gas</div>` : ""}</td>
                <td>${deadlineChip(row.deadline)}</td>
                <td>${proofBadge(row.proof_verified)}</td>
                <td>${statusBadge(row.status)}${row.expired ? `<div class="tiny dim">window closed</div>` : ""}</td>
                <td class="num">${actionable ? (write
                  ? `<button class="btn btn-sm btn-primary" data-prepare="${esc(drop.id)}" data-wallet="${esc(row.wallet)}" type="button">Prepare</button>`
                  : `<span class="badge badge-source">locked</span>`) : `<span class="dim tiny">—</span>`}</td>
              </tr>`;
            }).join("")}</tbody>
          </table></div>` : emptyState("◇", "No tracked positions", "Add wallets and run a scan. If this drop has no eligibility source, allocations cannot be discovered automatically.")}
        </div>
      </section>`;

    $$("[data-copy-inline]", root).forEach((button) => {
      button.addEventListener("click", () => copyText(button.dataset.copyInline));
    });
    $("#profile-toggle", root).addEventListener("change", async (event) => {
      try {
        await api(`/api/airdrops/${encodeURIComponent(drop.id)}`, { method: "PATCH", body: { enabled: event.target.checked } });
        toast("ok", event.target.checked ? "Airdrop enabled" : "Airdrop disabled", "It will be included in the next scan.");
        state.portfolio = null;
      } catch (err) {
        event.target.checked = !event.target.checked;
        toast("err", "Could not update airdrop", err.message);
      }
    });
    bindPrepareButtons(root);
  }

  // ---------------------------------------------------------- wallet profile
  async function viewWallet(root, address) {
    const data = await api(`/api/wallets/${encodeURIComponent(address)}`);
    const wallet = data.wallet || {};
    const snapshot = data.snapshot;
    const holdings = data.holdings || [];
    const claims = data.claims || [];
    const totals = data.totals || {};
    const unclaimed = claims.filter((row) => row.status === "unclaimed" && !row.expired);
    const history = data.history || [];
    document.title = `${wallet.label || short(address, 8)} · idlescan`;
    const chain = (state.config?.chains || []).find((item) => item.key === (holdings[0] || {}).chain);
    const explorer = safeUrl(chain?.explorer ? `${chain.explorer}/address/${wallet.address}` : "");
    const write = state.config.write_enabled;
    const tags = String(wallet.tags || "").split(",").map((tag) => tag.trim()).filter(Boolean);

    root.innerHTML = `
      ${crumb("Wallets", "#/wallets")}
      <div class="page-head profile-head">
        <div>
          <div class="row-between" style="justify-content:flex-start;gap:10px;flex-wrap:wrap">
            <h1>${wallet.label ? esc(wallet.label) : "Unlabelled wallet"}</h1>
            ${tags.map((tag) => `<span class="tag">${esc(tag)}</span>`).join("")}
          </div>
          <p class="page-sub"><span class="mono">${esc(wallet.address)}</span> <button class="copy-inline" data-copy-inline="${esc(wallet.address)}" type="button">copy</button></p>
          <div class="hero-meta" style="margin-top:8px">
            <span>tracked since ${esc(fmtDate(wallet.created_at))}</span><span class="dot"></span>
            <span>${totals.chains ?? 0} chain${totals.chains === 1 ? "" : "s"}</span><span class="dot"></span>
            <span>${totals.assets ?? 0} balance line${totals.assets === 1 ? "" : "s"}</span>
          </div>
        </div>
        <div class="page-actions">
          ${explorer ? `<a class="btn btn-sm" href="${esc(explorer)}" target="_blank" rel="noopener noreferrer">Open explorer ↗</a>` : ""}
          <button class="btn btn-sm" id="wallet-rescan" type="button">Rescan portfolio</button>
        </div>
      </div>

      <div class="grid grid-stats">
        <div class="stat" style="--stat-accent:var(--accent)"><span class="stat-label">Idle balances</span><span class="stat-value stat-money">${usd(totals.idle_usd)}</span><span class="stat-sub">${totals.assets ?? 0} assets · ${esc(snapshot ? `snapshot #${snapshot.id}` : "no snapshot")}</span></div>
        <div class="stat" style="--stat-accent:var(--sky)"><span class="stat-label">Unclaimed gross</span><span class="stat-value">${usd(totals.unclaimed_usd)}</span><span class="stat-sub">${totals.unclaimed ?? 0} allocation${totals.unclaimed === 1 ? "" : "s"}</span></div>
        <div class="stat" style="--stat-accent:var(--teal)"><span class="stat-label">Net after gas</span><span class="stat-value">${usd(totals.net_usd)}</span><span class="stat-sub">gas ${usd(totals.gas_usd)}</span></div>
        <div class="stat" style="--stat-accent:var(--violet)"><span class="stat-label">Claimed</span><span class="stat-value">${usd(totals.claimed_usd)}</span><span class="stat-sub">${totals.claimed ?? 0} confirmed claim${totals.claimed === 1 ? "" : "s"}</span></div>
      </div>

      <div class="grid grid-2 mt">
        <section class="panel">
          <div class="panel-head"><h2>Allocation history</h2><span class="panel-note">${history.length} snapshot${history.length === 1 ? "" : "s"}</span></div>
          <div class="panel-body">
            ${sparkline(history.map((row) => (row.idle_usd || 0) + (row.unclaimed_usd || 0)), { label: "Wallet value" })}
            <div class="spark-scale"><span>oldest</span><span>now</span><span>idle + unclaimed</span></div>
            <div class="divider"></div>
            <span class="eyebrow">Claim positions by chain</span>
            <div class="status-mix mt-sm">${(data.chains || []).map((item) => `
              <div class="status-mix-row"><span class="tag">${esc(item.chain)}</span><span class="grow dim">idle ${usd(item.idle_usd)}</span><span class="mono">${usd(item.unclaimed_usd)}</span></div>`).join("") || `<p class="muted small">No balances or positions recorded.</p>`}</div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head"><h2>Provenance</h2><span class="panel-note">latest snapshot</span></div>
          <div class="panel-body">
            ${snapshot ? `<dl class="kv">
              <dt>Snapshot</dt><dd>#${snapshot.id} · ${timeAgo(snapshot.created_at)}</dd>
              <dt>Trigger</dt><dd>${esc(snapshot.trigger)}</dd>
              <dt>Chains scanned</dt><dd>${esc((snapshot.chains || []).join(", ") || "all")}</dd>
              <dt>Wallets in scan</dt><dd>${(snapshot.wallets || []).length}</dd>
              <dt>Idle value</dt><dd>${usd(snapshot.totals.idle_usd)}</dd>
              <dt>Unclaimed</dt><dd>${usd(snapshot.totals.unclaimed_usd)}</dd>
            </dl>` : `<p class="muted small">No snapshot yet. Run a scan to populate this profile.</p>`}
            <div class="divider"></div>
            <p class="small muted">This is a watch-only profile. The address is public data; no key material is stored or requested.</p>
          </div>
        </section>
      </div>

      <section class="panel mt">
        <div class="panel-head"><h2>Claim positions</h2><span class="panel-note">${claims.length} total · ${unclaimed.length} actionable</span></div>
        <div class="panel-body flush">
          ${claims.length ? `<div class="table-wrap"><table>
            <thead><tr><th>Drop</th><th class="num">Amount</th><th class="num">Value</th><th class="num">Gas</th><th class="num">Net</th><th>Deadline</th><th>Proof</th><th>Status</th><th></th></tr></thead>
            <tbody>${claims.map((row) => {
              const actionable = row.status === "unclaimed" && !row.expired;
              return `<tr>
                <td><a class="strong link" href="#/drop/${encodeURIComponent(row.drop_id)}">${esc(row.name)}</a><div class="tiny dim">${esc(row.chain)} · ${esc(row.drop_id)}</div></td>
                <td class="num">${formatUnits(row.amount_raw, row.decimals, 2)}</td>
                <td class="num">${usd(row.usd_value)}</td>
                <td class="num muted">${usd(row.gas_usd)}</td>
                <td class="num ${row.net_usd === null ? "" : row.net_usd <= 0 ? "neg" : "pos"}">${usd(row.net_usd)}</td>
                <td>${deadlineChip(row.deadline)}</td>
                <td>${proofBadge(row.proof_verified)}</td>
                <td>${statusBadge(row.status)}${row.expired ? `<div class="tiny dim">window closed</div>` : ""}</td>
                <td class="num">${actionable ? (write
                  ? `<button class="btn btn-sm btn-primary" data-prepare="${esc(row.drop_id)}" data-wallet="${esc(row.wallet)}" type="button">Prepare</button>`
                  : `<span class="badge badge-source">locked</span>`) : `<span class="dim tiny">—</span>`}</td>
              </tr>`;
            }).join("")}</tbody>
          </table></div>` : emptyState("◇", "No claim positions", "This wallet has no discovered allocations in the latest snapshot.")}
        </div>
      </section>

      <section class="panel mt">
        <div class="panel-head"><h2>Idle balances</h2><span class="panel-note">${holdings.length} line${holdings.length === 1 ? "" : "s"}</span></div>
        <div class="panel-body flush">
          ${holdings.length ? `<div class="table-wrap"><table>
            <thead><tr><th>Asset</th><th>Chain</th><th class="num">Amount</th><th class="num">Value</th></tr></thead>
            <tbody>${holdings.map((row) => `<tr>
              <td class="strong">${esc(row.asset)}</td>
              <td><span class="tag">${esc(row.chain)}</span></td>
              <td class="num">${formatUnits(row.raw, row.decimals, 4)}</td>
              <td class="num">${usd(row.usd_value)}</td>
            </tr>`).join("")}</tbody>
          </table></div>` : emptyState("◇", "No idle balances", "The latest scan found no balances above the configured minimum.")}
        </div>
      </section>

      ${((data.executions || []).length || (data.alerts || []).length) ? `<section class="panel mt">
        <div class="panel-head"><h2>Wallet activity</h2><a class="btn btn-sm btn-ghost" href="#/activity">Audit →</a></div>
        <div class="panel-body">
          <div class="feed">
            ${(data.executions || []).map((execution) => `<div class="feed-item">
              <div class="feed-icon">⇢</div>
              <div class="feed-main"><div class="feed-title">${esc(execution.drop_id)} ${statusBadge(execution.status)}</div><div class="feed-sub mono">${esc(execution.tx_hash ? short(execution.tx_hash, 8) : "no hash")}</div></div>
              <div class="feed-time">${timeAgo(execution.created_at)}</div>
            </div>`).join("")}
            ${(data.alerts || []).map((alert) => `<div class="feed-item">
              <div class="feed-icon">${alert.severity === "critical" ? "!" : "·"}</div>
              <div class="feed-main"><div class="feed-title">${esc(alert.title)}</div><div class="feed-sub">${esc(alert.body)}</div></div>
              <div class="feed-time">${timeAgo(alert.created_at)}</div>
            </div>`).join("")}
          </div>
        </div>
      </section>` : ""}`;

    $$("[data-copy-inline]", root).forEach((button) => {
      button.addEventListener("click", () => copyText(button.dataset.copyInline));
    });
    const rescan = $("#wallet-rescan", root);
    if (rescan) rescan.addEventListener("click", startScan);
    bindPrepareButtons(root);
  }

  // ----------------------------------------------------------------- claims
  async function viewClaims(root) {
    if (!state.portfolio) state.portfolio = await api("/api/portfolio");
    const portfolio = state.portfolio;
    const write = state.config.write_enabled;

    root.innerHTML = `
      <div class="page-head">
        <div>
          <h1>Claims planner</h1>
          <p class="page-sub">Prioritise by <strong>net after gas</strong>, not headline value. Every proof shown here was re-derived and checked against the distributor's on-chain merkle root before it was reported.</p>
        </div>
        <div class="page-actions">
          <a class="btn" href="/api/export/csv" download>Export CSV</a>
          <a class="btn" href="/api/export/md" download>Export report</a>
        </div>
      </div>

      ${write ? `
        <div class="callout callout-warn" style="margin-bottom:14px">
          <strong>Write mode is enabled.</strong> Preparing a claim returns an <em>unsigned</em> transaction. The server never holds keys, signs, or broadcasts — you review and approve in your own wallet. A legitimate claim never asks for a token approval or seed phrase.
        </div>` : `
        <div class="callout callout-info" style="margin-bottom:14px">
          <strong>Read-only mode.</strong> Claim preparation is locked. Set <code>IDLESCAN_READ_ONLY=false</code> in your <code>.env</code>, restart, and this planner will hand unsigned transactions to your browser wallet. <a href="#/settings" style="color:inherit;text-decoration:underline">How to toggle →</a>
        </div>`}

      <section class="panel">
        <div class="panel-head">
          <div class="toolbar">
            <div class="seg" id="status-filter">
              <button type="button" data-status="actionable" class="${filters.claimStatus === "actionable" ? "active" : ""}">Actionable</button>
              <button type="button" data-status="all" class="${filters.claimStatus === "all" ? "active" : ""}">All positions</button>
              <button type="button" data-status="expired" class="${filters.claimStatus === "expired" ? "active" : ""}">Expired</button>
            </div>
            <select id="wallet-filter" style="width:190px">
              <option value="">All wallets</option>
              ${(portfolio.wallets || []).map((wallet) => `<option value="${esc(wallet.address)}" ${filters.claimWallet === wallet.address ? "selected" : ""}>${esc(wallet.label || short(wallet.address))}</option>`).join("")}
            </select>
            <label class="switch"><input type="checkbox" id="hide-below-gas" ${filters.hideBelowGas ? "checked" : ""}><span>hide below-gas</span></label>
          </div>
          <span class="panel-note" id="claim-count"></span>
        </div>
        <div class="panel-body flush" id="claims-table"></div>
        <div class="panel-body"><p class="small dim" id="gas-note">${esc(state.config.gas_estimate_note || "")}</p></div>
      </section>

      <div class="grid grid-2-even mt">
        <section class="panel">
          <div class="panel-head"><h2>Idle balances</h2><span class="panel-note">${(portfolio.holdings || []).length} lines</span></div>
          <div class="panel-body flush">${holdingsTable(portfolio)}</div>
        </section>
        <section class="panel">
          <div class="panel-head"><h2>Snapshot provenance</h2><span class="panel-note">#${portfolio.snapshot ? portfolio.snapshot.id : "—"}</span></div>
          <div class="panel-body">
            ${portfolio.snapshot ? `
              <dl class="kv">
                <dt>Captured</dt><dd>${esc(portfolio.snapshot.created_at)}</dd>
                <dt>Trigger</dt><dd>${esc(portfolio.snapshot.trigger)}</dd>
                <dt>Chains</dt><dd>${esc((portfolio.snapshot.chains || []).join(", ") || "all")}</dd>
                <dt>Wallets</dt><dd>${(portfolio.snapshot.wallets || []).length}</dd>
                <dt>Idle value</dt><dd>${usd(portfolio.snapshot.totals.idle_usd)}</dd>
                <dt>Unclaimed</dt><dd>${usd(portfolio.snapshot.totals.unclaimed_usd)}</dd>
              </dl>
              <div class="divider"></div>
              <p class="small muted">Snapshots are immutable. Rescanning creates a new one, so a claim confirmed on-chain shows up as a status change on the next scan.</p>
              <div class="toolbar mt">
                <a class="btn btn-sm" href="/api/export/json" download>JSON</a>
                <a class="btn btn-sm" href="/api/export/csv" download>CSV</a>
                <a class="btn btn-sm" href="/api/export/md" download>Markdown</a>
              </div>` : emptyState("◇", "No snapshot", "Run a scan to populate claim positions.")}
          </div>
        </section>
      </div>`;

    const redraw = () => {
      $("#claims-table", root).innerHTML = claimsTable(portfolio);
      $("#claim-count", root).textContent = `${visibleClaims(portfolio).length} shown`;
      bindPrepareButtons(root);
      markScrollables(root);
    };
    $$("#status-filter button", root).forEach((button) => {
      button.addEventListener("click", () => {
        filters.claimStatus = button.dataset.status;
        $$("#status-filter button", root).forEach((item) => item.classList.toggle("active", item === button));
        redraw();
      });
    });
    $("#wallet-filter", root).addEventListener("change", (event) => {
      filters.claimWallet = event.target.value;
      redraw();
    });
    $("#hide-below-gas", root).addEventListener("change", (event) => {
      filters.hideBelowGas = event.target.checked;
      redraw();
    });
    redraw();
  }

  function visibleClaims(portfolio) {
    let rows = portfolio.claims || [];
    if (filters.claimStatus === "actionable") rows = rows.filter((row) => row.status === "unclaimed" && !row.expired);
    if (filters.claimStatus === "expired") rows = rows.filter((row) => Boolean(row.expired));
    if (filters.claimWallet) rows = rows.filter((row) => row.wallet === filters.claimWallet);
    if (filters.hideBelowGas) rows = rows.filter((row) => row.net_usd === null || row.net_usd > 0);
    return rows;
  }

  function claimsTable(portfolio) {
    const rows = visibleClaims(portfolio);
    if (!rows.length) {
      return emptyState("◇", "Nothing here", filters.claimStatus === "actionable"
        ? "No unclaimed, unexpired allocations in this snapshot. Try “All positions” or rescan after adding wallets."
        : "No claim positions match these filters.");
    }
    const write = state.config.write_enabled;
    return `<div class="table-wrap"><table>
      <thead><tr><th>Drop</th><th>Wallet</th><th class="num">Amount</th><th class="num">Value</th><th class="num">Gas</th><th class="num">Net</th><th>Deadline</th><th>Proof</th><th>Status</th><th></th></tr></thead>
      <tbody>${rows.map((row) => {
        const actionable = row.status === "unclaimed" && !row.expired;
        const net = row.net_usd ?? 0;
        const netClass = row.net_usd === null ? "" : net <= 0 ? "neg" : "pos";
        const belowGas = row.net_usd !== null && net <= 0 && row.status === "unclaimed";
        return `<tr>
          <td><a class="strong link" href="#/drop/${encodeURIComponent(row.drop_id)}">${esc(row.name)}</a><div class="tiny dim">${esc(row.chain)} · ${esc(row.drop_id)}</div></td>
          <td><a class="link" href="#/wallet/${encodeURIComponent(row.wallet)}">${walletLabel(row)}</a><div class="addr" title="${esc(row.wallet)}">${esc(short(row.wallet))}</div></td>
          <td class="num">${formatUnits(row.amount_raw, row.decimals, 2)}</td>
          <td class="num">${usd(row.usd_value)}</td>
          <td class="num muted">${usd(row.gas_usd)}</td>
          <td class="num ${netClass}"><span title="${esc(row.net_usd === null ? "price unavailable" : `gross ${usd(row.usd_value)} minus gas ${usd(row.gas_usd)}`)}">${usd(row.net_usd)}</span>${belowGas ? `<div class="tiny neg">below gas</div>` : ""}</td>
          <td>${deadlineChip(row.deadline)}</td>
          <td>${proofBadge(row.proof_verified)}</td>
          <td>${statusBadge(row.status)}${row.expired ? `<div class="tiny dim">window closed</div>` : ""}</td>
          <td class="num">${actionable
            ? (write
              ? `<button class="btn btn-sm btn-primary" data-prepare="${esc(row.drop_id)}" data-wallet="${esc(row.wallet)}" type="button">Prepare</button>`
              : `<button class="btn btn-sm" disabled title="Enable write mode (IDLESCAN_READ_ONLY=false) to prepare transactions">Prepare</button>`)
            : `<span class="dim tiny">—</span>`}</td>
        </tr>`;
      }).join("")}</tbody>
    </table></div>`;
  }

  function holdingsTable(portfolio) {
    const holdings = [...(portfolio.holdings || [])].sort((a, b) => (b.usd_value ?? 0) - (a.usd_value ?? 0));
    if (!holdings.length) return emptyState("◇", "No idle balances", "The last snapshot found no token or native balances above the configured minimum.");
    return `<div class="table-wrap"><table>
      <thead><tr><th>Asset</th><th>Chain</th><th>Wallet</th><th class="num">Amount</th><th class="num">Value</th></tr></thead>
      <tbody>${holdings.map((row) => `<tr>
        <td class="strong">${esc(row.asset)}</td>
        <td><span class="tag">${esc(row.chain)}</span></td>
        <td title="${esc(row.wallet)}"><a class="link" href="#/wallet/${encodeURIComponent(row.wallet)}">${walletLabel(row)}</a><div class="addr">${esc(short(row.wallet))}</div></td>
        <td class="num">${formatUnits(row.raw, row.decimals, 4)}</td>
        <td class="num">${usd(row.usd_value)}</td>
      </tr>`).join("")}</tbody>
    </table></div>`;
  }

  function bindPrepareButtons(root) {
    $$("[data-prepare]", root).forEach((button) => {
      button.addEventListener("click", () => prepareClaim(button.dataset.prepare, button.dataset.wallet, button));
    });
  }

  async function prepareClaim(dropId, wallet, button) {
    if (button) { button.disabled = true; button.textContent = "Preparing…"; }
    try {
      const data = await api(`/api/claims/${encodeURIComponent(dropId)}/${encodeURIComponent(wallet)}/prepare`, {
        method: "POST", body: {},
      });
      openClaimModal(dropId, wallet, data);
    } catch (err) {
      toast("err", "Cannot prepare claim", err.message);
    } finally {
      if (button) { button.disabled = false; button.textContent = "Prepare"; }
    }
  }

  function openClaimModal(dropId, wallet, data) {
    const claim = data.claim || {};
    const tx = data.transaction || {};
    const txJson = JSON.stringify(tx, null, 2);
    const hasWallet = typeof window.ethereum !== "undefined";
    const { root, close } = openModal({
      title: `Prepare claim · ${claim.name || dropId}`,
      subtitle: "Review every field before signing. The server cannot sign or send this transaction.",
      wide: true,
      body: `
        <dl class="kv">
          <dt>Position</dt><dd>${esc(short(wallet, 6))}</dd>
          <dt>Chain</dt><dd>${esc(claim.chain)} (${esc(tx.chainId || "unknown chain id")})</dd>
          <dt>Distributor</dt><dd>${esc(tx.to)} <button class="copy-inline" data-copy-inline="${esc(tx.to)}" type="button">copy</button></dd>
          <dt>Amount</dt><dd>${formatUnits(claim.amount, claim.decimals, 4)} tokens</dd>
          <dt>Gross value</dt><dd>${usd(claim.usd_value)}</dd>
          <dt>Gas estimate</dt><dd>${usd(claim.gas_usd)}</dd>
          <dt>Net after gas</dt><dd>${usd(claim.net_usd)}</dd>
        </dl>

        <div class="callout callout-danger mt">
          <strong>Verify before you sign.</strong>
          <ul>
            ${(data.warnings || []).map((warning) => `<li>${esc(warning)}</li>`).join("")}
          </ul>
        </div>

        <div class="mt">
          <div class="row-between" style="margin-bottom:6px">
            <span class="eyebrow">Unsigned transaction</span>
            <button class="btn btn-sm btn-ghost" id="copy-tx" type="button">Copy JSON</button>
          </div>
          <pre class="code-block" id="tx-json">${esc(txJson)}</pre>
        </div>

        <div class="mt">
          <label class="field"><span>Already sent it elsewhere? Record the transaction hash</span>
            <div class="field-row">
              <input id="tx-hash-input" placeholder="0x… 32-byte transaction hash" autocomplete="off" spellcheck="false">
              <button class="btn" id="record-tx" type="button">Record</button>
            </div>
          </label>
          <div class="hint">The server checks the receipt when possible and stores the result. It never broadcasts.</div>
        </div>

        <div id="claim-status" class="small muted mt"></div>`,
      footer: `
        <span class="left small dim">Position ${esc(dropId)} · ${esc(short(wallet))}</span>
        <button class="btn btn-ghost" type="button" data-close>Close</button>
        <button class="btn btn-primary" id="wallet-send" type="button" ${hasWallet ? "" : "disabled"}>
          ${hasWallet ? "Connect wallet & send" : "No browser wallet detected"}
        </button>`,
    });

    root.querySelector("[data-close]").addEventListener("click", close);
    $("#copy-tx", root).addEventListener("click", () => copyText(txJson, "Transaction JSON copied"));
    $$("[data-copy-inline]", root).forEach((button) => {
      button.addEventListener("click", () => copyText(button.dataset.copyInline));
    });

    const status = $("#claim-status", root);

    const record = async (hash) => {
      status.textContent = "Recording transaction…";
      try {
        const result = await api("/api/claims/confirm", {
          method: "POST",
          body: { drop_id: dropId, wallet, tx_hash: hash },
        });
        status.innerHTML = `<span class="badge badge-${result.status === "failed" ? "critical" : "info"}">${esc(result.status)}</span> ${esc(result.detail || "")}`;
        toast(result.status === "failed" ? "err" : "ok", `Transaction ${result.status}`, short(hash, 8));
        state.portfolio = null;
      } catch (err) {
        status.textContent = err.message;
        toast("err", "Could not record transaction", err.message);
      }
    };

    $("#record-tx", root).addEventListener("click", () => {
      const hash = $("#tx-hash-input", root).value.trim();
      if (!/^0x[0-9a-fA-F]{64}$/.test(hash)) {
        toast("err", "Invalid transaction hash", "Expected 0x followed by 64 hex characters.");
        return;
      }
      record(hash);
    });

    const sendButton = $("#wallet-send", root);
    if (hasWallet) {
      sendButton.addEventListener("click", async () => {
        sendButton.disabled = true;
        sendButton.textContent = "Check your wallet…";
        status.textContent = "Requesting account access…";
        try {
          const accounts = await window.ethereum.request({ method: "eth_requestAccounts" });
          const from = (accounts && accounts[0]) || "";
          if (!from) throw new Error("No account returned by the wallet.");
          if (from.toLowerCase() !== String(wallet).toLowerCase()) {
            throw new Error(`Connected account ${short(from)} does not match the position wallet ${short(wallet)}. Switch accounts in your wallet and try again.`);
          }
          if (tx.chainId) {
            try {
              await window.ethereum.request({
                method: "wallet_switchEthereumChain",
                params: [{ chainId: tx.chainId }],
              });
            } catch (switchError) {
              if (switchError && switchError.code === 4902) {
                throw new Error(`Your wallet does not know chain ${tx.chainId}. Add it first, then retry.`);
              }
              if (switchError && switchError.code === 4001) throw new Error("Network switch was rejected.");
              throw new Error(`Could not switch your wallet to chain ${tx.chainId}. Switch manually, then retry.`);
            }
          }
          status.textContent = "Waiting for wallet approval…";
          const hash = await window.ethereum.request({
            method: "eth_sendTransaction",
            params: [{ from, to: tx.to, value: tx.value || "0x0", data: tx.data }],
          });
          status.innerHTML = `Submitted <span class="mono">${esc(short(hash, 8))}</span> · verifying receipt…`;
          toast("ok", "Transaction submitted", short(hash, 8));
          await record(hash);
          sendButton.textContent = "Sent";
        } catch (err) {
          sendButton.disabled = false;
          sendButton.textContent = "Connect wallet & send";
          const message = err?.message || String(err);
          status.textContent = message;
          toast("err", "Wallet hand-off stopped", message);
        }
      });
    }
  }

  // ----------------------------------------------------------------- checker
  const CHECK_RE = /^0x[0-9a-fA-F]{40}$/;

  async function viewCheck(root, address = "", query = null) {
    const seed = String(address || query?.get("address") || "").trim();
    const demoSeeds = [
      ["0x1111111111111111111111111111111111111111", "unclaimed allocation"],
      ["0x2222222222222222222222222222222222222222", "claimed"],
      ["0x3333333333333333333333333333333333333333", "no allocation"],
    ];

    root.innerHTML = `
      <div class="page-head">
        <div>
          <h1>Check any address</h1>
          <p class="page-sub">Run a read-only eligibility check against every tracked drop without adding the address to your watchlist. Nothing is persisted — no wallet row, no snapshot, no alert.</p>
        </div>
        <div class="page-actions">
          <span class="badge badge-source">read-only</span>
          ${state.config.demo ? `<span class="badge badge-warning">demo ledger</span>` : ""}
        </div>
      </div>

      <section class="panel check-panel">
        <form id="check-form" class="check-form" novalidate>
          <div class="check-input">
            <span class="search-glyph" aria-hidden="true">⌕</span>
            <label class="sr-only" for="check-address">EVM address</label>
            <input id="check-address" name="address" type="text" inputmode="text" autocomplete="off" spellcheck="false"
              placeholder="0x… · paste any EVM address" value="${esc(seed)}">
          </div>
          <button class="btn btn-primary" id="check-submit" type="submit">Check eligibility</button>
        </form>
        <div class="check-meta">
          <p class="small muted">Covers ${state.config.chains.length} EVM chains and every enabled drop. The server answers from a single read-only pass${state.config.demo ? " over the demo ledger" : ""}.</p>
          ${state.config.demo ? `<div class="check-seeds">${demoSeeds.map(([value, label]) =>
            `<button class="chip chip-demo" type="button" data-check-seed="${value}">${esc(label)} <span class="mono">${esc(short(value, 6))}</span></button>`).join("")}</div>` : ""}
        </div>
      </section>

      <div id="check-result" class="check-result" aria-live="polite"></div>`;

    const host = $("#check-result", root);
    const input = $("#check-address", root);
    const submit = $("#check-submit", root);

    const addToWatchlist = async (address, button) => {
      button.disabled = true;
      try {
        await api("/api/wallets", { method: "POST", body: { address } });
        toast("ok", "Added to watchlist", "Run a scan to track its balances over time.");
        // Update the result in place: re-rendering the route would run the whole
        // eligibility pass again just to flip one badge.
        button.replaceWith(Object.assign(document.createElement("span"), {
          className: "badge badge-high",
          textContent: "tracking",
          title: "This address is in your watchlist",
        }));
        const notTracked = $(".check-tags .badge-source", host);
        if (notTracked) {
          notTracked.className = "badge badge-high";
          notTracked.textContent = "tracking";
        }
        await refreshShell();
      } catch (err) {
        button.disabled = false;
        toast("err", "Could not add wallet", err.message);
      }
    };

    const runCheck = async (raw) => {
      const value = String(raw || "").trim();
      if (!CHECK_RE.test(value)) {
        host.innerHTML = `<div class="callout callout-warn">
          <strong>That is not an EVM address.</strong> Addresses are <code>0x</code> followed by 40 hexadecimal characters. ENS names are not resolved by a read-only checker — paste the address instead.
        </div>`;
        input.focus();
        return;
      }
      submit.disabled = true;
      submit.textContent = "Checking…";
      host.innerHTML = `<section class="panel"><div class="panel-body"><div class="loading-block">
        <div class="skeleton wide"></div><div class="skeleton"></div><div class="skeleton short"></div>
      </div></div></section>`;
      try {
        const data = await api("/api/check", { method: "POST", body: { address: value } });
        const clean = data.address;
        input.value = clean;
        history.replaceState(null, "", `#/check/${clean}`);
        host.innerHTML = checkResult(data);
        const add = $("[data-add-from-check]", host);
        if (add) add.addEventListener("click", () => addToWatchlist(clean, add));
        $$("[data-copy-inline]", host).forEach((button) => {
          button.addEventListener("click", () => copyText(button.dataset.copyInline));
        });
      } catch (err) {
        host.innerHTML = `<div class="callout callout-warn"><strong>Check failed.</strong> ${esc(err.message)}</div>`;
      } finally {
        submit.disabled = false;
        submit.textContent = "Check eligibility";
      }
    };

    $("#check-form", root).addEventListener("submit", (event) => {
      event.preventDefault();
      runCheck(input.value);
    });
    $$("[data-check-seed]", root).forEach((button) => {
      button.addEventListener("click", () => {
        input.value = button.dataset.checkSeed;
        runCheck(button.dataset.checkSeed);
      });
    });
    if (seed) runCheck(seed);
  }

  function checkResult(data) {
    const totals = data.totals || {};
    const claims = data.claims || [];
    const holdings = data.holdings || [];
    const actionable = claims.filter((row) => row.status === "unclaimed" && !row.expired);
    const addButton = data.tracked
      ? ""
      : `<button class="btn btn-sm btn-primary" type="button" data-add-from-check>Add to watchlist</button>`;

    const stats = `
      <div class="grid grid-stats">
        <div class="stat" style="--stat-accent:var(--accent)"><span class="stat-label">Recoverable net</span><span class="stat-value stat-money">${usd(totals.net_usd)}</span><span class="stat-sub">after gas · ${actionable.length} actionable</span></div>
        <div class="stat" style="--stat-accent:var(--sky)"><span class="stat-label">Unclaimed gross</span><span class="stat-value">${usd(totals.unclaimed_usd)}</span><span class="stat-sub">gas ${usd(totals.gas_usd)}</span></div>
        <div class="stat" style="--stat-accent:var(--amber)"><span class="stat-label">Idle balances</span><span class="stat-value">${usd(totals.idle_usd)}</span><span class="stat-sub">${totals.assets ?? 0} asset${totals.assets === 1 ? "" : "s"}</span></div>
        <div class="stat" style="--stat-accent:var(--violet)"><span class="stat-label">Airdrop positions</span><span class="stat-value">${totals.positions ?? 0}</span><span class="stat-sub">${totals.drops ?? 0} drop${totals.drops === 1 ? "" : "s"} · ${totals.claimed ?? 0} claimed</span></div>
      </div>`;

    const warnings = (data.warnings || []).length
      ? `<div class="callout callout-warn check-warnings"><strong>Scan warnings.</strong><ul>${data.warnings.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>`
      : "";

    const claimsBlock = claims.length ? `
      <section class="panel mt">
        <div class="panel-head"><h2>Allocations</h2><span class="panel-note">${claims.length} position${claims.length === 1 ? "" : "s"} across ${totals.drops ?? 0} drop${totals.drops === 1 ? "" : "s"}</span></div>
        <div class="panel-body flush">
          <div class="table-wrap"><table>
            <thead><tr><th>Drop</th><th class="num">Amount</th><th class="num">Value</th><th class="num">Gas</th><th class="num">Net</th><th>Deadline</th><th>Proof</th><th>Status</th></tr></thead>
            <tbody>${claims.map((row) => {
              const netClass = row.net_usd === null ? "" : row.net_usd <= 0 ? "neg" : "pos";
              return `<tr>
                <td><a class="strong link" href="#/drop/${encodeURIComponent(row.drop_id)}">${esc(row.name)}</a><div class="tiny dim">${esc(row.chain)} · ${esc(row.drop_id)}</div></td>
                <td class="num">${formatUnits(row.amount_raw, row.decimals, 2)}</td>
                <td class="num">${usd(row.usd_value)}</td>
                <td class="num muted">${usd(row.gas_usd)}</td>
                <td class="num ${netClass}">${usd(row.net_usd)}</td>
                <td>${deadlineChip(row.deadline)}</td>
                <td>${proofBadge(row.proof_verified)}</td>
                <td>${statusBadge(row.expired ? "expired" : row.status)}${row.phase ? `<div class="tiny dim">${esc(row.phase)}</div>` : ""}</td>
              </tr>`;
            }).join("")}</tbody>
          </table></div>
        </div>
      </section>` : `
      <section class="panel mt">
        <div class="panel-head"><h2>Allocations</h2><span class="panel-note">no matching claims</span></div>
        <div class="panel-body">${emptyState("◇", "No allocation found",
          data.demo
            ? "This demo ledger only contains allocations for its three sample wallets. Track a real wallet and run a live scan to test the checker against current data."
            : "No enabled drop lists this address in its eligibility source. New drops are added to the registry as they are published — rescan after adding one.")}</div>
      </section>`;

    const holdingsBlock = holdings.length ? `
      <section class="panel mt">
        <div class="panel-head"><h2>Idle balances</h2><span class="panel-note">${holdings.length} line${holdings.length === 1 ? "" : "s"} above the configured minimum</span></div>
        <div class="panel-body flush">
          <div class="table-wrap"><table>
            <thead><tr><th>Asset</th><th>Chain</th><th class="num">Amount</th><th class="num">Value</th></tr></thead>
            <tbody>${[...holdings].sort((a, b) => (b.usd_value ?? 0) - (a.usd_value ?? 0)).map((row) => `<tr>
              <td class="strong">${esc(row.asset)}</td>
              <td><span class="tag">${esc(row.chain)}</span></td>
              <td class="num">${formatUnits(row.raw, row.decimals, 4)}</td>
              <td class="num">${usd(row.usd_value)}</td>
            </tr>`).join("")}</tbody>
          </table></div>
        </div>
      </section>` : "";

    return `
      <section class="panel check-summary">
        <div class="panel-head check-head">
          <div class="check-ident">
            <span class="eyebrow">Checked address</span>
            <span class="check-address mono" title="${esc(data.address)}">${esc(data.address)}</span>
            <div class="check-tags">
              ${data.tracked ? `<span class="badge badge-high">tracking</span>` : `<span class="badge badge-source">not tracked</span>`}
              ${data.demo ? `<span class="badge badge-warning">demo ledger</span>` : `<span class="badge badge-info">live scan</span>`}
              <span class="dim tiny">checked ${esc(timeAgo(data.checked_at))}</span>
            </div>
          </div>
          <div class="toolbar">
            <button class="btn btn-sm" type="button" data-copy-inline="${esc(data.address)}">Copy address</button>
            ${addButton}
          </div>
        </div>
        <div class="panel-body">
          ${stats}
          ${warnings}
          <p class="small muted mt">Nothing about this check was saved: no watchlist entry, snapshot or alert. ${data.tracked ? "The address is already tracked, so its next full scan will update your portfolio normally." : "Add it to the watchlist only if you want its value tracked over time."}</p>
        </div>
      </section>
      ${claimsBlock}
      ${holdingsBlock}`;
  }

  // ---------------------------------------------------------------- wallets
  async function viewWallets(root) {
    const [{ wallets }, stats] = await Promise.all([api("/api/wallets"), api("/api/stats")]);
    root.innerHTML = `
      <div class="page-head">
        <div>
          <h1>Wallets</h1>
          <p class="page-sub">Watch-only addresses with labels and tags. These are public keys — the server never asks for, stores, or uses a private key or seed phrase.</p>
        </div>
        <div class="page-actions">
          <button class="btn" id="show-import-wallets" type="button">Import list</button>
          <button class="btn btn-primary" id="show-add-wallet" type="button">+ Add wallet</button>
        </div>
      </div>

      <section class="panel" id="add-wallet-panel" hidden>
        <div class="panel-head"><h2>Add a wallet</h2><span class="panel-note">EVM address (0x + 40 hex)</span></div>
        <div class="panel-body">
          <form id="add-wallet-form" class="form-grid">
            <div class="field full"><label for="w-address">Address *</label><input id="w-address" name="address" required placeholder="0x…" autocomplete="off" spellcheck="false"></div>
            <div class="field"><label for="w-label">Label</label><input id="w-label" name="label" placeholder="Cold wallet, main, research…" maxlength="60"></div>
            <div class="field"><label for="w-tags">Tags</label><input id="w-tags" name="tags" placeholder="defi, old, whale" maxlength="120"></div>
            <div class="field full"><button class="btn btn-primary" type="submit">Add wallet</button></div>
          </form>
        </div>
      </section>

      <div class="grid grid-stats mt">
        <div class="stat" style="--stat-accent:var(--accent)"><span class="stat-label">Wallets</span><span class="stat-value">${wallets.length}</span><span class="stat-sub">watch-only</span></div>
        <div class="stat" style="--stat-accent:var(--sky)"><span class="stat-label">Idle tracked</span><span class="stat-value">${usd(wallets.reduce((sum, w) => sum + (w.idle_usd || 0), 0))}</span><span class="stat-sub">latest snapshot</span></div>
        <div class="stat" style="--stat-accent:var(--amber)"><span class="stat-label">Unclaimed</span><span class="stat-value stat-amber">${usd(wallets.reduce((sum, w) => sum + (w.unclaimed_usd || 0), 0))}</span><span class="stat-sub">${wallets.reduce((sum, w) => sum + (w.unclaimed || 0), 0)} allocations</span></div>
        <div class="stat" style="--stat-accent:var(--teal)"><span class="stat-label">Net claimable</span><span class="stat-value">${usd(wallets.reduce((sum, w) => sum + (w.claimable_net_usd || 0), 0))}</span><span class="stat-sub">after gas</span></div>
      </div>

      <section class="panel mt">
        <div class="panel-head"><h2>Tracked addresses</h2><span class="panel-note">${wallets.length} total</span></div>
        <div class="panel-body flush">
          ${wallets.length ? `<div class="table-wrap"><table>
            <thead><tr><th>Wallet</th><th>Tags</th><th class="num">Assets</th><th class="num">Idle</th><th class="num">Unclaimed</th><th class="num">Net claimable</th><th></th></tr></thead>
            <tbody>${wallets.map((wallet) => `<tr>
              <td><div class="wallet-cell"><a class="name link" href="#/wallet/${encodeURIComponent(wallet.address)}">${wallet.label ? esc(wallet.label) : `<span class="dim">unlabelled</span>`}</a><span class="addr" title="${esc(wallet.address)}">${esc(short(wallet.address, 8))} <button class="copy-inline" data-copy-inline="${esc(wallet.address)}" type="button">copy</button></span></div></td>
              <td>${(wallet.tags || "").split(",").filter(Boolean).map((tag) => `<span class="tag">${esc(tag.trim())}</span>`).join(" ") || `<span class="dim tiny">—</span>`}</td>
              <td class="num">${wallet.assets ?? 0}</td>
              <td class="num">${usd(wallet.idle_usd || 0)}</td>
              <td class="num">${usd(wallet.unclaimed_usd || 0)}${wallet.unclaimed ? `<div class="tiny dim">${wallet.unclaimed} claim${wallet.unclaimed === 1 ? "" : "s"}</div>` : ""}</td>
              <td class="num ${(wallet.claimable_net_usd || 0) > 0 ? "pos" : ""}">${usd(wallet.claimable_net_usd || 0)}</td>
              <td class="num nowrap">
                <button class="btn btn-sm btn-ghost" data-edit-wallet="${esc(wallet.address)}" data-label="${esc(wallet.label || "")}" data-tags="${esc(wallet.tags || "")}" type="button">Edit</button>
                <button class="btn btn-sm btn-danger" data-delete-wallet="${esc(wallet.address)}" type="button">Remove</button>
              </td>
            </tr>`).join("")}</tbody>
          </table></div>` : emptyState("＋", "No wallets yet", "Add a public address to begin. You can label it and tag it however you like — nothing is sent anywhere except read-only RPC calls when you scan.")}
        </div>
      </section>

      <div class="callout callout-info mt">
        <strong>Scanning is read-only.</strong> The scanner calls public RPC endpoints for balances and claim state, plus a price API for USD values. Addresses are public; no key material is ever collected. ${stats.snapshot ? `Last snapshot ${esc(timeAgo(stats.snapshot.created_at))}.` : "No snapshot yet."}
      </div>`;

    const panel = $("#add-wallet-panel", root);
    $("#show-add-wallet", root).addEventListener("click", () => {
      panel.hidden = !panel.hidden;
      if (!panel.hidden) $("#w-address", root).focus();
    });    $("#add-wallet-form", root).addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      try {
        await api("/api/wallets", {
          method: "POST",
          body: {
            address: String(form.get("address") || "").trim(),
            label: String(form.get("label") || "").trim(),
            tags: String(form.get("tags") || "").trim(),
          },
        });
        toast("ok", "Wallet added", "Run a scan to price its balances and claim positions.");
        state.portfolio = null;
        await renderRoute();
      } catch (err) {
        toast("err", "Could not add wallet", err.message);
      }
    });

    $("#show-import-wallets", root).addEventListener("click", () => {
      const { root: modal, close } = openModal({
        title: "Import wallets",
        subtitle: "One address per line · up to 200 rows",
        wide: true,
        body: `<form id="import-form" class="form-grid">
          <div class="field full">
            <label for="import-rows">Addresses</label>
            <textarea id="import-rows" name="rows" rows="9" spellcheck="false" placeholder="0xabc…&#10;0xdef… , label , tags&#10;0x123…"></textarea>
            <p class="hint">Use <code>address</code>, <code>address, label</code> or <code>address, label, tags</code> per line. Duplicates and invalid rows are reported without aborting the import.</p>
          </div>
        </form>
        <div id="import-report" class="import-report" aria-live="polite"></div>`,
        footer: `<button class="btn btn-ghost" type="button" data-cancel>Cancel</button><button class="btn btn-primary" type="submit" form="import-form">Import</button>`,
      });
      modal.querySelector("[data-cancel]").addEventListener("click", close);
      $("#import-form", modal).addEventListener("submit", async (event) => {
        event.preventDefault();
        const lines = String(new FormData(event.target).get("rows") || "")
          .split(/\r?\n/)
          .map((line) => line.trim())
          .filter(Boolean);
        if (!lines.length) { toast("err", "Nothing to import", "Paste at least one address."); return; }
        const entries = lines.map((line) => {
          const [address = "", label = "", ...rest] = line.split(",").map((part) => part.trim());
          return { address, label, tags: rest.join(", ") };
        });
        const report = $("#import-report", modal);
        report.innerHTML = `<p class="small muted">Importing ${entries.length} row${entries.length === 1 ? "" : "s"}…</p>`;
        try {
          const result = await api("/api/wallets/bulk", { method: "POST", body: { wallets: entries } });
          const errorRows = (result.errors || []).map((row) =>
            `<li><span class="mono">${esc(row.address || "—")}</span> — ${esc(row.error)}</li>`).join("");
          report.innerHTML = `
            <div class="callout callout-success">
              <strong>${result.added} added · ${result.updated} updated${result.errors.length ? ` · ${result.errors.length} skipped` : ""}.</strong>
              ${result.added || result.updated ? "Run a scan to price the new addresses." : ""}
            </div>
            ${errorRows ? `<ul class="import-errors">${errorRows}</ul>` : ""}`;
          if (result.added || result.updated) {
            state.portfolio = null;
            await refreshShell();
            toast("ok", "Wallets imported", `${result.added} added · ${result.updated} updated`);
          }
        } catch (err) {
          report.innerHTML = `<div class="callout callout-warn"><strong>Import failed.</strong> ${esc(err.message)}</div>`;
        }
      });
    });

    $$("[data-copy-inline]", root).forEach((button) => {
      button.addEventListener("click", () => copyText(button.dataset.copyInline));
    });

    $$("[data-edit-wallet]", root).forEach((button) => {
      button.addEventListener("click", () => {
        const address = button.dataset.editWallet;
        const { root: modal, close } = openModal({
          title: "Edit wallet",
          subtitle: short(address, 8),
          body: `<form id="edit-wallet-form" class="form-grid">
            <div class="field full"><label for="e-label">Label</label><input id="e-label" name="label" maxlength="60" value="${esc(button.dataset.label)}"></div>
            <div class="field full"><label for="e-tags">Tags</label><input id="e-tags" name="tags" maxlength="120" value="${esc(button.dataset.tags)}"></div>
          </form>`,
          footer: `<button class="btn btn-ghost" type="button" data-cancel>Cancel</button><button class="btn btn-primary" type="submit" form="edit-wallet-form">Save</button>`,
        });
        modal.querySelector("[data-cancel]").addEventListener("click", close);
        $("#edit-wallet-form", modal).addEventListener("submit", async (event) => {
          event.preventDefault();
          const form = new FormData(event.target);
          try {
            await api(`/api/wallets/${address}`, {
              method: "PATCH",
              body: { label: String(form.get("label") || ""), tags: String(form.get("tags") || "") },
            });
            close();
            toast("ok", "Wallet updated");
            await renderRoute();
          } catch (err) { toast("err", "Could not update", err.message); }
        });
      });
    });

    $$("[data-delete-wallet]", root).forEach((button) => {
      button.addEventListener("click", async () => {
        if (!confirm(`Stop tracking ${short(button.dataset.deleteWallet)}? Past snapshots are kept.`)) return;
        try {
          await api(`/api/wallets/${button.dataset.deleteWallet}`, { method: "DELETE" });
          toast("ok", "Wallet removed");
          await renderRoute();
        } catch (err) { toast("err", "Could not remove wallet", err.message); }
      });
    });
  }

  // ----------------------------------------------------------------- alerts
  async function viewAlerts(root) {
    const { alerts } = await api(`/api/alerts${filters.alertsAll ? "?all=1" : ""}`);
    const critical = alerts.filter((alert) => alert.severity === "critical").length;
    const warning = alerts.filter((alert) => alert.severity === "warning").length;

    root.innerHTML = `
      <div class="page-head">
        <div>
          <h1>Alerts</h1>
          <p class="page-sub">Generated only from verifiable signals: deadlines inside 14 days, negative net value, failed proofs, new allocations and confirmed claims.</p>
        </div>
        <div class="page-actions">
          <label class="switch"><input type="checkbox" id="show-acked" ${filters.alertsAll ? "checked" : ""}><span>include acknowledged</span></label>
          <button class="btn" id="ack-all" type="button" ${alerts.length ? "" : "disabled"}>Acknowledge all</button>
        </div>
      </div>

      <div class="grid grid-stats">
        <div class="stat" style="--stat-accent:var(--rose)"><span class="stat-label">Critical</span><span class="stat-value stat-rose">${critical}</span><span class="stat-sub">act before you miss a window</span></div>
        <div class="stat" style="--stat-accent:var(--amber)"><span class="stat-label">Warning</span><span class="stat-value stat-amber">${warning}</span><span class="stat-sub">worth review</span></div>
        <div class="stat" style="--stat-accent:var(--teal)"><span class="stat-label">Info</span><span class="stat-value">${alerts.length - critical - warning}</span><span class="stat-sub">state changes</span></div>
        <div class="stat" style="--stat-accent:var(--sky)"><span class="stat-label">Showing</span><span class="stat-value">${alerts.length}</span><span class="stat-sub">${filters.alertsAll ? "including acknowledged" : "open only"}</span></div>
      </div>

      <section class="panel mt">
        <div class="panel-head"><h2>${filters.alertsAll ? "All alerts" : "Open alerts"}</h2><span class="panel-note">newest first, severity-weighted</span></div>
        <div class="panel-body">
          ${alerts.length ? `<div class="alert-list">${alerts.map((alert) => `
            <div class="alert-item is-${esc(alert.severity)}">
              <div class="alert-main">
                <div class="row-between"><div class="alert-title">${esc(alert.title)}</div>${statusBadge(alert.severity)}</div>
                <div class="alert-body">${esc(alert.body)}</div>
                <div class="alert-meta">
                  <span class="tag">${esc(alert.kind)}</span>
                  ${alert.drop_id ? `<span class="mono">${esc(alert.drop_id)}</span>` : ""}
                  ${alert.wallet ? `<span class="addr">${esc(short(alert.wallet))}</span>` : ""}
                  <span>${timeAgo(alert.created_at)}</span>
                  ${alert.acknowledged ? `<span class="tag">acknowledged</span>` : ""}
                </div>
              </div>
              ${alert.acknowledged ? "" : `<button class="btn btn-sm btn-ghost" data-ack="${alert.id}" type="button">Acknowledge</button>`}
            </div>`).join("")}</div>`
            : emptyState("✓", "You're all caught up", "No open alerts. New alerts appear automatically after each scan — deadlines, proofs, and profitability changes.")}
        </div>
      </section>`;

    $("#show-acked", root).addEventListener("change", (event) => {
      filters.alertsAll = event.target.checked;
      renderRoute();
    });
    $("#ack-all", root).addEventListener("click", async () => {
      try {
        const result = await api("/api/alerts/ack-all", { method: "POST", body: {} });
        toast("ok", "Alerts acknowledged", `${result.acknowledged} cleared`);
        await refreshShell();
        await renderRoute();
      } catch (err) { toast("err", "Could not acknowledge", err.message); }
    });
    $$("[data-ack]", root).forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          await api(`/api/alerts/${button.dataset.ack}/ack`, { method: "POST", body: {} });
          await refreshShell();
          await renderRoute();
        } catch (err) { toast("err", "Could not acknowledge", err.message); }
      });
    });
  }

  // --------------------------------------------------------------- activity
  async function viewActivity(root) {
    const [audit, jobs, snapshots] = await Promise.all([
      api("/api/audit"),
      api("/api/jobs"),
      api("/api/snapshots"),
    ]);

    root.innerHTML = `
      <div class="page-head">
        <div>
          <h1>Activity & audit</h1>
          <p class="page-sub">Every snapshot, claim preparation and administrative change is recorded locally. If write mode is on, this is your paper trail.</p>
        </div>
        <div class="page-actions"><button class="btn" id="activity-refresh" type="button">Refresh</button></div>
      </div>

      <div class="grid grid-2">
        <section class="panel">
          <div class="panel-head"><h2>Scan jobs</h2><span class="panel-note">${jobs.jobs.length} recent</span></div>
          <div class="panel-body">
            ${jobs.jobs.length ? `<div class="feed">${jobs.jobs.slice(0, 8).map((job) => `
              <div class="feed-item">
                <div class="feed-icon">${job.status === "done" ? "✓" : job.status === "failed" ? "×" : "…"}</div>
                <div class="feed-main">
                  <div class="feed-title">${esc(job.kind)} · ${statusBadge(job.status)}</div>
                  <div class="feed-sub">${esc(job.error || job.message || "")}</div>
                  ${job.status === "running" ? `<div class="progress mt" style="max-width:220px"><i style="width:${Math.round((job.progress || 0) * 100)}%"></i></div>` : ""}
                </div>
                <div class="feed-time">${timeAgo(job.created_at)}</div>
              </div>`).join("")}</div>` : emptyState("◇", "No jobs yet", "Run a scan to create the first job.")}
          </div>
        </section>

        <section class="panel">
          <div class="panel-head"><h2>Claim executions</h2><span class="panel-note">${audit.executions.length} recorded</span></div>
          <div class="panel-body">
            ${audit.executions.length ? `<div class="feed">${audit.executions.slice(0, 8).map((execution) => `
              <div class="feed-item">
                <div class="feed-icon">⇢</div>
                <div class="feed-main">
                  <div class="feed-title">${esc(execution.drop_id)} ${statusBadge(execution.status)}</div>
                  <div class="feed-sub mono">${esc(short(execution.wallet, 5))}${execution.tx_hash ? ` · ${esc(short(execution.tx_hash, 6))}` : ""}</div>
                  <div class="feed-sub">${esc(execution.detail || "")}</div>
                </div>
                <div class="feed-time">${timeAgo(execution.created_at)}</div>
              </div>`).join("")}</div>`
              : emptyState("◇", "No claim executions", state.config.write_enabled ? "Prepared and confirmed claims will appear here." : "Enable write mode to prepare unsigned claim transactions.")}
          </div>
        </section>
      </div>

      <section class="panel mt">
        <div class="panel-head"><h2>Snapshots</h2><span class="panel-note">immutable local history</span></div>
        <div class="panel-body flush">
          ${snapshots.snapshots.length ? `<div class="table-wrap"><table>
            <thead><tr><th>#</th><th>Captured</th><th>Trigger</th><th>Chains</th><th class="num">Wallets</th><th class="num">Idle</th><th class="num">Unclaimed</th><th></th></tr></thead>
            <tbody>${snapshots.snapshots.map((snapshot) => `<tr>
              <td class="mono">#${snapshot.id}</td>
              <td>${esc(snapshot.created_at.replace("T", " ").replace("+00:00", " UTC"))}</td>
              <td><span class="tag">${esc(snapshot.trigger)}</span></td>
              <td class="small muted">${esc((snapshot.chains || []).join(", ") || "all")}</td>
              <td class="num">${(snapshot.wallets || []).length}</td>
              <td class="num">${usd(snapshot.totals.idle_usd)}</td>
              <td class="num">${usd(snapshot.totals.unclaimed_usd)}</td>
              <td class="num nowrap"><button class="btn btn-sm btn-ghost" data-snapshot="${snapshot.id}" type="button">Inspect</button> <button class="btn btn-sm btn-ghost" data-diff="${snapshot.id}" type="button" ${snapshot.id > 1 ? "" : "disabled"}>Diff</button></td>
            </tr>`).join("")}</tbody>
          </table></div>` : emptyState("◇", "No snapshots", "Scans create immutable snapshots for diffing and exports.")}
        </div>
      </section>

      <section class="panel mt">
        <div class="panel-head"><h2>Audit log</h2><span class="panel-note">${audit.audit.length} entries</span></div>
        <div class="panel-body">
          ${audit.audit.length ? `<div class="feed">${audit.audit.slice(0, 30).map((entry) => `
            <div class="feed-item">
              <div class="feed-icon">·</div>
              <div class="feed-main">
                <div class="feed-title mono" style="font-size:11.8px">${esc(entry.action)}</div>
                <div class="feed-sub">${esc(entry.detail)}</div>
              </div>
              <div class="feed-time">${esc(entry.actor)} · ${timeAgo(entry.created_at)}</div>
            </div>`).join("")}</div>` : `<p class="muted small">No audit entries yet.</p>`}
        </div>
      </section>`;

    $("#activity-refresh", root).addEventListener("click", renderRoute);
    $$("[data-snapshot]", root).forEach((button) => {
      button.addEventListener("click", () => inspectSnapshot(button.dataset.snapshot));
    });
    $$("[data-diff]", root).forEach((button) => {
      button.addEventListener("click", () => openDiff(button.dataset.diff === "latest" ? null : button.dataset.diff));
    });
  }

  async function inspectSnapshot(snapshotId) {
    try {
      const data = await api(`/api/snapshots/${snapshotId}`);
      const { snapshot, claims, holdings } = data;
      const unclaimed = (claims || []).filter((row) => row.status === "unclaimed" && !row.expired);
      const { root, close } = openModal({
        title: `Snapshot #${snapshot.id}`,
        subtitle: `${snapshot.created_at.replace("T", " ").replace("+00:00", " UTC")} · trigger ${snapshot.trigger}`,
        wide: true,
        body: `
          <div class="grid grid-stats">
            <div class="stat" style="--stat-accent:var(--accent)"><span class="stat-label">Idle</span><span class="stat-value">${usd(snapshot.totals.idle_usd)}</span></div>
            <div class="stat" style="--stat-accent:var(--sky)"><span class="stat-label">Unclaimed</span><span class="stat-value">${usd(snapshot.totals.unclaimed_usd)}</span></div>
            <div class="stat" style="--stat-accent:var(--teal)"><span class="stat-label">Combined</span><span class="stat-value">${usd(snapshot.totals.combined_usd)}</span></div>
            <div class="stat" style="--stat-accent:var(--amber)"><span class="stat-label">Positions</span><span class="stat-value">${(claims || []).length}</span><span class="stat-sub">${unclaimed.length} unclaimed</span></div>
          </div>
          <div class="divider"></div>
          <p class="small muted">${(holdings || []).length} balance lines · ${(claims || []).length} claim positions · ${(snapshot.chains || []).length} chains. Exports include the full snapshot payload.</p>
          <div class="toolbar mt">
            <a class="btn btn-sm" href="/api/export/json" download>JSON</a>
            <a class="btn btn-sm" href="/api/export/csv" download>CSV</a>
            <a class="btn btn-sm" href="/api/export/md" download>Markdown</a>
          </div>`,
        footer: `<button class="btn" type="button" data-close>Close</button>`,
      });
      root.querySelector("[data-close]").addEventListener("click", close);
    } catch (err) { toast("err", "Could not load snapshot", err.message); }
  }

  const DIFF_LABELS = {
    new: ["new allocation", "badge-info"],
    claimed: ["claimed", "badge-high"],
    expired: ["expired", "badge-critical"],
    proof: ["proof change", "badge-warning"],
    status: ["status change", "badge-source"],
    value: ["value move", "badge-source"],
    removed: ["removed", "badge-critical"],
  };

  async function openDiff(snapshotId) {
    try {
      const diff = snapshotId
        ? await api(`/api/snapshots/${encodeURIComponent(snapshotId)}/diff`)
        : (await api("/api/analytics")).diff;
      if (!diff) {
        toast("err", "No diff available", "Run at least two scans to compare snapshots.");
        return;
      }
      if (!diff.previous) {
        toast("err", "Nothing to compare", `Snapshot #${diff.snapshot.id} is the first recorded scan.`);
        return;
      }
      const changes = diff.changes || [];
      const delta = diff.delta || {};
      const { root, close } = openModal({
        title: `Snapshot diff · #${diff.snapshot.id} vs #${diff.previous.id}`,
        subtitle: `${timeAgo(diff.snapshot.created_at)} compared with ${timeAgo(diff.previous.created_at)} · ${changes.length} position change${changes.length === 1 ? "" : "s"}`,
        wide: true,
        body: `
          <div class="grid grid-stats">
            <div class="stat" style="--stat-accent:var(--accent)"><span class="stat-label">Idle change</span><span class="stat-value ${delta.idle_usd >= 0 ? "pos" : "neg"}">${delta.idle_usd >= 0 ? "+" : ""}${usd(delta.idle_usd)}</span></div>
            <div class="stat" style="--stat-accent:var(--sky)"><span class="stat-label">Unclaimed change</span><span class="stat-value ${delta.unclaimed_usd >= 0 ? "pos" : "neg"}">${delta.unclaimed_usd >= 0 ? "+" : ""}${usd(delta.unclaimed_usd)}</span></div>
            <div class="stat" style="--stat-accent:var(--teal)"><span class="stat-label">Combined change</span><span class="stat-value ${delta.combined_usd >= 0 ? "pos" : "neg"}">${delta.combined_usd >= 0 ? "+" : ""}${usd(delta.combined_usd)}</span></div>
            <div class="stat" style="--stat-accent:var(--amber)"><span class="stat-label">Changes</span><span class="stat-value">${changes.length}</span><span class="stat-sub">${diff.counts.claimed || 0} claimed · ${diff.counts.expired || 0} expired</span></div>
          </div>
          <div class="divider"></div>
          ${changes.length ? `<div class="table-wrap"><table>
            <thead><tr><th>Change</th><th>Drop</th><th>Wallet</th><th class="num">Delta</th><th>Status</th></tr></thead>
            <tbody>${changes.map((change) => {
              const [label, cls] = DIFF_LABELS[change.kind] || [change.kind, "badge-source"];
              return `<tr>
                <td><span class="badge ${cls}">${esc(label)}</span></td>
                <td><a class="link" href="#/drop/${encodeURIComponent(change.drop_id)}">${esc(change.name)}</a><div class="tiny dim">${esc(change.chain)}</div></td>
                <td><a class="link" href="#/wallet/${encodeURIComponent(change.wallet)}">${esc(short(change.wallet))}</a></td>
                <td class="num ${change.delta > 0 ? "pos" : change.delta < 0 ? "neg" : "dim"}">${change.delta ? `${change.delta > 0 ? "+" : ""}${usd(change.delta)}` : "—"}</td>
                <td class="tiny">${esc(change.from ?? "—")} → ${esc(change.to ?? "—")}${change.expired ? `<div class="dim">window closed</div>` : ""}</td>
              </tr>`;
            }).join("")}</tbody>
          </table></div>`
            : `<div class="empty"><div class="empty-icon">≈</div><h3>No changes</h3><p>Claim state, proof checks and values are identical between these snapshots.</p></div>`}`,
        footer: `<button class="btn" type="button" data-close>Close</button>`,
      });
      root.querySelector("[data-close]").addEventListener("click", close);
      markScrollables(root);
    } catch (err) {
      toast("err", "Could not compare snapshots", err.message);
    }
  }

  // --------------------------------------------------------------- settings
  async function viewSettings(root) {
    const config = state.config || await api("/api/config");
    state.config = config;
    const toggleSnippet = `# .env\nIDLESCAN_READ_ONLY=false\n\n# optional\nIDLESCAN_HOST=127.0.0.1\nIDLESCAN_PORT=8787\nIDLESCAN_CHAINS=ethereum,base,arbitrum\nIDLESCAN_MIN_USD=1\nIDLESCAN_REFRESH_MINUTES=60`;

    root.innerHTML = `
      <div class="page-head">
        <div>
          <h1>Settings</h1>
          <p class="page-sub">One switch controls on-chain claim hand-off. Local administrative writes (wallets, airdrops, scans, alerts, backups) work in either mode; the server still never holds keys, signs, or broadcasts.</p>
        </div>
        <div class="page-actions">
          <span class="badge ${config.read_only ? "badge-info" : "badge-warning"}">${config.read_only ? "read-only" : "write mode"}</span>
          <span class="badge badge-source">v${esc(config.version)}</span>
        </div>
      </div>

      <p class="small dim" id="disclaimer">${esc(config.disclaimer || "Research tool, not financial advice. Verify contract addresses against official sources.")}</p>

      <div class="grid grid-2">
        <section class="panel">
          <div class="panel-head"><h2>Mode</h2><span class="panel-note">requires restart</span></div>
          <div class="panel-body">
            <div class="row-between">
              <div>
                <div class="strong" style="font-size:16px">${config.read_only ? "Read-only mode" : "Write mode enabled"}</div>
                <p class="small muted" style="margin-top:4px">${config.read_only
                  ? "The server will not return claim transactions. Scanning and research work exactly the same."
                  : "Claim endpoints return unsigned transactions for your wallet to sign. The server still never holds keys or broadcasts."}</p>
              </div>
              <span class="mode-pill ${config.read_only ? "" : "mode-write"}">${config.read_only ? "read-only" : "write mode"}</span>
            </div>
            <div class="divider"></div>
            <p class="small muted">To change the mode, edit <code>.env</code> and restart the process. The UI cannot flip this switch — that is deliberate.</p>
            <div class="mt">
              <div class="row-between" style="margin-bottom:6px"><span class="eyebrow">Toggle recipe</span><button class="btn btn-sm btn-ghost" id="copy-toggle" type="button">Copy</button></div>
              <pre class="code-block">${esc(toggleSnippet)}</pre>
            </div>
            <dl class="kv mt">
              <dt>Bind</dt><dd>${esc(config.host)}:${esc(config.port)}</dd>
              <dt>Auto-refresh</dt><dd>${config.refresh_minutes ? `every ${config.refresh_minutes} min` : "disabled"}</dd>
              <dt>Demo data</dt><dd>${config.demo ? "on" : "off"}</dd>
            </dl>
            ${config.warnings && config.warnings.length ? `<div class="callout callout-warn mt"><strong>Startup warnings</strong><ul>${config.warnings.map((warning) => `<li>${esc(warning)}</li>`).join("")}</ul></div>` : ""}
          </div>
        </section>

        <section class="panel">
          <div class="panel-head"><h2>Security model</h2><span class="panel-note">what this server can and cannot do</span></div>
          <div class="panel-body">
            <div class="callout callout-info">
              <strong>Write mode is a hand-off, not custody.</strong>
              <ul>
                <li>No private keys, seed phrases or signatures are ever sent to or stored by the server.</li>
                <li>Prepare returns a transaction object. Your browser wallet displays and signs it.</li>
                <li>The server never broadcasts a transaction and cannot move funds on its own.</li>
                <li>State-changing API calls require the <code>X-Idlescan: 1</code> header, which blocks cross-site form posts.</li>
                <li>Write mode refuses to start on a non-loopback host unless <code>IDLESCAN_ALLOW_REMOTE_WRITE=true</code>.</li>
                <li>Every prepare/confirm is written to the audit log.</li>
              </ul>
            </div>
            <div class="callout callout-danger mt">
              <strong>Still verify out-of-band.</strong> A malicious registry entry or dataset can point at the wrong distributor. Check the destination address against the project's official documentation before signing. A legitimate claim never requires a token approval or an off-chain signature.
            </div>
            <p class="small muted mt">Portfolio data lives in a local SQLite database that never leaves the server. The only outbound traffic is read-only RPC calls and price lookups.</p>
          </div>
        </section>
      </div>

      <div class="grid grid-2-even mt">
        <section class="panel">
          <div class="panel-head"><h2>Exports</h2><span class="panel-note">latest snapshot</span></div>
          <div class="panel-body">
            <p class="small muted">Download the current snapshot for spreadsheets, research notes or an accountant. JSON includes every field, including calldata and proof results.</p>
            <div class="toolbar mt">
              <a class="btn" href="/api/export/json" download>JSON</a>
              <a class="btn" href="/api/export/csv" download>CSV</a>
              <a class="btn" href="/api/export/md" download>Markdown report</a>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head"><h2>Environment reference</h2><span class="panel-note">all optional except the toggle</span></div>
          <div class="panel-body">
            <dl class="kv kv-plain">
              <dt>IDLESCAN_READ_ONLY</dt><dd>true (default) · false unlocks claim hand-off</dd>
              <dt>IDLESCAN_HOST</dt><dd>127.0.0.1</dd>
              <dt>IDLESCAN_PORT</dt><dd>8787</dd>
              <dt>IDLESCAN_DB</dt><dd>data/idlescan.db</dd>
              <dt>IDLESCAN_CHAINS</dt><dd>empty = all configured</dd>
              <dt>IDLESCAN_MIN_USD</dt><dd>1.0</dd>
              <dt>IDLESCAN_REFRESH_MINUTES</dt><dd>0 = manual</dd>
              <dt>IDLESCAN_DEMO</dt><dd>false</dd>
              <dt>IDLESCAN_ALLOW_REMOTE_WRITE</dt><dd>false</dd>
            </dl>
          </div>
        </section>
      </div>

      <section class="panel mt">
        <div class="panel-head"><h2>Maintenance</h2><span class="panel-note">local, non-destructive</span></div>
        <div class="panel-body">
          <div class="grid grid-2-even">
            <div>
              <div class="strong">Database backup</div>
              <p class="small muted mt-sm">Take a consistent snapshot of the SQLite database while the server is running. Backups land in <code>data/backups/</code> beside the database unless you use <code>idlescan backup --out</code>.</p>
              <div class="toolbar mt">
                <button class="btn btn-primary" id="backup-now" type="button">Back up now</button>
                <span class="small muted" id="backup-status"></span>
              </div>
            </div>
            <div>
              <div class="strong">Deadline calendar</div>
              <p class="small muted mt-sm">Download an iCalendar feed of documented claim deadlines, each with a three-day reminder. Per-drop feeds are available from every airdrop profile.</p>
              <div class="toolbar mt">
                <a class="btn" href="/api/calendar.ics" download>Download .ics</a>
                <code class="small dim">/api/calendar.ics?drop=&lt;id&gt;</code>
              </div>
            </div>
          </div>
          <div class="divider"></div>
          <div class="grid grid-2-even">
            <div>
              <div class="strong">Keyboard</div>
              <p class="small muted mt-sm">Press <kbd>⌘K</kbd> / <kbd>Ctrl K</kbd> anywhere to search drops, wallets, transactions and pages. Press <kbd>/</kbd> outside an input to open the same palette.</p>
            </div>
            <div>
              <div class="strong">Headless parity</div>
              <p class="small muted mt-sm"><code>idlescan backup</code>, <code>idlescan calendar</code>, <code>idlescan export --format csv</code> and every API endpoint are available without the UI.</p>
            </div>
          </div>
        </div>
      </section>

      <section class="panel mt">
        <div class="panel-head"><h2>Chains</h2><span class="panel-note">${config.chains.length} configured · native assets priced by DefiLlama</span></div>
        <div class="panel-body flush">
          <div class="table-wrap"><table>
            <thead><tr><th>Key</th><th>Name</th><th class="num">Chain ID</th><th>Native</th></tr></thead>
            <tbody>${config.chains.map((chain) => `<tr>
              <td class="mono">${esc(chain.key)}</td>
              <td>${esc(chain.name)}</td>
              <td class="num">${chain.chain_id}</td>
              <td>${esc(chain.native)}</td>
            </tr>`).join("")}</tbody>
          </table></div>
        </div>
      </section>

      <div class="callout callout-info mt">
        <strong>CLI parity.</strong> Everything here is also available headless: <code>idlescan serve</code>, <code>idlescan wallets add</code>, <code>idlescan claim prepare</code>, <code>idlescan export</code>. The read-only gate lives in the service layer, so neither surface can bypass it.
      </div>`;

    $("#copy-toggle", root).addEventListener("click", () => copyText(toggleSnippet, "Toggle recipe copied"));

    const backupButton = $("#backup-now", root);
    if (backupButton) {
      backupButton.addEventListener("click", async () => {
        backupButton.disabled = true;
        backupButton.textContent = "Backing up…";
        const status = $("#backup-status", root);
        try {
          const result = await api("/api/backup", { method: "POST", body: {} });
          status.innerHTML = `Wrote <span class="mono">${esc(result.path)}</span> (${Number(result.size).toLocaleString()} bytes)`;
          toast("ok", "Backup written", result.path);
        } catch (err) {
          status.textContent = err.message;
          toast("err", "Backup failed", err.message);
        } finally {
          backupButton.disabled = false;
          backupButton.textContent = "Back up now";
        }
      });
    }
  }

  // --------------------------------------------------------------- routing
  const ROUTES = {
    overview: { label: "Overview", render: viewOverview },
    check: { label: "Address check", render: (root) => viewCheck(root) },
    drops: { label: "Airdrops", render: viewDrops },
    claims: { label: "Claims", render: viewClaims },
    wallets: { label: "Wallets", render: viewWallets },
    alerts: { label: "Alerts", render: viewAlerts },
    activity: { label: "Activity", render: viewActivity },
    settings: { label: "Settings", render: viewSettings },
  };

  let renderToken = 0;

  function resolveRoute() {
    const raw = location.hash.replace(/^#\/?/, "");
    const [pathPart, searchPart] = raw.split("?");
    const parts = pathPart.split("/").filter(Boolean).map((part) => {
      try { return decodeURIComponent(part); } catch { return part; }
    });
    const query = new URLSearchParams(searchPart || "");
    const head = (parts[0] || "overview").toLowerCase();
    if (head === "drop" && parts[1]) {
      return { name: "drop", nav: "drops", label: "Airdrop profile", render: (root) => viewDrop(root, parts[1]), query };
    }
    if (head === "wallet" && parts[1]) {
      return { name: "wallet", nav: "wallets", label: "Wallet profile", render: (root) => viewWallet(root, parts[1]), query };
    }
    if (head === "check") {
      return {
        name: "check",
        nav: "check",
        label: "Address check",
        render: (root) => viewCheck(root, parts[1] || "", query),
        query,
      };
    }
    const route = ROUTES[head] ? head : "overview";
    return { name: route, nav: route, label: ROUTES[route].label, render: ROUTES[route].render, query };
  }

  async function renderRoute() {
    const resolved = resolveRoute();
    state.route = resolved.name;
    $$("#nav a").forEach((link) => {
      const active = link.dataset.route === resolved.nav;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    const activeLink = $("#nav a.active");
    if (activeLink && typeof activeLink.scrollIntoView === "function") {
      activeLink.scrollIntoView({ block: "nearest", inline: "center" });
    }
    document.title = `${resolved.label} · idlescan`;
    renderBanners();

    const token = ++renderToken;
    app.setAttribute("aria-busy", "true");
    app.innerHTML = skeleton();
    try {
      await resolved.render(app);
      if (token !== renderToken) return;
      app.setAttribute("aria-busy", "false");
      markScrollables(app);
      document.documentElement.scrollTop = 0;
      document.body.scrollTop = 0;
    } catch (err) {
      if (token !== renderToken) return;
      app.innerHTML = errorBlock(err);
      const retry = $("#retry-btn");
      if (retry) retry.addEventListener("click", renderRoute);
      app.setAttribute("aria-busy", "false");
    }
  }

  // ------------------------------------------------------------------- boot
  async function boot() {
    $("#scan-btn").addEventListener("click", startScan);
    const trigger = $("#command-trigger");
    if (trigger) trigger.addEventListener("click", () => openPalette());
    installShortcuts();
    window.addEventListener("hashchange", () => { state.portfolio = null; renderRoute(); });
    let resizeTimer = null;
    window.addEventListener("resize", () => {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(refreshScrollStates, 150);
    });

    try {
      const [config, stats, jobs] = await Promise.all([
        api("/api/config"), api("/api/stats"), api("/api/jobs").catch(() => ({ jobs: [] })),
      ]);
      state.config = config;
      state.stats = stats;
      updateChrome();

      const running = (jobs.jobs || []).find((job) => job.status === "running" || job.status === "queued");
      if (running) pollJob(running.id);
    } catch (err) {
      app.innerHTML = errorBlock(err);
      const retry = $("#retry-btn");
      if (retry) retry.addEventListener("click", boot);
      return;
    }
    await renderRoute();
  }

  boot();
})();

// ─── Helpers ───────────────────────────────────────────────
function getRsiClass(rsi) {
    if (rsi < 30) return "rsi-oversold";
    if (rsi > 70) return "rsi-overbought";
    return "rsi-neutral";
}

function getSignal(rsi) {
    if (rsi < 30) return { text: "Buy", cls: "signal-buy" };
    if (rsi > 70) return { text: "Sell", cls: "signal-sell" };
    return { text: "Hold", cls: "signal-neutral" };
}

function formatChange(pct) {
    const sign = pct >= 0 ? "+" : "";
    const cls = pct >= 0 ? "change-positive" : "change-negative";
    return `<span class="${cls}">${sign}${pct.toFixed(2)}%</span>`;
}

function buildRow(stock, index) {
    const signal = getSignal(stock.rsi);
    return `
        <tr>
            <td>${index + 1}</td>
            <td class="ticker-name">${stock.ticker}</td>
            <td>$${stock.price.toLocaleString("en-US", { minimumFractionDigits: 2 })}</td>
            <td>${formatChange(stock.change_pct)}</td>
            <td><span class="rsi-value ${getRsiClass(stock.rsi)}">${stock.rsi}</span></td>
            <td><span class="signal ${signal.cls}">${signal.text}</span></td>
        </tr>`;
}

function buildTableHTML(bodyId, headerTitle) {
    return `
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Stock</th>
                    <th>Price ($)</th>
                    <th>Change %</th>
                    <th>RSI (14)</th>
                    <th>Signal</th>
                </tr>
            </thead>
            <tbody id="${bodyId}"></tbody>
        </table>`;
}

function populateTable(bodyId, stocks) {
    const tbody = document.getElementById(bodyId);
    if (tbody) {
        tbody.innerHTML = stocks.map((s, i) => buildRow(s, i)).join("");
    }
}

// ─── State ─────────────────────────────────────────────────
let indices = [];
let activeTab = null;
const loadedPanels = {};

// Earnings state
let earningsLoaded = false;


// ─── Theme Toggle ──────────────────────────────────────────
function getTheme() {
    return document.documentElement.getAttribute("data-theme") || "dark";
}

function themeColors() {
    const dark = getTheme() === "dark";
    return {
        grid: dark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.08)",
        tick: dark ? "#8b8fa3" : "#5f6477",
        tooltipBg: dark ? "rgba(15,17,23,0.9)" : "rgba(255,255,255,0.95)",
        tooltipText: dark ? "#e1e4ed" : "#1a1d27",
    };
}

function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
    const btn = document.getElementById("theme-toggle");
    if (btn) btn.textContent = theme === "dark" ? "🌙" : "☀️";
    // Update chart colors if chart exists
    if (priceChart) {
        const tc = themeColors();
        priceChart.options.scales.x.grid.color = tc.grid;
        priceChart.options.scales.x.ticks.color = tc.tick;
        priceChart.options.scales.y.grid.color = tc.grid;
        priceChart.options.scales.y.ticks.color = tc.tick;
        priceChart.options.plugins.tooltip.backgroundColor = tc.tooltipBg;
        priceChart.options.plugins.tooltip.titleColor = tc.tooltipText;
        priceChart.options.plugins.tooltip.bodyColor = tc.tooltipText;
        priceChart.update();
    }
}

function setupThemeToggle() {
    const saved = localStorage.getItem("theme") || "dark";
    applyTheme(saved);
    const btn = document.getElementById("theme-toggle");
    if (btn) {
        btn.addEventListener("click", () => {
            const current = getTheme();
            applyTheme(current === "dark" ? "light" : "dark");
        });
    }
}

// ─── Chart Widget State ───────────────────────────────────
let priceChart = null;
let currentPeriod = "1mo";
let currentTicker = "";
const smaSelections = { 50: false, 100: false, 200: false };
let optionsTerm = "long";
let optionsSortBy = "volume";

const dateFormatter = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    year: "numeric",
});

function formatDateLabel(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return dateFormatter.format(date);
}

function createChart(ctx) {
    const tc = themeColors();
    return new Chart(ctx, {
        type: "line",
        data: {
            labels: [],
            datasets: [
                {
                    label: "Close",
                    data: [],
                    borderColor: "#22c55e",
                    backgroundColor: "rgba(34,197,94,0.10)",
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                    fill: true,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            scales: {
                x: {
                    grid: { color: tc.grid, drawBorder: false },
                    ticks: {
                        color: tc.tick,
                        maxRotation: 0,
                        autoSkip: true,
                        maxTicksLimit: 8,
                        autoSkipPadding: 16,
                        callback: function (value) {
                            const label = this.getLabelForValue(value);
                            return formatDateLabel(label);
                        },
                    },
                },
                y: {
                    grid: { color: tc.grid, drawBorder: false },
                    ticks: {
                        color: tc.tick,
                        callback: v => `$${v}`,
                    },
                },
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: tc.tooltipBg,
                    titleColor: tc.tooltipText,
                    bodyColor: tc.tooltipText,
                    callbacks: {
                        title: (items) => formatDateLabel(items?.[0]?.label ?? ""),
                        label: (ctx) => `Close: $${Number(ctx.parsed.y).toFixed(2)}`,
                    },
                },
            },
        },
    });
}

function computeSMA(values, window) {
    const result = new Array(values.length).fill(null);
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
        sum += values[i];
        if (i >= window) {
            sum -= values[i - window];
        }
        if (i >= window - 1) {
            result[i] = +(sum / window).toFixed(2);
        }
    }
    return result;
}

function updateSmaDatasets(values) {
    if (!priceChart) return;
    const existing = priceChart.data.datasets.filter(d => !d.isSma);
    const warning = document.getElementById("sma-warning");

    const smaDatasets = [];
    if (smaSelections[50] && values.length >= 50) {
        smaDatasets.push({
            label: "SMA 50",
            data: computeSMA(values, 50),
            borderColor: "#60a5fa",
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.2,
            fill: false,
            isSma: true,
        });
    }
    if (smaSelections[100] && values.length >= 100) {
        smaDatasets.push({
            label: "SMA 100",
            data: computeSMA(values, 100),
            borderColor: "#f59e0b",
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.2,
            fill: false,
            isSma: true,
        });
    }
    if (smaSelections[200] && values.length >= 200) {
        smaDatasets.push({
            label: "SMA 200",
            data: computeSMA(values, 200),
            borderColor: "#a855f7",
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.2,
            fill: false,
            isSma: true,
        });
    }

    priceChart.data.datasets = [...existing, ...smaDatasets];

    if (warning) {
        const missing = [];
        if (smaSelections[50] && values.length < 50) missing.push("50");
        if (smaSelections[100] && values.length < 100) missing.push("100");
        if (smaSelections[200] && values.length < 200) missing.push("200");

        if (missing.length) {
            warning.textContent = `Not enough data points for SMA ${missing.join(", ")}. Try a longer timeframe.`;
            warning.classList.remove("hidden");
        } else {
            warning.textContent = "";
            warning.classList.add("hidden");
        }
    }
}

async function fetchAndRenderHistory(ticker, period) {
    const loadingEl = document.getElementById("chart-loading");
    const errorEl = document.getElementById("chart-error");
    const canvas = document.getElementById("price-chart");

    errorEl.classList.add("hidden");
    loadingEl.classList.remove("hidden");

    try {
        const res = await fetch(`/api/history?ticker=${encodeURIComponent(ticker)}&period=${encodeURIComponent(period)}`);
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${res.status}`);
        }
        const payload = await res.json();
        const data = payload.data || [];
        if (!data.length) throw new Error("No historical data available for this ticker");

    const labels = data.map(d => d.date);
    const values = data.map(d => d.close);

        if (!priceChart) {
            priceChart = createChart(canvas.getContext("2d"));
        }

    priceChart.data.labels = labels;
    priceChart.data.datasets[0].data = values;

    const first = values[0];
    const last = values[values.length - 1];
    const isUp = last >= first;
    const lineColor = isUp ? "#22c55e" : "#ef4444";
    const fillColor = isUp ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.12)";
    priceChart.data.datasets[0].borderColor = lineColor;
    priceChart.data.datasets[0].backgroundColor = fillColor;
    updateSmaDatasets(values);
        priceChart.update();

    fetchAndRenderOptions(ticker, optionsTerm);
    fetchAndRenderOwnership(ticker);

        loadingEl.classList.add("hidden");
    } catch (err) {
        console.error("Chart error:", err);
        loadingEl.classList.add("hidden");
        errorEl.textContent = `⚠️ ${err.message}`;
        errorEl.classList.remove("hidden");
    }
}

async function fetchAndRenderOptions(ticker, term, sortBy) {
    const body = document.getElementById("options-body");
    const meta = document.getElementById("options-meta");
    const note = document.getElementById("options-note");
    const error = document.getElementById("options-error");

    if (!body || !meta || !note || !error) return;

    sortBy = sortBy || optionsSortBy;

    error.classList.add("hidden");
    body.innerHTML = "";
    meta.textContent = "Loading…";

    try {
        const res = await fetch(`/api/options?ticker=${encodeURIComponent(ticker)}&term=${encodeURIComponent(term)}&sort_by=${encodeURIComponent(sortBy)}`);
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${res.status}`);
        }
    const payload = await res.json();

    meta.textContent = `${payload.term_label || ""} · Expiry: ${payload.expiry}`;
        note.textContent = payload.note || "";

    const rows = payload.top_options || [];
        body.innerHTML = rows.map(r => `
            <tr>
                <td>${r.option_type || "—"}</td>
                <td>${r.expiry || "—"}</td>
                <td>$${Number(r.strike).toFixed(2)}</td>
                <td>$${Number(r.last).toFixed(2)}</td>
                <td>$${Number(r.bid).toFixed(2)}</td>
                <td>$${Number(r.ask).toFixed(2)}</td>
                <td>${Number(r.volume).toLocaleString()}</td>
                <td>${Number(r.open_interest).toLocaleString()}</td>
                <td>${Number(r.iv).toFixed(1)}%</td>
                <td>${Number(r.delta).toFixed(4)}</td>
                <td>${Number(r.theta).toFixed(4)}</td>
                <td>$${Number(r.spread).toFixed(2)}</td>
            </tr>
        `).join("");
    } catch (err) {
        meta.textContent = "";
        note.textContent = "";
        error.textContent = `⚠️ ${err.message}`;
        error.classList.remove("hidden");
    }
}

// ─── Institutional Ownership ──────────────────────────────
let _ownershipWhaleLoaded    = false;   // track if whale tab already fetched for current ticker
let _ownershipActivityLoaded = false;   // track if activity tab already fetched for current ticker

function fmtVal(v) {
    if (!v) return "—";
    if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
    if (v >= 1e9)  return `$${(v / 1e9).toFixed(2)}B`;
    if (v >= 1e6)  return `$${(v / 1e6).toFixed(2)}M`;
    return `$${Number(v).toLocaleString()}`;
}
function fmtShares(s) {
    if (!s) return "—";
    if (s >= 1e9) return `${(s / 1e9).toFixed(2)}B`;
    if (s >= 1e6) return `${(s / 1e6).toFixed(2)}M`;
    if (s >= 1e3) return `${(s / 1e3).toFixed(1)}K`;
    return String(s);
}

function setupOwnershipTabs() {
    document.querySelectorAll(".own-tab-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".own-tab-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".own-tab-panel").forEach(p => p.classList.add("hidden"));
            btn.classList.add("active");
            const tab = btn.dataset.ownTab;
            document.getElementById(`own-panel-${tab}`)?.classList.remove("hidden");

            // Lazy-load whales tab on first click
            if (tab === "whales" && !_ownershipWhaleLoaded && currentTicker) {
                _ownershipWhaleLoaded = true;
                fetchAndRenderWhaleOwnership(currentTicker);
            }
            // Lazy-load activity tab on first click
            if (tab === "activity" && !_ownershipActivityLoaded && currentTicker) {
                _ownershipActivityLoaded = true;
                fetchAndRenderOwnershipActivity(currentTicker);
            }
        });
    });
}

async function fetchAndRenderOwnership(ticker) {
    const content  = document.getElementById("ownership-content");
    const loading  = document.getElementById("ownership-loading");
    const errorEl  = document.getElementById("ownership-error");
    const meta     = document.getElementById("ownership-meta");
    const summary  = document.getElementById("ownership-summary");
    const instBody = document.getElementById("ownership-inst-body");
    const mfBody   = document.getElementById("ownership-mf-body");
    if (!content) return;

    // Reset whale tab state when ticker changes
    _ownershipWhaleLoaded = false;
    document.getElementById("ownership-whale-body").innerHTML = "";
    document.getElementById("ownership-whale-error").classList.add("hidden");

    // Reset activity tab state when ticker changes
    _ownershipActivityLoaded = false;
    document.getElementById("ownership-activity-content")?.classList.add("hidden");
    document.getElementById("ownership-activity-error")?.classList.add("hidden");
    document.getElementById("ownership-activity-buys").innerHTML  = "";
    document.getElementById("ownership-activity-sells").innerHTML = "";

    // Reset to Institutional tab
    document.querySelectorAll(".own-tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".own-tab-panel").forEach(p => p.classList.add("hidden"));
    document.querySelector('[data-own-tab="institutional"]')?.classList.add("active");
    document.getElementById("own-panel-institutional")?.classList.remove("hidden");

    errorEl.classList.add("hidden");
    content.classList.add("hidden");
    loading.classList.remove("hidden");
    meta.textContent = "";

    try {
        const res = await fetch(`/api/ownership?ticker=${encodeURIComponent(ticker)}`);
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${res.status}`);
        }
        const d = await res.json();

        // ── Summary pills ──────────────────────────────────────────
        const major = d.major_holders || {};
        const pills = Object.entries(major).map(([label, val]) =>
            `<span class="ownership-pill"><strong>${val}</strong> ${label}</span>`
        ).join("");
        summary.innerHTML = pills || "<span class='ownership-pill'>No major-holder summary available</span>";

        function rowsHTML(list, colLabel) {
            if (!list || !list.length)
                return `<tr><td colspan="6" class="no-data">No data available</td></tr>`;
            return list.map((r, i) => `
                <tr>
                    <td>${i + 1}</td>
                    <td class="owner-name">${r.holder || "—"}</td>
                    <td>${fmtShares(r.shares)}</td>
                    <td>${r.pct_out != null ? r.pct_out.toFixed(2) + "%" : "—"}</td>
                    <td>${fmtVal(r.value)}</td>
                    <td class="date-col">${r.date_reported ? r.date_reported.slice(0, 10) : "—"}</td>
                </tr>`).join("");
        }

        instBody.innerHTML = rowsHTML(d.institutional_holders);
        mfBody.innerHTML   = rowsHTML(d.mutualfund_holders);

        const instCount = (d.institutional_holders || []).length;
        const mfCount   = (d.mutualfund_holders || []).length;
        meta.textContent = `${instCount} institutions · ${mfCount} funds · updated ${d.last_updated}`;

        loading.classList.add("hidden");
        content.classList.remove("hidden");
    } catch (err) {
        loading.classList.add("hidden");
        errorEl.textContent = `⚠️ ${err.message}`;
        errorEl.classList.remove("hidden");
    }
}

async function fetchAndRenderWhaleOwnership(ticker) {
    const whaleLoading = document.getElementById("ownership-whale-loading");
    const whaleError   = document.getElementById("ownership-whale-error");
    const whaleBody    = document.getElementById("ownership-whale-body");
    if (!whaleBody) return;

    whaleError.classList.add("hidden");
    whaleLoading.classList.remove("hidden");
    whaleBody.innerHTML = "";

    try {
        const res = await fetch(`/api/ownership/whales?ticker=${encodeURIComponent(ticker)}`);
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${res.status}`);
        }
        const d = await res.json();
        const whales = d.whales || [];

        if (!whales.length) {
            whaleBody.innerHTML = `<tr><td colspan="8" class="no-data">None of the tracked whale investors hold <strong>${ticker}</strong> in their latest 13F filing.</td></tr>`;
        } else {
            whaleBody.innerHTML = whales.map((w, i) => `
                <tr>
                    <td>${i + 1}</td>
                    <td><span class="whale-icon-sm">${w.icon}</span> <strong>${w.name}</strong></td>
                    <td class="owner-name">${w.fund}</td>
                    <td>${fmtShares(w.shares)}</td>
                    <td>${w.value_fmt || fmtVal(w.value_k)}</td>
                    <td>${w.pct_portfolio != null ? w.pct_portfolio.toFixed(2) + "%" : "—"}</td>
                    <td>${w.implied_price_fmt || "—"}</td>
                    <td class="date-col">${w.period || "—"}</td>
                </tr>`).join("");
        }

        whaleLoading.classList.add("hidden");
    } catch (err) {
        whaleLoading.classList.add("hidden");
        whaleError.textContent = `⚠️ ${err.message}`;
        whaleError.classList.remove("hidden");
    }
}

async function fetchAndRenderOwnershipActivity(ticker) {
    const loading  = document.getElementById("ownership-activity-loading");
    const errorEl  = document.getElementById("ownership-activity-error");
    const content  = document.getElementById("ownership-activity-content");
    const buysBody = document.getElementById("ownership-activity-buys");
    const sellsBody= document.getElementById("ownership-activity-sells");
    if (!buysBody) return;

    errorEl.classList.add("hidden");
    content.classList.add("hidden");
    loading.classList.remove("hidden");
    buysBody.innerHTML = "";
    sellsBody.innerHTML = "";

    try {
        const res = await fetch(`/api/ownership/activity?ticker=${encodeURIComponent(ticker)}`);
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.error || `HTTP ${res.status}`);
        }
        const d = await res.json();

        function buildActivityRow(r, i) {
            const actionColors = { NEW: "#22c55e", ADD: "#4ade80", REDUCE: "#f97316", EXIT: "#ef4444" };
            const actionBg     = { NEW: "#14532d", ADD: "#166534", REDUCE: "#431407", EXIT: "#450a0a" };
            const badge = `<span class="whale-action-badge act-${r.action.toLowerCase()}"
                              style="background:${actionBg[r.action]};color:${actionColors[r.action]}">
                              ${r.action}</span>`;
            const pctStr = r.pct_change != null
                ? `<span style="color:${r.pct_change > 0 ? '#4ade80' : '#f87171'}">${r.pct_change > 0 ? '+' : ''}${r.pct_change.toFixed(1)}%</span>`
                : "—";
            const typeTag = r.type === "whale"
                ? `<span class="activity-type-tag whale-tag">🐋 Whale</span>`
                : `<span class="activity-type-tag inst-tag">🏦 Inst.</span>`;
            const sharesSign = r.shares_change > 0 ? "+" : "";
            return `<tr>
                <td>${i + 1}</td>
                <td>
                    <span class="whale-icon-sm">${r.icon}</span>
                    <strong>${r.name}</strong> ${typeTag}
                    <div class="owner-sub">${r.fund !== r.name ? r.fund : ""}</div>
                </td>
                <td>${badge}</td>
                <td class="${r.shares_change > 0 ? 'chg-up' : 'chg-dn'}">${sharesSign}${fmtShares(Math.abs(r.shares_change))}</td>
                <td class="${r.value_change_k > 0 ? 'chg-up' : 'chg-dn'}">${r.value_change_fmt}</td>
                <td>${pctStr}</td>
                <td>${r.action === "EXIT" ? "—" : fmtShares(r.curr_shares)}</td>
                <td>${r.curr_value_fmt || "—"}</td>
                <td>${r.implied_price_fmt || "—"}</td>
                <td class="date-col">${r.period || "—"}</td>
            </tr>`;
        }

        const noRow = (cols, msg) =>
            `<tr><td colspan="${cols}" class="no-data">${msg}</td></tr>`;

        buysBody.innerHTML  = d.buys?.length
            ? d.buys.map((r, i) => buildActivityRow(r, i)).join("")
            : noRow(10, `No institutions/whales added ${ticker} in the most recent 13F cycle.`);

        sellsBody.innerHTML = d.sells?.length
            ? d.sells.map((r, i) => buildActivityRow(r, i)).join("")
            : noRow(10, `No institutions/whales reduced or exited ${ticker} in the most recent 13F cycle.`);

        loading.classList.add("hidden");
        content.classList.remove("hidden");
    } catch (err) {
        loading.classList.add("hidden");
        errorEl.textContent = `⚠️ ${err.message}`;
        errorEl.classList.remove("hidden");
    }
}

function setupChartWidget() {
    const input = document.getElementById("ticker-input");
    const btn = document.getElementById("fetch-chart-btn");
    const watchlistBtn = document.getElementById("watchlist-add-btn");
    const tfButtons = document.querySelectorAll(".tf-btn");
    const sma50 = document.getElementById("sma-50");
    const sma100 = document.getElementById("sma-100");
    const sma200 = document.getElementById("sma-200");
    const optButtons = document.querySelectorAll(".opt-term-btn");

    // attach timeframe clicks
    tfButtons.forEach(b => b.addEventListener("click", (e) => {
        tfButtons.forEach(x => x.classList.remove("active"));
        b.classList.add("active");
        currentPeriod = b.dataset.period;
        if (currentTicker) fetchAndRenderHistory(currentTicker, currentPeriod);
    }));

    // fetch on button click
    btn.addEventListener("click", () => {
        const t = input.value.trim().toUpperCase();
        if (!t) return;
        currentTicker = t;
        fetchAndRenderHistory(currentTicker, currentPeriod);
        _syncWatchlistBtn(t);
    });

    // Also sync button as user types in the input
    input.addEventListener("input", () => {
        const t = input.value.trim().toUpperCase();
        _syncWatchlistBtn(t);
    });
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            const t = input.value.trim().toUpperCase();
            if (!t) return;
            currentTicker = t;
            fetchAndRenderHistory(currentTicker, currentPeriod);
            _syncWatchlistBtn(t);
        }
    });

    // watchlist toggle button
    watchlistBtn.addEventListener("click", () => {
        const t = (currentTicker || input.value.trim()).toUpperCase();
        if (!t) { alert("Search a ticker first, then add it to your watchlist."); return; }
        const inList = _wlLoad().includes(t);
        if (inList) {
            removeFromWatchlist(t);
        } else {
            addToWatchlist(t);
        }
        _syncWatchlistBtn(t);
    });

    // SMA toggles
    const onSmaToggle = () => {
        smaSelections[50] = sma50.checked;
        smaSelections[100] = sma100.checked;
        smaSelections[200] = sma200.checked;
        if (!priceChart) return;
        const values = priceChart.data.datasets[0].data || [];
        updateSmaDatasets(values);
        priceChart.update();
    };

    sma50.addEventListener("change", onSmaToggle);
    sma100.addEventListener("change", onSmaToggle);
    sma200.addEventListener("change", onSmaToggle);

    optButtons.forEach(btnEl => btnEl.addEventListener("click", () => {
        optButtons.forEach(b => b.classList.remove("active"));
        btnEl.classList.add("active");
        optionsTerm = btnEl.dataset.term;
        if (currentTicker) {
            fetchAndRenderOptions(currentTicker, optionsTerm, optionsSortBy);
        }
    }));

    // Sort-by dropdown
    const sortSelect = document.getElementById("options-sort");
    if (sortSelect) {
        sortSelect.addEventListener("change", () => {
            optionsSortBy = sortSelect.value;
            if (currentTicker) {
                fetchAndRenderOptions(currentTicker, optionsTerm, optionsSortBy);
            }
        });
    }

    // support Enter key
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            btn.click();
        }
    });

    // lazy-create chart instance so we don't allocate until used
}

// ─── Build Tab Panel HTML ──────────────────────────────────
function buildPanelHTML(key) {
    return `
        <div id="panel-${key}" class="tab-panel">
            <!-- Loading state -->
            <div id="loading-${key}" class="panel-loading">
                <div class="spinner"></div>
                <p>Fetching RSI data…</p>
            </div>
            <!-- Error state -->
            <div id="error-${key}" class="panel-error hidden">
                <p>⚠️ Failed to load data. <a href="#" onclick="fetchIndex('${key}', true); return false;">Retry</a></p>
            </div>
            <!-- Data state -->
            <div id="data-${key}" class="hidden">
                <!-- Section header -->
                <div class="index-section-header">
                    <h2 id="title-${key}"></h2>
                    <div class="index-meta">
                        <span id="meta-${key}"></span>
                    </div>
                </div>
                <!-- Summary cards -->
                <div class="panel-summary">
                    <div class="summary-card">
                        <span class="summary-label">Stocks Analyzed</span>
                        <span id="total-${key}" class="summary-value">—</span>
                    </div>
                    <div class="summary-card oversold">
                        <span class="summary-label">Oversold (RSI &lt; 30)</span>
                        <span id="oversold-${key}" class="summary-value">—</span>
                    </div>
                    <div class="summary-card overbought">
                        <span class="summary-label">Overbought (RSI &gt; 70)</span>
                        <span id="overbought-${key}" class="summary-value">—</span>
                    </div>
                </div>
                <!-- Top 5 tables -->
                <div class="tables-grid">
                    <section class="table-section lowest">
                        <h2>🟢 Top 5 — Lowest RSI <span class="badge badge-green">Oversold</span></h2>
                        <p class="section-desc">Stocks potentially undervalued — may signal a buying opportunity.</p>
                        <div class="table-wrapper">
                            ${buildTableHTML(`lowest-${key}`)}
                        </div>
                    </section>
                    <section class="table-section highest">
                        <h2>🔴 Top 5 — Highest RSI <span class="badge badge-red">Overbought</span></h2>
                        <p class="section-desc">Stocks potentially overvalued — may signal a selling opportunity.</p>
                        <div class="table-wrapper">
                            ${buildTableHTML(`highest-${key}`)}
                        </div>
                    </section>
                </div>
                <!-- All stocks (collapsible) -->
                <button class="toggle-all-btn" onclick="toggleAllStocks('${key}')">
                    📊 Show All Stocks
                </button>
                <div id="all-container-${key}" class="all-stocks-container">
                    <section class="table-section all-stocks">
                        <h2>All Stocks — Sorted by RSI</h2>
                        <div class="table-wrapper">
                            ${buildTableHTML(`all-${key}`)}
                        </div>
                    </section>
                </div>
            </div>
        </div>`;
}

// ─── Earnings state & helpers ───────────────────────────
let earningsUniverse = "watchlist";
let earningsSortBy   = "earnings_date";
let earningsCache    = null;   // last fetched payload (JS-side, separate from server cache)

function sortEarnings(rows, sortBy) {
    const copy = [...rows];
    switch (sortBy) {
        case "rsi_asc":    return copy.sort((a, b) => (a.rsi ?? 999) - (b.rsi ?? 999));
        case "rsi_desc":   return copy.sort((a, b) => (b.rsi ?? -1)  - (a.rsi ?? -1));
        case "ticker":     return copy.sort((a, b) => a.ticker.localeCompare(b.ticker));
        case "post_move":  return copy.sort((a, b) => {
            const pa = Math.abs(a.post_earnings_move?.pct ?? 0);
            const pb = Math.abs(b.post_earnings_move?.pct ?? 0);
            return pb - pa;
        });
        default:           return copy.sort((a, b) => a.earnings_date.localeCompare(b.earnings_date));
    }
}

function buildEarningsRow(item, idx) {
    // Past EPS/Rev: show last 3 quarters
    const pastRows = item.past_earnings || [];
    let pastHtml = "—";
    if (pastRows.length) {
        pastHtml = pastRows.map(e => {
            const eps = e.eps_actual != null ? `EPS ${e.eps_actual > 0 ? "+" : ""}${e.eps_actual}` : "";
            const rev = e.revenue != null ? ` · Rev ${(e.revenue / 1e9).toFixed(1)}B` : "";
            const surp = e.surprise_pct != null ? ` <span class="${e.surprise_pct >= 0 ? "change-positive" : "change-negative"}">(${e.surprise_pct > 0 ? "+" : ""}${e.surprise_pct?.toFixed(1)}%)</span>` : "";
            return `<div class="earn-row-q"><span class="earn-q-date">${e.date}</span> ${eps}${rev}${surp}</div>`;
        }).join("");
    }

    // Guidance badge
    const guidanceBadge = item.guidance
        ? `<span class="badge badge-green">Yes</span>`
        : `<span style="color:var(--text-muted)">—</span>`;

    // Post-earnings move
    const pem = item.post_earnings_move;
    let pemHtml = "—";
    if (pem) {
        const cls  = pem.direction === "up" ? "change-positive" : (pem.direction === "down" ? "change-negative" : "");
        const sign = pem.pct >= 0 ? "+" : "";
        const arrow = pem.direction === "up" ? "▲" : (pem.direction === "down" ? "▼" : "►");
        pemHtml = `<span class="${cls}">${arrow} ${sign}${pem.pct}% ($${pem.move >= 0 ? "+" : ""}${pem.move})</span><br><small class="earn-pem-date">day after ${pem.earn_date}</small>`;
    }

    const signal = getSignal(item.rsi ?? 50);
    return `
        <tr>
            <td>${idx + 1}</td>
            <td class="ticker-name">${item.ticker}</td>
            <td>${item.earnings_date}</td>
            <td>$${Number(item.price ?? 0).toFixed(2)}</td>
            <td><span class="rsi-value ${getRsiClass(item.rsi ?? 50)}">${item.rsi ?? "—"}</span></td>
            <td><span class="signal ${signal.cls}">${signal.text}</span></td>
            <td class="earn-past-cell">${pastHtml}</td>
            <td>${guidanceBadge}</td>
            <td>${pemHtml}</td>
        </tr>`;
}

function renderEarningsFromCache() {
    if (!earningsCache) return;
    const body      = document.getElementById("earnings-body");
    const tableWrap = document.getElementById("earnings-table-wrap");
    const empty     = document.getElementById("earnings-empty");
    const meta      = document.getElementById("earnings-meta");
    const p = earningsCache;
    const sorted = sortEarnings(p.results || [], earningsSortBy);
    meta.textContent = `${p.count} stock${p.count !== 1 ? "s" : ""} · ${p.week_start} — ${p.week_end} · updated ${p.last_updated || ""}`;
    if (!sorted.length) {
        tableWrap.classList.add("hidden");
        empty.classList.remove("hidden");
    } else {
        empty.classList.add("hidden");
        tableWrap.classList.remove("hidden");
        body.innerHTML = sorted.map((r, i) => buildEarningsRow(r, i)).join("");
    }
}

async function fetchAndRenderEarnings(force = false) {
    const body      = document.getElementById("earnings-body");
    const meta      = document.getElementById("earnings-meta");
    const err       = document.getElementById("earnings-error");
    const loading   = document.getElementById("earnings-loading");
    const tableWrap = document.getElementById("earnings-table-wrap");
    const empty     = document.getElementById("earnings-empty");
    if (!body) return;

    // If we have cached data for this universe and not forcing, just re-sort
    if (!force && earningsCache && earningsCache.universe === earningsUniverse) {
        renderEarningsFromCache();
        return;
    }

    // Show loading state
    err.classList.add("hidden");
    empty.classList.add("hidden");
    tableWrap.classList.add("hidden");
    loading.classList.remove("hidden");
    meta.textContent = "Scanning earnings calendar…";
    body.innerHTML = "";

    try {
        const res = await fetch(`/api/earnings?universe=${encodeURIComponent(earningsUniverse)}`);
        if (!res.ok) {
            const j = await res.json().catch(() => ({}));
            throw new Error(j.error || `HTTP ${res.status}`);
        }
        earningsCache = await res.json();
        earningsLoaded = true;
        renderEarningsFromCache();
        // Populate quick-pick buttons in the hist section
        const tickers = (earningsCache.results || []).map(r => r.ticker);
        populateEarnHistQuickBtns(tickers);
    } catch (e) {
        console.error("Earnings error:", e);
        err.textContent = `⚠️ ${e.message}`;
        err.classList.remove("hidden");
        meta.textContent = "";
    } finally {
        loading.classList.add("hidden");
    }
}

function setupEarningsPanel() {
    // Universe buttons
    document.querySelectorAll(".earn-univ-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".earn-univ-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            earningsUniverse = btn.dataset.universe;
            fetchAndRenderEarnings();
        });
    });
    // Sort dropdown
    const sortSel = document.getElementById("earnings-sort");
    if (sortSel) {
        sortSel.addEventListener("change", () => {
            earningsSortBy = sortSel.value;
            renderEarningsFromCache();  // re-sort in-browser, no re-fetch
        });
    }
    // Refresh button
    const refreshBtn = document.getElementById("earnings-refresh-btn");
    if (refreshBtn) {
        refreshBtn.addEventListener("click", () => fetchAndRenderEarnings(true));
    }
}

// ─── Post-Earnings History ────────────────────────────────

async function fetchEarnHistory(ticker) {
    ticker = (ticker || "").trim().toUpperCase();
    if (!ticker) return;

    const loadEl  = document.getElementById("earn-hist-loading");
    const errEl   = document.getElementById("earn-hist-error");
    const resultEl= document.getElementById("earn-hist-result");

    loadEl.classList.remove("hidden");
    errEl.classList.add("hidden");
    resultEl.classList.add("hidden");

    try {
        const res  = await fetch(`/api/earnings/history?ticker=${encodeURIComponent(ticker)}`);
        const data = await res.json();
        loadEl.classList.add("hidden");

        if (data.error) {
            errEl.textContent = `⚠️ ${data.error}`;
            errEl.classList.remove("hidden");
            return;
        }

        const q     = data.quarters || [];
        const ups   = q.filter(r => r.direction === "up").length;
        const dns   = q.filter(r => r.direction === "down").length;
        const avg   = q.length ? (q.reduce((s, r) => s + r.pct, 0) / q.length) : 0;
        const best  = q.length ? Math.max(...q.map(r => r.pct)) : 0;
        const worst = q.length ? Math.min(...q.map(r => r.pct)) : 0;

        const priceHtml = data.curr_price != null
            ? `<span class="ehist-curr-price">Current Price: <b>$${data.curr_price.toFixed(2)}</b></span>`
            : "";

        document.getElementById("earn-hist-summary").innerHTML = `
            <span class="ehist-name">${data.name} <span class="ehist-ticker">(${data.ticker})</span></span>
            ${priceHtml}
            <span class="ehist-pill up">▲ ${ups} up</span>
            <span class="ehist-pill dn">▼ ${dns} down</span>
            <span class="ehist-pill">Avg: <b class="${avg >= 0 ? 'up' : 'dn'}">${avg >= 0 ? '+' : ''}${avg.toFixed(2)}%</b></span>
            <span class="ehist-pill">Best: <b class="up">+${best.toFixed(2)}%</b></span>
            <span class="ehist-pill">Worst: <b class="dn">${worst.toFixed(2)}%</b></span>
            <span class="ehist-pill">Past ${q.length} quarters</span>
        `;

        const tbody = document.getElementById("earn-hist-body");
        if (!q.length) {
            tbody.innerHTML = `<tr><td colspan="13" class="pol-empty">No historical data available.</td></tr>`;
        } else {
            tbody.innerHTML = q.map((r, i) => {
                const dirBadge = r.direction === "up"
                    ? `<span class="ehist-badge up">▲ Up</span>`
                    : r.direction === "down"
                    ? `<span class="ehist-badge dn">▼ Down</span>`
                    : `<span class="ehist-badge flat">— Flat</span>`;

                const pctCls  = r.pct  > 0 ? "up" : (r.pct  < 0 ? "dn" : "");
                const pctSign = r.pct  > 0 ? "+" : "";
                const movSign = r.move > 0 ? "+" : "";

                // Timing badge: pre-market vs post-market
                const timingBadge = r.timing === "Pre-market"
                    ? `<span class="ehist-timing pre">🌅 Pre</span>`
                    : `<span class="ehist-timing post">🌆 Post</span>`;

                // Pre-earnings: show what date it refers to (previous day or same day)
                const preSubLabel = r.timing === "Pre-market" ? `prior close · ${r.pre_date}` : `same-day close · ${r.pre_date}`;
                const postSubLabel = r.timing === "Pre-market" ? `earn-day close · ${r.post_date}` : `next-day open · ${r.post_date}`;

                // 5-day close + drift
                const d5 = r.drift_5d_pct;
                const d5Html = r.close_5d != null
                    ? `$${r.close_5d.toFixed(2)}<br><small class="${d5 > 0 ? 'up' : (d5 < 0 ? 'dn' : '')}">${d5 > 0 ? '+' : ''}${d5.toFixed(2)}% drift · ${r.d5_date}</small>`
                    : "—";

                // Guidance signal badge + tooltip
                const guidanceMap = {
                    "Raised":     ["up",   "🚀 Raised"],
                    "Reaffirmed": ["flat", "✅ Reaffirmed"],
                    "In-Line":    ["flat", "✅ In-Line"],
                    "Provided":   ["flat", "📋 Provided"],
                    "Cautious":   ["warn", "⚠️ Cautious"],
                    "Mixed":      ["flat", "↔ Mixed"],
                    "Lowered":    ["dn",   "✂️ Lowered"],
                    "Weak / Cut": ["dn",   "✂️ Weak / Cut"],
                };
                const [gCls, gLabel] = guidanceMap[r.guidance_signal] || ["flat", r.guidance_signal || "—"];
                const src = r.guidance_source || "";
                const isRealSec  = src.startsWith("SEC 8-K");
                const isEstSec   = src.startsWith("~Est. via SEC 8-K");
                const srcTag = isRealSec
                    ? `<span class="ehist-guidance-src">📄 SEC 8-K</span>`
                    : isEstSec
                        ? `<span class="ehist-guidance-src estimated">~Est. · SEC 8-K</span>`
                        : `<span class="ehist-guidance-src mktimpl">~Est. (no 8-K)</span>`;
                const snippetHtml = r.guidance_snippet
                    ? `<div class="ehist-guidance-snippet">${r.guidance_snippet.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</div>`
                    : "";
                const guidanceBadge = r.guidance_signal
                    ? `<div class="ehist-guidance-wrap">
                           <span class="ehist-badge ${gCls}">${gLabel}</span>${srcTag}
                           ${snippetHtml ? `<div class="ehist-guidance-tooltip">${snippetHtml}<div class="ehist-guidance-source-line">${r.guidance_source || ""}</div></div>` : ""}
                       </div>`
                    : "—";

                const hasBoth  = r.eps_actual !== null && r.eps_estimate !== null;
                const beatMiss = hasBoth
                    ? (r.eps_actual >= r.eps_estimate
                        ? `<span class="ehist-badge up">Beat</span>`
                        : `<span class="ehist-badge dn">Miss</span>`)
                    : "—";

                return `<tr>
                    <td class="pol-num">${i + 1}</td>
                    <td class="ehist-date">${r.earn_date}<br>${timingBadge}</td>
                    <td class="ehist-price">$${r.pre_close.toFixed(2)}<br><small class="ehist-sub">${preSubLabel}</small></td>
                    <td class="ehist-price">$${r.post_close.toFixed(2)}<br><small class="ehist-sub">${postSubLabel}</small></td>
                    <td class="${pctCls}">${movSign}$${Math.abs(r.move).toFixed(2)}</td>
                    <td class="${pctCls} ehist-pct"><b>${pctSign}${r.pct.toFixed(2)}%</b></td>
                    <td>${dirBadge}</td>
                    <td class="ehist-drift">${d5Html}</td>
                    <td class="ehist-guidance-cell">${guidanceBadge}</td>
                    <td>${r.eps_actual   !== null ? r.eps_actual.toFixed(2)   : "—"}</td>
                    <td>${r.eps_estimate !== null ? r.eps_estimate.toFixed(2) : "—"}</td>
                    <td>${beatMiss}</td>
                </tr>`;
            }).join("");
        }

        resultEl.classList.remove("hidden");
        resultEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (e) {
        loadEl.classList.add("hidden");
        errEl.textContent = `⚠️ Failed to load: ${e.message}`;
        errEl.classList.remove("hidden");
    }
}

function setupEarnHistSection() {
    document.getElementById("earn-hist-search-btn")?.addEventListener("click", () => {
        fetchEarnHistory(document.getElementById("earn-hist-ticker").value);
    });
    document.getElementById("earn-hist-ticker")?.addEventListener("keydown", e => {
        if (e.key === "Enter") fetchEarnHistory(e.target.value);
    });
}

// Populate quick-pick buttons from this week's tickers (called after earnings load)
function populateEarnHistQuickBtns(tickers) {
    const wrap = document.getElementById("earn-hist-quick-btns");
    if (!wrap || !tickers.length) return;
    wrap.innerHTML = tickers.slice(0, 12).map(tkr =>
        `<button class="ehist-quick-btn" data-ticker="${tkr}">${tkr}</button>`
    ).join("");
    wrap.querySelectorAll(".ehist-quick-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.getElementById("earn-hist-ticker").value = btn.dataset.ticker;
            fetchEarnHistory(btn.dataset.ticker);
        });
    });
}

// ─── 13F Whale Tracker ────────────────────────────────────
let whaleKey        = "ackman";
let whaleQuarter    = null;   // null = latest
let whaleCache      = {};     // `${key}:${quarter||"latest"}` → {data, ts}
let whaleQtrCache   = {};     // key → [{accession, report_date, filed}]
const WHALE_TTL     = 60 * 60 * 1000;  // 1 hour client-side TTL

// Format report_date "2026-03-31" → "Q1 2026"
function _qtrLabel(report_date) {
    if (!report_date) return "—";
    const [year, month] = report_date.split("-");
    const q = Math.ceil(parseInt(month) / 3);
    return `Q${q} ${year}`;
}

async function fetchAndRenderWhale(key, quarter = null, force = false) {
    whaleKey     = key;
    whaleQuarter = quarter;

    // Highlight active investor btn
    document.querySelectorAll(".whale-inv-btn").forEach(b => {
        b.classList.toggle("active", b.dataset.key === key);
    });

    const cacheKey = `${key}:${quarter || "latest"}`;
    const now = Date.now();
    if (!force && whaleCache[cacheKey] && (now - whaleCache[cacheKey].ts) < WHALE_TTL) {
        renderWhaleData(whaleCache[cacheKey].data);
        return;
    }

    const loading = document.getElementById("whale-loading");
    const errEl   = document.getElementById("whale-error");
    const content = document.getElementById("whale-content");
    if (loading) loading.classList.remove("hidden");
    if (errEl)   errEl.classList.add("hidden");
    if (content) content.classList.add("hidden");

    try {
        const url = quarter ? `/api/whales/${key}?quarter=${encodeURIComponent(quarter)}` : `/api/whales/${key}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        whaleCache[cacheKey] = { data, ts: Date.now() };
        renderWhaleData(data);

        // load quarter selector if not already loaded for this key
        if (!whaleQtrCache[key]) loadWhaleQuarters(key, data.accession);
    } catch (err) {
        console.error("Whale fetch error:", err);
        if (loading) loading.classList.add("hidden");
        if (errEl) {
            errEl.classList.remove("hidden");
            errEl.textContent = `⚠️ ${err.message}`;
        }
    }
}

async function loadWhaleQuarters(key, activeAccession) {
    try {
        const res = await fetch(`/api/whales/${key}/quarters`);
        if (!res.ok) return;
        const quarters = await res.json();
        whaleQtrCache[key] = quarters;
        renderQuarterSelector(key, quarters, activeAccession);
    } catch (e) {
        console.warn("Could not load quarters:", e);
    }
}

function renderQuarterSelector(key, quarters, activeAccession) {
    const container = document.getElementById("whale-quarter-selector");
    if (!container) return;

    // "Latest" + up to 12 past quarters, then "All" label separator
    const btns = quarters.map((q, i) => {
        const label  = i === 0 ? `${_qtrLabel(q.report_date)} ★` : _qtrLabel(q.report_date);
        const active = q.accession === activeAccession ? " active" : "";
        return `<button class="whale-qtr-btn${active}" data-acc="${q.accession}" data-key="${key}"
                        onclick="switchWhaleQuarter('${key}','${q.accession}')"
                        title="Filed: ${q.filed}">${label}</button>`;
    });
    container.innerHTML = btns.join("");
}

function switchWhaleQuarter(key, accession) {
    // Update active state immediately
    document.querySelectorAll(".whale-qtr-btn").forEach(b => {
        b.classList.toggle("active", b.dataset.acc === accession);
    });
    fetchAndRenderWhale(key, accession);
}


function renderWhaleData(data) {
    const loading = document.getElementById("whale-loading");
    const errEl   = document.getElementById("whale-error");
    const content = document.getElementById("whale-content");
    if (loading) loading.classList.add("hidden");
    if (errEl)   errEl.classList.add("hidden");
    if (content) content.classList.remove("hidden");

    // Info bar
    const setEl = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val || ""; };
    setEl("whale-fund-name",    `${data.icon || ""} ${data.name || ""} — ${data.fund || ""}`);
    setEl("whale-period",       `Q-end: ${data.current_period || ""} · Filed: ${data.filed_date || ""}`);
    setEl("whale-positions",    data.top_buys ? `${(data.top_buys.length + data.top_sells.length)} moves` : "");
    setEl("whale-portfolio-val", `Portfolio: ${data.portfolio_value || ""}`);
    setEl("whale-updated",      `Updated: ${data.last_updated || ""}`);

    // Populate buys
    const buysBody = document.getElementById("whale-buys-body");
    if (buysBody) {
        buysBody.innerHTML = (data.top_buys || []).map((r, i) => buildWhaleRow(r, i + 1)).join("");
    }
    // Populate sells
    const sellsBody = document.getElementById("whale-sells-body");
    if (sellsBody) {
        sellsBody.innerHTML = (data.top_sells || []).map((r, i) => buildWhaleRow(r, i + 1)).join("");
    }
}

function buildWhaleRow(row, idx) {
    const actionClass = {
        NEW:    "action-new",
        ADD:    "action-add",
        REDUCE: "action-reduce",
        EXIT:   "action-exit"
    }[row.action] || "";

    const sharesChg = row.shares_change != null
        ? (row.shares_change > 0 ? `+${row.shares_change.toLocaleString()}` : row.shares_change.toLocaleString())
        : "—";
    const valueChg = row.value_change_fmt || "—";
    const pctChg   = row.pct_change != null
        ? `${row.pct_change > 0 ? "+" : ""}${row.pct_change.toFixed(1)}%`
        : "—";
    // Price per share: for buys show curr price; for exits show prev price
    const priceStr  = row.implied_price_fmt || row.prev_implied_price_fmt || "—";
    const priceChg  = row.price_chg_fmt && row.price_chg_fmt !== "—"
        ? ` <span class="whale-price-chg ${(row.price_chg_pct||0) >= 0 ? 'up' : 'dn'}">${row.price_chg_fmt}</span>`
        : "";
    const currVal   = row.curr_value_fmt || "—";

    // Live price + return since quarter-end
    const nowPrice  = row.current_price_fmt || "—";
    const ticker    = row.ticker && row.ticker !== "—" ? `<span class="whale-ticker">${row.ticker}</span> ` : "";
    const ret       = row.return_since_qend_fmt || "—";
    const retClass  = row.return_since_qend != null
        ? (row.return_since_qend >= 0 ? "whale-ret-up" : "whale-ret-dn")
        : "";

    return `
    <tr>
        <td class="whale-rank">${idx}</td>
        <td class="whale-name">${ticker}${row.name || "—"}</td>
        <td><span class="whale-action-badge ${actionClass}">${row.action || "—"}</span></td>
        <td class="whale-num">${sharesChg}</td>
        <td class="whale-num">${valueChg}</td>
        <td class="whale-num">${pctChg}</td>
        <td class="whale-num whale-price">${priceStr}${priceChg}</td>
        <td class="whale-num">${nowPrice}</td>
        <td class="whale-num ${retClass}">${ret}</td>
        <td class="whale-num">${currVal}</td>
    </tr>`;
}

async function setupWhalesPanel() {
    // Wire up Individual ↔ Consensus toggle buttons (idempotent)
    document.getElementById("whale-view-individual")?.addEventListener("click", () => {
        document.getElementById("whale-view-individual")?.classList.add("active");
        document.getElementById("whale-view-consensus")?.classList.remove("active");
        document.getElementById("whale-selector")?.classList.remove("hidden");
        document.getElementById("whale-quarter-selector")?.classList.remove("hidden");
        document.getElementById("whale-consensus-view")?.classList.add("hidden");
        // Restore individual view visibility
        const wc = document.getElementById("whale-content");
        if (wc && !wc.classList.contains("hidden")) wc.classList.remove("hidden");
    });
    document.getElementById("whale-view-consensus")?.addEventListener("click", () => {
        document.getElementById("whale-view-consensus")?.classList.add("active");
        document.getElementById("whale-view-individual")?.classList.remove("active");
        document.getElementById("whale-selector")?.classList.add("hidden");
        document.getElementById("whale-quarter-selector")?.classList.add("hidden");
        document.getElementById("whale-loading")?.classList.add("hidden");
        document.getElementById("whale-content")?.classList.add("hidden");
        document.getElementById("whale-error")?.classList.add("hidden");
        document.getElementById("whale-consensus-view")?.classList.remove("hidden");
        loadWhaleConsensus();
    });

    // Load investor list
    try {
        const res = await fetch("/api/whales");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const investors = await res.json();

        const selector = document.getElementById("whale-selector");
        if (selector) {
            const funds = investors.filter(i => i.group !== "bank");
            const banks = investors.filter(i => i.group === "bank");
            const makeBtn = inv =>
                `<button class="whale-inv-btn${inv.key === whaleKey ? " active" : ""}"
                         data-key="${inv.key}"
                         onclick="fetchAndRenderWhale('${inv.key}')">
                     ${inv.icon} ${inv.name}
                 </button>`;
            selector.innerHTML =
                funds.map(makeBtn).join("") +
                (banks.length
                    ? `<span class="whale-group-divider">🏦 Banks &amp; Institutions</span>` +
                      banks.map(makeBtn).join("")
                    : "");
        }
        // Load default whale
        fetchAndRenderWhale(whaleKey);
    } catch (e) {
        console.warn("Could not load whale investor list:", e);
    }
}

// ── Consensus heatmap ────────────────────────────────────────────────────
let _consensusLoaded = false;

async function loadWhaleConsensus() {
    if (_consensusLoaded) return;   // already fetched this session

    const loading = document.getElementById("consensus-loading");
    const error   = document.getElementById("consensus-error");
    const content = document.getElementById("consensus-content");

    loading?.classList.remove("hidden");
    error?.classList.add("hidden");
    content?.classList.add("hidden");

    try {
        const res  = await fetch("/api/whales/consensus");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        _consensusLoaded = true;
        loading?.classList.add("hidden");
        content?.classList.remove("hidden");

        // Quarter label
        const ql = document.getElementById("consensus-quarter-label");
        if (ql && data.quarter) ql.textContent = `📅 ${data.quarter}`;
        const qm = document.getElementById("consensus-meta-text");
        if (qm) qm.textContent = `${data.whale_count} investors · updated ${data.updated_at}`;

        _renderConsensusPills(data.consensus);
        _renderConsensusHeatmap(data.investors);
        _renderConsensusTable(data.consensus);

    } catch (e) {
        loading?.classList.add("hidden");
        if (error) { error.classList.remove("hidden"); error.textContent = `Failed: ${e.message}`; }
    }
}

function _renderConsensusPills(consensus) {
    const sellsEl = document.getElementById("consensus-sells-pills");
    const buysEl  = document.getElementById("consensus-buys-pills");
    if (!sellsEl || !buysEl) return;

    const sells = consensus.filter(c => c.net_signal === "strong_sell" || c.net_signal === "sell")
                           .sort((a, b) => b.seller_count - a.seller_count);
    const buys  = consensus.filter(c => c.net_signal === "strong_buy"  || c.net_signal === "buy")
                           .sort((a, b) => b.buyer_count - a.buyer_count);

    sellsEl.innerHTML = sells.map(c => {
        const cls = c.net_signal === "strong_sell" ? "csig-pill-strong-sell" : "csig-pill-sell";
        return `<span class="csig-pill ${cls}" title="${c.sellers.join(', ')}">
            ${c.ticker} <span class="csig-count">${c.seller_count}</span>
        </span>`;
    }).join("") || `<span style="color:var(--text-muted);font-size:0.76rem">None this quarter</span>`;

    buysEl.innerHTML = buys.map(c => {
        const cls = c.net_signal === "strong_buy" ? "csig-pill-strong-buy" : "csig-pill-buy";
        return `<span class="csig-pill ${cls}" title="${c.buyers.join(', ')}">
            ${c.ticker} <span class="csig-count">${c.buyer_count}</span>
        </span>`;
    }).join("") || `<span style="color:var(--text-muted);font-size:0.76rem">None this quarter</span>`;
}

function _consensusTileClass(direction, pct) {
    const abs = Math.abs(pct || 0);
    if (direction === "sell") {
        if (abs >= 90) return "ct-sell-xdark";
        if (abs >= 50) return "ct-sell-dark";
        if (abs >= 20) return "ct-sell-mid";
        return "ct-sell-light";
    } else {
        if (abs >= 200) return "ct-buy-xdark";
        if (abs >= 50)  return "ct-buy-dark";
        if (abs >= 20)  return "ct-buy-mid";
        return "ct-buy-light";
    }
}

function _renderConsensusHeatmap(investors) {
    const el = document.getElementById("consensus-heatmap");
    if (!el) return;

    const rows = (investors || []).map(inv => {
        // Show top 12 moves by magnitude
        const moves = [...(inv.moves || [])]
            .sort((a, b) => Math.abs(b.value_change_k || b.pct_change || 0) - Math.abs(a.value_change_k || a.pct_change || 0))
            .slice(0, 12);

        if (!moves.length) return "";

        const tiles = moves.map(m => {
            const cls   = _consensusTileClass(m.direction, m.pct_change);
            const pctTx = m.pct_change != null
                ? `${m.pct_change > 0 ? "+" : ""}${m.pct_change.toFixed(0)}%`
                : "";
            const tip = [m.name || m.ticker, m.action, pctTx].filter(Boolean).join(" · ");
            return `<div class="consensus-tile ${cls}" title="${tip}">
                <span class="consensus-tile-ticker">${m.ticker}</span>
                ${pctTx ? `<span class="consensus-tile-pct">${pctTx}</span>` : ""}
                <span class="consensus-tile-action">${m.action}</span>
            </div>`;
        }).join("");

        return `<div class="consensus-row">
            <div class="consensus-investor-label">
                ${inv.icon} ${inv.name}
                <small>${inv.fund}</small>
                ${inv.period ? `<small style="color:#6366f1">${inv.period}</small>` : ""}
            </div>
            <div class="consensus-tiles">${tiles}</div>
        </div>`;
    }).filter(Boolean);

    el.innerHTML = rows.join("") || `<div class="consensus-empty">No top position changes were available for the fetched investors.</div>`;
}

function _renderConsensusTable(consensus) {
    const tbody = document.getElementById("consensus-table-body");
    if (!tbody) return;

    const SIG_MAP = {
        "strong_sell": ["csig-badge-strong-sell", "🔴🔴 Strong Sell"],
        "sell":        ["csig-badge-sell",        "🔴 Sell"],
        "neutral":     ["csig-badge-neutral",     "⚪ Neutral"],
        "buy":         ["csig-badge-buy",         "🟢 Buy"],
        "strong_buy":  ["csig-badge-strong-buy",  "🟢🟢 Strong Buy"],
    };

    if (!consensus || !consensus.length) {
        tbody.innerHTML = `<tr><td colspan="6" class="no-data">No tickers had 2+ tracked whales moving in the same 13F cycle.</td></tr>`;
        return;
    }

    tbody.innerHTML = consensus.map(c => {
        const [badgeCls, label] = SIG_MAP[c.net_signal] || ["", c.net_signal];
        const buyerBadges  = c.buyers.map(k  => `<span class="whale-badge">${k}</span>`).join("");
        const sellerBadges = c.sellers.map(k => `<span class="whale-badge">${k}</span>`).join("");
        const conviction = c.conviction > 0 ? "★".repeat(Math.min(c.conviction, 5)) : "mixed";
        return `<tr>
            <td><strong>${c.ticker}</strong>${c.name && c.name !== c.ticker ? `<br><small style="font-weight:400;color:var(--text-muted)">${c.name}</small>` : ""}</td>
            <td><span class="csig-badge ${badgeCls}">${label}</span></td>
            <td>${conviction}</td>
            <td>${buyerBadges  || "—"}</td>
            <td>${sellerBadges || "—"}</td>
            <td style="font-weight:700">${c.total_whales}</td>
        </tr>`;
    }).join("");
}

function toggleAllStocks(key) {
    const container = document.getElementById(`all-container-${key}`);
    const btn = container.previousElementSibling;
    container.classList.toggle("visible");
    btn.textContent = container.classList.contains("visible")
        ? "📊 Hide All Stocks"
        : "📊 Show All Stocks";
}

// ─── Fetch Index Data ──────────────────────────────────────
async function fetchIndex(key, force = false) {
    if (loadedPanels[key] && !force) return;

    const loading = document.getElementById(`loading-${key}`);
    const error = document.getElementById(`error-${key}`);
    const data = document.getElementById(`data-${key}`);

    loading.classList.remove("hidden");
    error.classList.add("hidden");
    data.classList.add("hidden");

    try {
        const res = await fetch(`/api/rsi/${key}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const result = await res.json();

        // Populate tables
        populateTable(`lowest-${key}`, result.lowest_rsi);
        populateTable(`highest-${key}`, result.highest_rsi);
        populateTable(`all-${key}`, result.all_stocks);

        // Title
        document.getElementById(`title-${key}`).textContent = `${result.icon} ${result.name}`;
        document.getElementById(`meta-${key}`).textContent = `Updated: ${result.last_updated}`;

        // Summary
        document.getElementById(`total-${key}`).textContent = result.total_stocks;
        const oversold = result.all_stocks.filter(s => s.rsi < 30).length;
        const overbought = result.all_stocks.filter(s => s.rsi > 70).length;
        document.getElementById(`oversold-${key}`).textContent = oversold;
        document.getElementById(`overbought-${key}`).textContent = overbought;

        // Update global last-updated
        document.getElementById("last-updated").textContent = `Last updated: ${result.last_updated}`;

        loading.classList.add("hidden");
        data.classList.remove("hidden");
        loadedPanels[key] = true;
    } catch (err) {
        console.error(`Failed to fetch ${key}:`, err);
        loading.classList.add("hidden");
        error.classList.remove("hidden");
    }
}

// ─── Tab Switching ─────────────────────────────────────────
function switchTab(key) {
    // Stop PTR live polling when leaving the politician trades tab
    if (_ptrLiveTimer) { clearInterval(_ptrLiveTimer); _ptrLiveTimer = null; }

    // Deactivate all
    document.querySelectorAll(".index-tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));

    // Activate selected
    document.getElementById(`tab-${key}`).classList.add("active");
    document.getElementById(`panel-${key}`).classList.add("active");
    activeTab = key;

    // Lazy-load data
    fetchIndex(key);
}

// ─── Refresh ───────────────────────────────────────────────
async function refreshAll() {
    const btn = document.getElementById("refresh-btn");
    btn.disabled = true;
    btn.textContent = "⏳ Refreshing…";

    // Clear loaded state so they re-fetch
    Object.keys(loadedPanels).forEach(k => delete loadedPanels[k]);

    // Fetch the active tab
    if (activeTab) {
        await fetchIndex(activeTab, true);
    }

    btn.disabled = false;
    btn.textContent = "🔄 Refresh All";
}

// ─── Init ──────────────────────────────────────────────────
async function init() {
        // Seed watchlist from server file before any UI renders
        await _wlSync();

        // setup theme toggle
        setupThemeToggle();
        // setup chart widget event handlers
        try { setupChartWidget(); } catch (e) { console.warn('Chart widget failed to initialize', e); }
        try { setupOwnershipTabs(); } catch (e) { console.warn('Ownership tabs failed to initialize', e); }
        // setup earnings panel controls
        try { setupEarningsPanel(); } catch (e) { console.warn('Earnings panel failed to initialize', e); }
        try { setupEarnHistSection(); } catch (e) { console.warn('Earn-hist section failed to initialize', e); }
        // setup whale panel investor buttons
        try { setupWhalesPanel(); } catch (e) { console.warn('Whale panel failed to initialize', e); }
        try { setupPolTradesPanel(); } catch (e) { console.warn('Pol-trades panel failed to initialize', e); }
    try {
        const res = await fetch("/api/indices");
        indices = await res.json();

        // Build tabs
        const tabsContainer = document.getElementById("index-tabs");
        const contentContainer = document.getElementById("tab-content");

        indices.forEach((idx, i) => {
            // Tab button
            const tab = document.createElement("button");
            tab.className = "index-tab";
            tab.id = `tab-${idx.key}`;
            tab.textContent = `${idx.icon} ${idx.name}`;
            tab.onclick = () => switchTab(idx.key);
            tabsContainer.appendChild(tab);

            // Panel
            contentContainer.insertAdjacentHTML("beforeend", buildPanelHTML(idx.key));
        });

        // Add Earnings tab button
        const earnTab = document.createElement("button");
        earnTab.className = "index-tab";
        earnTab.id = "tab-earnings";
        earnTab.textContent = "🧾 Earnings";
        earnTab.onclick = () => {
            document.querySelectorAll(".index-tab").forEach(t => t.classList.remove("active"));
            earnTab.classList.add("active");
            document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
            const panel = document.getElementById("panel-earnings");
            if (panel) panel.classList.add("active");
            fetchAndRenderEarnings();   // smart: uses JS cache if same universe
        };
        tabsContainer.appendChild(earnTab);

        // Add 13F Whales tab button
        const whaleTab = document.createElement("button");
        whaleTab.className = "index-tab";
        whaleTab.id = "tab-whales";
        whaleTab.textContent = "🐋 13F Whales";
        whaleTab.onclick = () => {
            document.querySelectorAll(".index-tab").forEach(t => t.classList.remove("active"));
            whaleTab.classList.add("active");
            document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
            const panel = document.getElementById("panel-whales");
            if (panel) panel.classList.add("active");
            fetchAndRenderWhale(whaleKey);
        };
        tabsContainer.appendChild(whaleTab);

        // Add Politician Trades tab button
        const polTab = document.createElement("button");
        polTab.className = "index-tab";
        polTab.id = "tab-pol-trades";
        polTab.textContent = "🏛️ Politician Trades";
        polTab.onclick = () => {
            document.querySelectorAll(".index-tab").forEach(t => t.classList.remove("active"));
            polTab.classList.add("active");
            document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
            const panel = document.getElementById("panel-pol-trades");
            if (panel) panel.classList.add("active");
            fetchAndRenderPolTrades();
            // Start live status polling (every 30s while panel is open)
            if (_ptrLiveTimer) clearInterval(_ptrLiveTimer);
            _pollPtrLiveStatus();
            _ptrLiveTimer = setInterval(_pollPtrLiveStatus, 30_000);
        };
        tabsContainer.appendChild(polTab);

        // Add Corporate Holdings tab button
        const corpTab = document.createElement("button");
        corpTab.className = "index-tab";
        corpTab.id = "tab-corp-holdings";
        corpTab.textContent = "🏢 Corp Holdings";
        corpTab.onclick = () => {
            document.querySelectorAll(".index-tab").forEach(t => t.classList.remove("active"));
            corpTab.classList.add("active");
            document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
            const panel = document.getElementById("panel-corp-holdings");
            if (panel) panel.classList.add("active");
            setupCorpHoldingsPanel();
        };
        tabsContainer.appendChild(corpTab);

        // Add Earnings Calendar tab button
        const calTab = document.createElement("button");
        calTab.className = "index-tab";
        calTab.id = "tab-cal";
        calTab.textContent = "📅 Earnings Calendar";
        calTab.onclick = () => {
            document.querySelectorAll(".index-tab").forEach(t => t.classList.remove("active"));
            calTab.classList.add("active");
            document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
            const panel = document.getElementById("panel-cal");
            if (panel) panel.classList.add("active");
            loadEarningsCalendar();
        };
        tabsContainer.appendChild(calTab);

        // Add Sectors tab button
        const sectorsTab = document.createElement("button");
        sectorsTab.className = "index-tab";
        sectorsTab.id = "tab-sectors";
        sectorsTab.textContent = "📊 Sectors";
        sectorsTab.onclick = () => {
            document.querySelectorAll(".index-tab").forEach(t => t.classList.remove("active"));
            sectorsTab.classList.add("active");
            document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
            const panel = document.getElementById("panel-sectors");
            if (panel) panel.classList.add("active");
            setupSectorsPanel();
        };
        tabsContainer.appendChild(sectorsTab);

        // Add Market Events tab button
        const eventsTab = document.createElement("button");
        eventsTab.className = "index-tab";
        eventsTab.id = "tab-events";
        eventsTab.textContent = "📅 Market Events";
        eventsTab.onclick = () => {
            document.querySelectorAll(".index-tab").forEach(t => t.classList.remove("active"));
            eventsTab.classList.add("active");
            document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
            const panel = document.getElementById("panel-events");
            if (panel) panel.classList.add("active");
            loadMarketEvents();
        };
        tabsContainer.appendChild(eventsTab);

        // Add Political Cycle tab button
        const cycleTab = document.createElement("button");
        cycleTab.className  = "index-tab";
        cycleTab.id         = "tab-cycle";
        cycleTab.textContent = "🗳️ Cycle Analysis";
        cycleTab.onclick = () => {
            document.querySelectorAll(".index-tab").forEach(t => t.classList.remove("active"));
            cycleTab.classList.add("active");
            document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
            const panel = document.getElementById("panel-cycle");
            if (panel) panel.classList.add("active");
            setupCyclePanel();
        };
        tabsContainer.appendChild(cycleTab);

        // Activate first tab
        if (indices.length > 0) {
            switchTab(indices[0].key);
        }
    } catch (err) {
        console.error("Failed to load indices:", err);
    }
}

document.addEventListener("DOMContentLoaded", init);

// ════════════════════════════════════════════════════
// WATCHLIST  (localStorage-backed)
// ════════════════════════════════════════════════════
const WL_KEY = "stocks_watchlist_v1";

function _syncWatchlistBtn(ticker) {
    const btn = document.getElementById("watchlist-add-btn");
    if (!btn) return;
    if (!ticker) {
        btn.textContent = "⭐ Watchlist";
        btn.classList.remove("wl-btn-remove");
        return;
    }
    const inList = _wlLoad().includes(ticker);
    if (inList) {
        btn.textContent = "✕ Remove from Watchlist";
        btn.classList.add("wl-btn-remove");
    } else {
        btn.textContent = "⭐ Add to Watchlist";
        btn.classList.remove("wl-btn-remove");
    }
}

// ─── Watchlist backed by stocks_to_track via /api/watchlist ───────────────
// In-memory list kept in sync with the server file.
// localStorage is used only as a fast first-paint cache (avoids a flash of
// empty state before the first fetch completes).

function _wlLoad() {
    try { return JSON.parse(localStorage.getItem(WL_KEY)) || []; }
    catch { return []; }
}
function _wlSave(list) {
    localStorage.setItem(WL_KEY, JSON.stringify(list));
}

/** Fetch the authoritative list from the server and update the local cache. */
async function _wlSync() {
    try {
        const res = await fetch("/api/watchlist");
        if (!res.ok) return;
        const data = await res.json();
        _wlSave(data.tickers);
        return data.tickers;
    } catch { return null; }
}

async function addToWatchlist(ticker) {
    const list = _wlLoad();
    if (list.includes(ticker)) {
        _showWatchlistToast(`${ticker} is already in your watchlist`);
        return;
    }
    // Optimistic local update
    list.push(ticker);
    _wlSave(list);
    _showWatchlistToast(`⭐ ${ticker} added to watchlist`);
    _syncWatchlistBtn(ticker);
    _updateWatchlistTabBadge();
    const panel = document.getElementById("panel-watchlist");
    if (panel && panel.classList.contains("active")) renderWatchlistPanel();

    // Persist to server file
    try {
        const res = await fetch("/api/watchlist", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ticker }),
        });
        if (res.ok) {
            const data = await res.json();
            _wlSave(data.tickers);   // authoritative order from server
            _updateWatchlistTabBadge();
        }
    } catch { /* keep optimistic state */ }
}

async function removeFromWatchlist(ticker) {
    // Optimistic local update
    const list = _wlLoad().filter(t => t !== ticker);
    _wlSave(list);
    _showWatchlistToast(`Removed ${ticker} from watchlist`);
    _syncWatchlistBtn(ticker);
    const card = document.getElementById(`wl-card-${ticker}`);
    if (card) card.remove();
    _checkWatchlistEmpty();
    _updateWatchlistTabBadge();

    // Persist to server file
    try {
        const res = await fetch(`/api/watchlist/${ticker}`, { method: "DELETE" });
        if (res.ok) {
            const data = await res.json();
            _wlSave(data.tickers);
            _updateWatchlistTabBadge();
        }
    } catch { /* keep optimistic state */ }
}

function _updateWatchlistTabBadge() {
    const btn = document.getElementById("tab-watchlist");
    if (!btn) return;
    const count = _wlLoad().length;
    btn.textContent = count > 0 ? `⭐ Watchlist (${count})` : "⭐ Watchlist";
}

function _checkWatchlistEmpty() {
    const grid = document.getElementById("watchlist-grid");
    const empty = document.getElementById("watchlist-empty");
    if (!grid || !empty) return;
    const hasCards = grid.querySelectorAll(".wl-card").length > 0;
    empty.classList.toggle("hidden", hasCards);
}

function _showWatchlistToast(msg) {
    let toast = document.getElementById("wl-toast");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "wl-toast";
        toast.className = "wl-toast";
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.classList.add("show");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => toast.classList.remove("show"), 2500);
}

async function renderWatchlistPanel() {
    const grid = document.getElementById("watchlist-grid");
    const empty = document.getElementById("watchlist-empty");
    if (!grid) return;
    const list = _wlLoad();
    if (list.length === 0) {
        grid.innerHTML = "";
        if (empty) empty.classList.remove("hidden");
        return;
    }
    if (empty) empty.classList.add("hidden");

    // Render skeleton cards first, then fill with live data
    grid.innerHTML = list.map(t => `
        <div class="wl-card" id="wl-card-${t}">
            <div class="wl-card-top">
                <span class="wl-ticker">${t}</span>
                <button class="wl-remove-btn" onclick="removeFromWatchlist('${t}')" title="Remove">✕</button>
            </div>
            <div class="wl-price">—</div>
            <div class="wl-change">—</div>
            <div class="wl-details">
                <span class="wl-label">RSI</span><span class="wl-val wl-rsi" id="wl-rsi-${t}">—</span>
                <span class="wl-label">Mkt Cap</span><span class="wl-val" id="wl-cap-${t}">—</span>
                <span class="wl-label">Vol</span><span class="wl-val" id="wl-vol-${t}">—</span>
                <span class="wl-label">52W</span><span class="wl-val" id="wl-52w-${t}">—</span>
            </div>
            <button class="wl-chart-btn" onclick="loadTickerFromWatchlist('${t}')">📈 Open Chart</button>
        </div>
    `).join("");

    // Fetch live quote data for each ticker in parallel
    await Promise.all(list.map(t => _fetchWatchlistCard(t)));
}

async function _fetchWatchlistCard(ticker) {
    try {
        const res = await fetch(`/api/chart?ticker=${ticker}&period=5d&interval=1d`);
        if (!res.ok) throw new Error("API error");
        const data = await res.json();

        const card = document.getElementById(`wl-card-${ticker}`);
        if (!card) return;

        const prices = data.prices || [];
        const curr = prices.length ? prices[prices.length - 1] : null;
        const prev = prices.length > 1 ? prices[prices.length - 2] : null;

        // Price + change
        const priceEl = card.querySelector(".wl-price");
        const changeEl = card.querySelector(".wl-change");
        if (curr != null) {
            priceEl.textContent = `$${curr.toFixed(2)}`;
            if (prev != null) {
                const chg = curr - prev;
                const pct = (chg / prev * 100);
                const sign = chg >= 0 ? "+" : "";
                changeEl.textContent = `${sign}${chg.toFixed(2)} (${sign}${pct.toFixed(2)}%)`;
                changeEl.className = `wl-change ${chg >= 0 ? "up" : "dn"}`;
            }
        }

        // RSI from data if available
        const rsiEl = document.getElementById(`wl-rsi-${ticker}`);
        if (rsiEl && data.rsi != null) {
            const rsi = parseFloat(data.rsi).toFixed(1);
            rsiEl.textContent = rsi;
            rsiEl.className = `wl-val wl-rsi ${rsi >= 70 ? "rsi-ob" : rsi <= 30 ? "rsi-os" : ""}`;
        }

        // Extra info from /api/quote if available
        const qres = await fetch(`/api/quote?ticker=${ticker}`);
        if (qres.ok) {
            const q = await qres.json();
            const capEl  = document.getElementById(`wl-cap-${ticker}`);
            const volEl  = document.getElementById(`wl-vol-${ticker}`);
            const w52El  = document.getElementById(`wl-52w-${ticker}`);
            if (capEl && q.market_cap)  capEl.textContent  = _fmtBig(q.market_cap);
            if (volEl && q.volume)      volEl.textContent  = _fmtBig(q.volume);
            if (w52El && q.week52_high && q.week52_low)
                w52El.textContent = `$${q.week52_low}–$${q.week52_high}`;
            // Company name as subtitle
            if (q.name) {
                const nameEl = card.querySelector(".wl-ticker");
                nameEl.title = q.name;
            }
        }
    } catch (e) {
        console.warn(`Watchlist card fetch failed for ${ticker}:`, e);
    }
}

function _fmtBig(n) {
    if (n == null) return "—";
    if (n >= 1e12) return `$${(n/1e12).toFixed(2)}T`;
    if (n >= 1e9)  return `$${(n/1e9).toFixed(2)}B`;
    if (n >= 1e6)  return `$${(n/1e6).toFixed(2)}M`;
    return `$${n.toLocaleString()}`;
}

function loadTickerFromWatchlist(ticker) {
    // Switch to first RSI tab (or just focus chart widget)
    currentTicker = ticker;
    document.getElementById("ticker-input").value = ticker;
    fetchAndRenderHistory(ticker, currentPeriod);
    // Scroll to chart widget
    document.getElementById("chart-widget").scrollIntoView({ behavior: "smooth" });
}

// ════════════════════════════════════════════════════
// CORPORATE EQUITY HOLDINGS
// ════════════════════════════════════════════════════
let _corpData        = null;
let _corpKey         = null;
let _corpInited      = false;
let _corpYearFilter  = "all";
let _corpMonthFilter = new Set();
let _corpSearchQ     = "";

// Sort state per table: { col, dir }
const _corpSort = {
    history: { col: "date",             dir: -1 },
    active:  { col: "last_event_date",  dir: -1 },
    exited:  { col: "last_event_date",  dir: -1 },
};

function _corpSortArr(arr, tableKey) {
    const { col, dir } = _corpSort[tableKey];
    return [...arr].sort((a, b) => {
        let av = a[col] ?? "", bv = b[col] ?? "";
        // Numeric cols
        if (col === "curr_price" || col === "pct" || col === "total_invested") {
            av = parseFloat(av) || 0;
            bv = parseFloat(bv) || 0;
            return dir * (av - bv);
        }
        return dir * String(av).localeCompare(String(bv));
    });
}

function _corpUpdateSortHeaders(tableKey) {
    const tableId = { history: "corp-history-table", active: "corp-active-table", exited: "corp-exited-table" }[tableKey];
    const { col, dir } = _corpSort[tableKey];
    document.querySelectorAll(`#${tableId} .corp-sortable`).forEach(th => {
        th.classList.remove("sort-asc", "sort-desc");
        const arrow = th.querySelector(".sort-arrow");
        if (arrow) arrow.textContent = "↕";
        if (th.dataset.col === col) {
            th.classList.add(dir === 1 ? "sort-asc" : "sort-desc");
            if (arrow) arrow.textContent = dir === 1 ? "↑" : "↓";
        }
    });
}

const CORP_ACTION_BADGE = {
    Acquired:  ["corp-act-acquired",  "Acquired"],
    IPO:       ["corp-act-ipo",       "IPO"],
    Increased: ["corp-act-inc",       "▲ Increased"],
    Reduced:   ["corp-act-red",       "▼ Reduced"],
    Sold:      ["corp-act-sold",      "Sold"],
    Exited:    ["corp-act-sold",      "Exited"],
};

function _corpBadge(action) {
    const [cls, label] = CORP_ACTION_BADGE[action] || ["corp-act-other", action];
    return `<span class="corp-act-badge ${cls}">${label}</span>`;
}

function buildCorpHistoryRow(idx, ev) {
    const tickerHtml = ev.ticker
        ? `<a href="https://finance.yahoo.com/quote/${ev.ticker}" target="_blank" class="pol-ticker">${ev.ticker}</a>`
        : `<span class="pol-no-ticker">—</span>`;
    const pctHtml = ev.pct ? `${parseFloat(ev.pct).toFixed(1)}%` : "—";
    return `<tr>
        <td class="pol-num">${idx}</td>
        <td class="corp-ev-date">${ev.date}</td>
        <td class="corp-ev-target">${ev.target}</td>
        <td class="pol-ticker-cell">${tickerHtml}</td>
        <td class="corp-ev-action">${_corpBadge(ev.action)}</td>
        <td class="corp-ev-pct">${pctHtml}</td>
        <td class="corp-ev-price">${ev.curr_price_fmt}</td>
        <td class="corp-ev-note" title="${ev.note}">${ev.note.length > 80 ? ev.note.slice(0,78)+"…" : ev.note}</td>
    </tr>`;
}

function buildCorpHoldingRow(idx, h, tableType) {
    const tickerHtml = h.ticker
        ? `<a href="https://finance.yahoo.com/quote/${h.ticker}" target="_blank" class="pol-ticker">${h.ticker}</a>`
        : `<span class="pol-no-ticker">—</span>`;
    const pctHtml  = h.pct ? `${parseFloat(h.pct).toFixed(1)}%` : "—";
    const statusCls = h.status === "exited" ? "corp-status-exit"
                    : h.status === "partial" ? "corp-status-partial"
                    : "corp-status-active";
    const statusLabel = h.status === "exited" ? "Exited"
                      : h.status === "partial" ? "Partial"
                      : "Active";

    if (tableType === "active") {
        return `<tr>
            <td class="pol-num">${idx}</td>
            <td class="corp-ev-target">${h.target}</td>
            <td class="pol-ticker-cell">${tickerHtml}</td>
            <td class="corp-ev-date">${h.first_event_date}</td>
            <td class="corp-ev-date">${h.last_event_date}</td>
            <td class="corp-ev-amt">${h.total_invested_fmt}</td>
            <td class="corp-ev-pct">${pctHtml}</td>
            <td class="corp-ev-price">${h.curr_price_fmt}</td>
            <td><span class="corp-status-badge ${statusCls}">${statusLabel}</span></td>
        </tr>`;
    } else {
        return `<tr>
            <td class="pol-num">${idx}</td>
            <td class="corp-ev-target">${h.target}</td>
            <td class="pol-ticker-cell">${tickerHtml}</td>
            <td class="corp-ev-date">${h.first_event_date}</td>
            <td class="corp-ev-date">${h.last_event_date}</td>
            <td class="corp-ev-amt">${h.total_invested_fmt}</td>
            <td class="corp-ev-price">${h.curr_price_fmt}</td>
            <td><span class="corp-status-badge ${statusCls}">${statusLabel}</span></td>
        </tr>`;
    }
}

function applyCorpFilters() {
    if (!_corpData) return;
    const q    = _corpSearchQ.toLowerCase().trim();
    const year = _corpYearFilter;

    // Filter history events
    let history = _corpData.history || [];
    if (year !== "all") history = history.filter(ev => ev.date.startsWith(year));
    if (_corpMonthFilter.size > 0) {
        history = history.filter(ev => {
            const m = parseInt((ev.date.split("-")[1] || "0"), 10);
            return _corpMonthFilter.has(m);
        });
    }
    if (q) history = history.filter(ev =>
        ev.target.toLowerCase().includes(q) ||
        ev.ticker.toLowerCase().includes(q)
    );

    // Active / exited always show full list (no date filter — show summary)
    let active = (_corpData.active || []).filter(h =>
        !q || h.target.toLowerCase().includes(q) || h.ticker.toLowerCase().includes(q)
    );
    let exited = (_corpData.exited || []).filter(h =>
        !q || h.target.toLowerCase().includes(q) || h.ticker.toLowerCase().includes(q)
    );

    // Apply sort
    history = _corpSortArr(history, "history");
    active  = _corpSortArr(active,  "active");
    exited  = _corpSortArr(exited,  "exited");

    // Render history
    const hBody = document.getElementById("corp-history-body");
    if (hBody) {
        if (history.length === 0) {
            hBody.innerHTML = `<tr><td colspan="8" class="pol-empty">No events match the selected filters.</td></tr>`;
        } else {
            hBody.innerHTML = history.map((ev, i) => buildCorpHistoryRow(i+1, ev)).join("");
        }
    }
    const title = document.getElementById("corp-history-title");
    if (title) title.textContent = `📅 Transaction History (${history.length} events)`;

    // Render active
    const aBody = document.getElementById("corp-active-body");
    if (aBody) {
        if (active.length === 0) {
            aBody.innerHTML = `<tr><td colspan="9" class="pol-empty">No active holdings.</td></tr>`;
        } else {
            aBody.innerHTML = active.map((h, i) => buildCorpHoldingRow(i+1, h, "active")).join("");
        }
    }

    // Render exited
    const eBody = document.getElementById("corp-exited-body");
    if (eBody) {
        if (exited.length === 0) {
            eBody.innerHTML = `<tr><td colspan="8" class="pol-empty">No exited positions.</td></tr>`;
        } else {
            eBody.innerHTML = exited.map((h, i) => buildCorpHoldingRow(i+1, h, "exited")).join("");
        }
    }

    const meta = document.getElementById("corp-meta");
    if (meta) meta.textContent = `${history.length} events · ${active.length} active · ${exited.length} exited`;
}

async function fetchAndRenderCorpHoldings(key) {
    if (!key) return;
    _corpKey = key;

    document.querySelectorAll(".corp-sel-btn").forEach(b =>
        b.classList.toggle("active", b.dataset.key === key)
    );

    const loading = document.getElementById("corp-loading");
    const errDiv  = document.getElementById("corp-error");
    const content = document.getElementById("corp-content");

    loading?.classList.remove("hidden");
    errDiv?.classList.add("hidden");
    content?.classList.add("hidden");

    try {
        const res  = await fetch(`/api/corp-holdings/${key}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Server error");

        _corpData = data;

        // Update info bar
        document.getElementById("corp-company").textContent      = `🏢 ${data.investor} (${data.ticker})`;
        document.getElementById("corp-active-count").textContent = `📈 ${data.active.length} Active`;
        document.getElementById("corp-exited-count").textContent = `📉 ${data.exited.length} Exited / Reduced`;
        document.getElementById("corp-updated").textContent      = `Updated ${new Date(data.updated_at).toLocaleTimeString()}`;

        content?.classList.remove("hidden");
        applyCorpFilters();
    } catch (err) {
        errDiv.textContent = `⚠️ ${err.message}`;
        errDiv?.classList.remove("hidden");
    } finally {
        loading?.classList.add("hidden");
    }
}

async function setupCorpHoldingsPanel() {
    if (_corpInited) return;
    _corpInited = true;

    // Build company selector
    try {
        const res       = await fetch("/api/corp-holdings");
        const companies = await res.json();
        const sel       = document.getElementById("corp-selector");
        if (sel) {
            sel.innerHTML = companies.map(c =>
                `<button class="corp-sel-btn" data-key="${c.key}" style="--corp-color:${c.color}">
                    <span class="corp-sel-ticker">${c.ticker}</span>
                    <span class="corp-sel-name">${c.name}</span>
                </button>`
            ).join("");
            sel.querySelectorAll(".corp-sel-btn").forEach(btn =>
                btn.addEventListener("click", () => fetchAndRenderCorpHoldings(btn.dataset.key))
            );
        }
        // Load NVIDIA by default
        if (companies.length > 0) fetchAndRenderCorpHoldings(companies[0].key);
    } catch (e) {
        console.error("Corp selector failed:", e);
    }

    // Year filter
    document.getElementById("corp-year-select")?.addEventListener("change", e => {
        _corpYearFilter = e.target.value;
        applyCorpFilters();
    });

    // Month multi-select
    document.querySelectorAll(".corp-month-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const m = parseInt(btn.dataset.month, 10);
            if (m === 0) {
                _corpMonthFilter.clear();
                document.querySelectorAll(".corp-month-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
            } else {
                document.querySelector('.corp-month-btn[data-month="0"]')?.classList.remove("active");
                if (_corpMonthFilter.has(m)) { _corpMonthFilter.delete(m); btn.classList.remove("active"); }
                else                         { _corpMonthFilter.add(m);    btn.classList.add("active"); }
                if (_corpMonthFilter.size === 0)
                    document.querySelector('.corp-month-btn[data-month="0"]')?.classList.add("active");
            }
            applyCorpFilters();
        });
    });

    // Search
    let _st;
    document.getElementById("corp-search")?.addEventListener("input", e => {
        clearTimeout(_st);
        _corpSearchQ = e.target.value;
        _st = setTimeout(applyCorpFilters, 250);
    });

    // Sortable column headers for all 3 tables
    ["corp-history-table", "corp-active-table", "corp-exited-table"].forEach(tableId => {
        document.querySelector(`#${tableId} thead`)?.addEventListener("click", e => {
            const th = e.target.closest(".corp-sortable");
            if (!th) return;
            const col = th.dataset.col;
            const tbl = th.dataset.table; // "history" | "active" | "exited"
            if (_corpSort[tbl].col === col) {
                _corpSort[tbl].dir *= -1;
            } else {
                _corpSort[tbl].col = col;
                _corpSort[tbl].dir = (col.includes("date") || col === "curr_price" || col === "total_invested") ? -1 : 1;
            }
            _corpUpdateSortHeaders(tbl);
            applyCorpFilters();
        });
    });

    // Init header arrows to match default sort
    ["history", "active", "exited"].forEach(t => _corpUpdateSortHeaders(t));
}

let _polTradesCache  = null;      // raw trades array (all, for year)
let _polTradesYear   = null;      // year the cache is for
let _polTypeFilter   = "all";     // "all" | "purchase" | "sale"
let _polAssetFilter  = "tradeable"; // "all" | "tradeable" | "stock" | "option"
let _polMonthFilter  = new Set(); // empty = all months; numbers 1-12 = selected
let _polSortCol      = "tx_date"; // current sort column key
let _polSortDir      = -1;        // -1 = desc, 1 = asc
let _polTradesInited = false;
let _ptrLiveTimer    = null;   // setInterval handle for live status polling
let _ptrLastNewCount = 0;      // track when server sees new filings
let _polAlertsCache  = [];        // suspicious alerts from last API call

// Amount range → numeric midpoint for sorting
function _polAmountMid(amount) {
    const nums = (amount || "").replace(/,/g, "").match(/\d+/g);
    if (!nums) return 0;
    if (nums.length === 1) return parseInt(nums[0]);
    return (parseInt(nums[0]) + parseInt(nums[1])) / 2;
}

function buildPolTradeRow(idx, t) {
    const isBuy  = t.type.toLowerCase().includes("purchase");
    const isSell = t.type.toLowerCase().includes("sale");

    const typeBadge = isBuy
        ? `<span class="pol-badge pol-buy">Buy</span>`
        : isSell
            ? `<span class="pol-badge pol-sell">Sell</span>`
            : `<span class="pol-badge pol-other">${t.type}</span>`;

    const ownerLabel = { SP: "Spouse", JT: "Joint", DC: "Child" }[t.owner] || "Self";

    const tickerHtml = t.ticker
        ? `<a href="https://finance.yahoo.com/quote/${t.ticker}" target="_blank" class="pol-ticker">${t.ticker}</a>`
        : `<span class="pol-no-ticker">—</span>`;

    // Asset type badge
    const at = (t.asset_type || "other").toLowerCase();
    const atLabel = { stock: "Stock", option: "Option", fund: "Fund", bond: "Bond", other: "Other" }[at] || "Other";
    const assetBadge = `<span class="pol-asset-badge pol-asset-${at}">${atLabel}</span>`;

    // Flag cell for suspicious / live
    const isLive = !!t.live;
    const isSusp = !!t.suspicious;
    const flagCell = (isSusp || isLive)
        ? `<td class="pol-flag-cell">${isSusp ? '<span class="pol-suspicious-flag" title="Suspicious: large option trade">⚠️</span>' : ''}${isLive ? '<span title="Brand-new live filing">🔴</span>' : ''}</td>`
        : `<td class="pol-flag-cell"></td>`;

    let rowClass = "";
    if (isSusp && isLive) rowClass = ' class="pol-row-suspicious pol-row-live"';
    else if (isSusp)      rowClass = ' class="pol-row-suspicious"';
    else if (isLive)      rowClass = ' class="pol-row-live"';

    return `<tr${rowClass}>
        ${flagCell}
        <td class="pol-num">${idx}</td>
        <td class="pol-member">${t.member}</td>
        <td class="pol-state">${t.state_dist || "—"}</td>
        <td class="pol-asset" title="${t.asset}">${t.asset.length > 35 ? t.asset.slice(0, 33) + "…" : t.asset}</td>
        <td class="pol-ticker-cell">${tickerHtml}</td>
        <td class="pol-type">${assetBadge}</td>
        <td class="pol-owner">${ownerLabel}</td>
        <td class="pol-type">${typeBadge}</td>
        <td class="pol-date">${t.tx_date || "—"}</td>
        <td class="pol-notif">${t.notif_date || "—"}</td>
        <td class="pol-amount">${t.amount || "—"}</td>
    </tr>`;
}

function renderPolTrades(trades, totalBeforeAssetFilter) {
    const tbody = document.getElementById("pol-trades-body");
    if (!trades || trades.length === 0) {
        let hint = "";
        if (totalBeforeAssetFilter > 0 && _polAssetFilter !== "all") {
            // Trades exist for the search term, but the asset-type filter is hiding them
            const filterLabel = { tradeable: "Tradeable", stock: "Stocks", option: "Options", fund: "Funds" }[_polAssetFilter] || _polAssetFilter;
            hint = `<br><span style="font-size:0.8rem;color:#94a3b8;">
                Found ${totalBeforeAssetFilter} trade(s) for this search, but none match the <b>${filterLabel}</b> filter.
                &nbsp;<span style="color:#a78bfa;cursor:pointer;text-decoration:underline;"
                    onclick="document.querySelector('.pol-asset-btn[data-asset=all]').click()">
                    Show All Assets
                </span>
            </span>`;
        } else if (totalBeforeAssetFilter === 0) {
            // No trades at all — probably not in the index yet
            hint = `<br><span style="font-size:0.8rem;color:#94a3b8;">
                Not in the House Clerk index yet.
                New filings take up to a few hours to appear in the bulk index.
                The live monitor will detect it automatically — or hit
                <b>↻ Refresh</b> to re-check now.
            </span>`;
        }
        tbody.innerHTML = `<tr><td colspan="12" class="pol-empty">No trades found for selected filters.${hint}</td></tr>`;
        return;
    }
    tbody.innerHTML = trades.map((t, i) => buildPolTradeRow(i + 1, t)).join("");
}

function _renderSuspiciousAlerts(alerts) {
    const section = document.getElementById("pol-suspicious-alerts");
    const cards   = document.getElementById("pol-suspicious-cards");
    const count   = document.getElementById("pol-suspicious-count");
    if (!section || !cards) return;

    if (!alerts || alerts.length === 0) {
        section.classList.add("hidden");
        return;
    }

    count.textContent = `${alerts.length} flagged`;
    cards.innerHTML = alerts.map(t => {
        const ticker = t.ticker || "N/A";
        const yfLink = t.ticker
            ? `<a href="https://finance.yahoo.com/quote/${t.ticker}" target="_blank" class="psc-link psc-link-yf">📈 Yahoo</a>`
            : "";
        const pdfLink = t.pdf_url
            ? `<a href="${t.pdf_url}" target="_blank" class="psc-link psc-link-pdf">📄 PDF</a>`
            : "";
        const isBuy  = t.type.toLowerCase().includes("purchase");
        const txLabel = isBuy ? "Option Purchase" : "Option Sale";
        return `<div class="pol-suspicious-card">
            <div class="psc-ticker">${ticker}</div>
            <div class="psc-body">
                <div class="psc-member">⚠️ ${t.member}</div>
                <div class="psc-detail">${txLabel} · ${t.tx_date || "—"} · Notif: ${t.notif_date || "—"}</div>
                <div class="psc-detail" style="font-style:italic;color:#94a3b8;">${t.asset.length > 70 ? t.asset.slice(0, 68) + "…" : t.asset}</div>
            </div>
            <div class="psc-amount">${t.amount || "—"}</div>
            <div class="psc-links">${yfLink}${pdfLink}</div>
        </div>`;
    }).join("");
    section.classList.remove("hidden");
}

function _polSortTrades(trades) {
    const col = _polSortCol;
    const dir = _polSortDir;
    return [...trades].sort((a, b) => {
        let av = a[col] ?? "", bv = b[col] ?? "";
        if (col === "amount") { av = _polAmountMid(av); bv = _polAmountMid(bv); }
        if (typeof av === "number") return dir * (av - bv);
        return dir * String(av).localeCompare(String(bv));
    });
}

function _polUpdateSortHeaders() {
    document.querySelectorAll(".pol-th.sortable").forEach(th => {
        th.classList.remove("sort-asc", "sort-desc");
        const arrow = th.querySelector(".sort-arrow");
        if (arrow) arrow.textContent = "↕";
        if (th.dataset.col === _polSortCol) {
            th.classList.add(_polSortDir === 1 ? "sort-asc" : "sort-desc");
            if (arrow) arrow.textContent = _polSortDir === 1 ? "↑" : "↓";
        }
    });
}

function applyPolFilters() {
    if (!_polTradesCache) return;
    const q = (document.getElementById("pol-search")?.value || "").toLowerCase().trim();

    let filtered = _polTradesCache;

    // Type filter
    if (_polTypeFilter !== "all") {
        filtered = filtered.filter(t => t.type.toLowerCase().includes(_polTypeFilter));
    }

    // Month filter (multi-select)
    if (_polMonthFilter.size > 0) {
        filtered = filtered.filter(t => {
            if (!t.tx_date) return false;
            const month = parseInt(t.tx_date.split("-")[1], 10);
            return _polMonthFilter.has(month);
        });
    }

    // Text search (applied BEFORE asset type filter so we can detect cross-type matches)
    if (q) {
        filtered = filtered.filter(t =>
            t.member.toLowerCase().includes(q) ||
            t.asset.toLowerCase().includes(q) ||
            (t.ticker && t.ticker.toLowerCase().includes(q))
        );
    }

    // Count before asset type filter (for zero-results hint)
    const totalBeforeAssetFilter = filtered.length;

    // Asset type filter
    if (_polAssetFilter === "tradeable") {
        filtered = filtered.filter(t => t.tradeable === true);
    } else if (_polAssetFilter !== "all") {
        filtered = filtered.filter(t => (t.asset_type || "other") === _polAssetFilter);
    }

    // Sort
    filtered = _polSortTrades(filtered);

    renderPolTrades(filtered, totalBeforeAssetFilter);

    const meta = document.getElementById("pol-trades-meta");
    if (meta) meta.textContent = `${filtered.length.toLocaleString()} trades shown`;
}

async function fetchAndRenderPolTrades(forceRefresh = false) {
    const year = document.getElementById("pol-year-select")?.value || new Date().getFullYear();

    if (!forceRefresh && _polTradesCache !== null && _polTradesYear === String(year)) {
        applyPolFilters();
        _renderSuspiciousAlerts(_polAlertsCache);
        return;
    }

    const loading = document.getElementById("pol-trades-loading");
    const errDiv  = document.getElementById("pol-trades-error");
    const content = document.getElementById("pol-trades-content");
    const stats   = document.getElementById("pol-trades-stats");
    const alertsSec = document.getElementById("pol-suspicious-alerts");
    const refreshBtn = document.getElementById("pol-refresh-btn");

    loading?.classList.remove("hidden");
    errDiv?.classList.add("hidden");
    content?.classList.add("hidden");
    stats?.classList.add("hidden");
    alertsSec?.classList.add("hidden");
    if (refreshBtn) { refreshBtn.disabled = true; refreshBtn.textContent = "⏳ Fetching…"; }

    try {
        // force=1 tells the backend to bypass its ZIP-index cache and re-download
        const forceParam = forceRefresh ? "&force=1" : "";
        const res  = await fetch(`/api/politician-trades?year=${year}${forceParam}`);
        const data = await res.json();

        if (!res.ok) throw new Error(data.error || "Server error");

        _polTradesCache = data.trades || [];
        _polAlertsCache = data.alerts || [];
        _polTradesYear  = String(year);

        // Stats bar
        const buys    = _polTradesCache.filter(t => t.type.toLowerCase().includes("purchase")).length;
        const sells   = _polTradesCache.filter(t => t.type.toLowerCase().includes("sale")).length;
        const members = new Set(_polTradesCache.map(t => t.member)).size;

        document.getElementById("pol-stat-total").textContent   = `📊 ${_polTradesCache.length.toLocaleString()} Trades`;
        document.getElementById("pol-stat-buys").textContent    = `🟢 ${buys.toLocaleString()} Buys`;
        document.getElementById("pol-stat-sells").textContent   = `🔴 ${sells.toLocaleString()} Sells`;
        document.getElementById("pol-stat-members").textContent = `👤 ${members} Members`;

        stats?.classList.remove("hidden");
        content?.classList.remove("hidden");

        // Show fetch timestamp + source link
        const meta = document.getElementById("pol-trades-meta");
        const fetchedAt = data.fetched_at || "";
        const sourceUrl = data.source_url || "https://disclosures-clerk.house.gov/FinancialDisclosure";
        if (meta) meta.innerHTML =
            `${_polTradesCache.length.toLocaleString()} total disclosures • ${year}` +
            (fetchedAt ? ` · Index fetched ${fetchedAt}` : "") +
            ` · <a href="${sourceUrl}" target="_blank" style="color:#60a5fa;text-decoration:none;">House Clerk ↗</a>`;

        // Show suspicious alerts (always from full dataset, not filtered)
        _renderSuspiciousAlerts(_polAlertsCache);

        applyPolFilters();
    } catch (err) {
        errDiv.textContent = `⚠️ Failed to load politician trades: ${err.message}`;
        errDiv?.classList.remove("hidden");
    } finally {
        loading?.classList.add("hidden");
        if (refreshBtn) { refreshBtn.disabled = false; refreshBtn.textContent = "↻ Refresh"; }
    }
}

// ── PTR Live poller UI ────────────────────────────────────────

async function _pollPtrLiveStatus() {
    try {
        const res  = await fetch("/api/ptr-live-status");
        const data = await res.json();

        const bar      = document.getElementById("ptr-live-bar");
        const statusTx = document.getElementById("ptr-live-status-text");
        const newBadge = document.getElementById("ptr-live-new-badge");
        if (!bar) return;

        bar.classList.remove("hidden", "ptr-error", "ptr-polling");

        const st = data.status || "unknown";
        if (st === "polling") {
            bar.classList.add("ptr-polling");
            if (statusTx) statusTx.textContent = "Checking House Clerk for new filings…";
        } else if (st === "error") {
            bar.classList.add("ptr-error");
            if (statusTx) statusTx.textContent = `Live monitor error — retrying in 5 min`;
        } else {
            const last = data.last_poll ? `Last check: ${data.last_poll}` : "Starting up…";
            if (statusTx) statusTx.textContent = `🔴 LIVE — ${last} · checks every 5 min`;
        }

        // Show new-filing badge and auto-refresh the trades list
        const newCount = data.new_count || 0;
        if (newCount > 0 && newCount !== _ptrLastNewCount) {
            _ptrLastNewCount = newCount;

            if (newBadge) {
                newBadge.textContent = `${newCount} new filing${newCount > 1 ? "s" : ""}!`;
                newBadge.classList.remove("hidden");
            }

            // Pulse the tab button
            const tabBtn = document.getElementById("tab-pol-trades");
            if (tabBtn && !tabBtn.querySelector(".pol-tab-live-dot")) {
                const dot = document.createElement("span");
                dot.className = "pol-tab-live-dot";
                tabBtn.appendChild(dot);
            }

            // If pol-trades panel is currently active, auto-refresh
            const panel = document.getElementById("panel-pol-trades");
            if (panel && panel.classList.contains("active")) {
                // The new trades are already merged into the server cache;
                // force a re-fetch so JS cache is busted too
                _polTradesCache = null;
                await fetchAndRenderPolTrades(false);  // use updated server cache
            }

            // Also surface any suspicious live alerts
            if (data.suspicious_live && data.suspicious_live.length) {
                _renderSuspiciousAlerts([...data.suspicious_live, ..._polAlertsCache]);
            }
        }
    } catch (_) {
        // Silent — poller may not be running
    }
}

function setupPolTradesPanel() {
    if (_polTradesInited) return;
    _polTradesInited = true;

    // Year selector
    document.getElementById("pol-year-select")?.addEventListener("change", () => {
        _polTradesCache = null;
        fetchAndRenderPolTrades();
    });

    // Month multi-select pills
    document.querySelectorAll(".pol-month-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const m = parseInt(btn.dataset.month, 10);
            if (m === 0) {
                // "All" — clear selection
                _polMonthFilter.clear();
                document.querySelectorAll(".pol-month-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
            } else {
                // Deactivate "All" button
                const allBtn = document.querySelector('.pol-month-btn[data-month="0"]');
                allBtn?.classList.remove("active");
                // Toggle this month
                if (_polMonthFilter.has(m)) {
                    _polMonthFilter.delete(m);
                    btn.classList.remove("active");
                } else {
                    _polMonthFilter.add(m);
                    btn.classList.add("active");
                }
                // If nothing selected, reactivate "All"
                if (_polMonthFilter.size === 0) {
                    allBtn?.classList.add("active");
                }
            }
            applyPolFilters();
        });
    });

    // Trade type toggle
    document.querySelectorAll(".pol-type-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".pol-type-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            _polTypeFilter = btn.dataset.type;
            applyPolFilters();
        });
    });

    // Asset type filter pills
    document.querySelectorAll(".pol-asset-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".pol-asset-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            _polAssetFilter = btn.dataset.asset;
            applyPolFilters();
        });
    });

    // Search
    let _searchTimer;
    document.getElementById("pol-search")?.addEventListener("input", () => {
        clearTimeout(_searchTimer);
        _searchTimer = setTimeout(applyPolFilters, 250);
    });

    // Refresh button
    document.getElementById("pol-refresh-btn")?.addEventListener("click", async () => {
        _polTradesCache = null;
        // Clear server-side cache AND ticker resolution cache so new resolver runs
        await fetch("/api/clear-pol-cache", { method: "POST" }).catch(() => {});
        fetchAndRenderPolTrades(true);
    });

    // Sortable column headers (delegated to thead)
    document.querySelector("#pol-trades-table thead")?.addEventListener("click", e => {
        const th = e.target.closest(".pol-th.sortable");
        if (!th) return;
        const col = th.dataset.col;
        if (_polSortCol === col) {
            _polSortDir *= -1;          // flip direction
        } else {
            _polSortCol = col;
            _polSortDir = col === "tx_date" || col === "notif_date" ? -1 : 1;
        }
        _polUpdateSortHeaders();
        applyPolFilters();
    });
}

// ════════════════════════════════════════════════════
// EARNINGS CALENDAR
// ════════════════════════════════════════════════════
let _calWeek      = "next";
let _calUniverse  = "sp500";
let _calData      = null;   // last fetched payload

async function loadEarningsCalendar(force = false) {
    if (!force && _calData &&
        _calData._week === _calWeek &&
        _calData._universe === _calUniverse) {
        _renderCalendar(_calData);
        return;
    }

    const loading = document.getElementById("cal-loading");
    const errEl   = document.getElementById("cal-error");
    const grid    = document.getElementById("cal-grid");
    const note    = document.getElementById("cal-note");

    loading?.classList.remove("hidden");
    errEl?.classList.add("hidden");
    grid?.classList.add("hidden");

    try {
        const url = `/api/earnings/calendar?week=${_calWeek}&universe=${_calUniverse}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        data._week      = _calWeek;
        data._universe  = _calUniverse;
        _calData        = data;
        _renderCalendar(data);
        if (note) {
            note.textContent = `Source: Yahoo Finance • ${data.count} companies • Updated ${data.last_updated}`;
        }
    } catch (e) {
        loading?.classList.add("hidden");
        if (errEl) {
            errEl.textContent = `⚠️ Failed to load calendar: ${e.message}`;
            errEl.classList.remove("hidden");
        }
    }
}

function _renderCalendar(data) {
    const loading = document.getElementById("cal-loading");
    const grid    = document.getElementById("cal-grid");
    const meta    = document.getElementById("cal-meta");

    loading?.classList.add("hidden");

    // Build Mon-Fri date list
    const mon = new Date(data.week_start + "T12:00:00");
    const days = [];
    for (let i = 0; i < 5; i++) {
        const d = new Date(mon);
        d.setDate(mon.getDate() + i);
        const iso = d.toISOString().slice(0, 10);
        const label = d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
        days.push({ iso, label });
    }

    if (meta) {
        const ws = new Date(data.week_start + "T12:00:00");
        const we = new Date(data.week_end   + "T12:00:00");
        const fmt = d => d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
        meta.textContent = `Week of ${fmt(ws)} – ${fmt(we)} • ${data.count} companies`;
    }

    let html = '<div class="cal-table">';

    // Header row
    html += '<div class="cal-row cal-header-row">';
    html += '<div class="cal-timing-label"></div>';  // left gutter
    days.forEach(({ label, iso }) => {
        const hasTickers = data.days[iso];
        const count = hasTickers
            ? (hasTickers.pre?.length || 0) + (hasTickers.post?.length || 0) + (hasTickers.unknown?.length || 0)
            : 0;
        html += `<div class="cal-day-hdr${count === 0 ? " cal-day-empty" : ""}">
            <span class="cal-day-name">${label}</span>
            ${count > 0 ? `<span class="cal-day-count">${count}</span>` : ''}
        </div>`;
    });
    html += '</div>';

    // Before Open row
    html += '<div class="cal-row">';
    html += '<div class="cal-timing-label cal-pre-label">🌅 Before<br>Open</div>';
    days.forEach(({ iso }) => {
        const bucket = data.days[iso] || {};
        const items  = (bucket.pre || []).concat(bucket.unknown || []);
        html += `<div class="cal-cell ${items.length === 0 ? "cal-cell-empty" : ""}">`;
        items.forEach(({ ticker, name }) => {
            html += `<div class="cal-pill cal-pill-pre" title="${name}" onclick="loadTickerFromCal('${ticker}')">`
                  + `<span class="cal-ticker">${ticker}</span>`
                  + `<span class="cal-company">${_trimName(name)}</span>`
                  + `</div>`;
        });
        if (items.length === 0) html += '<span class="cal-dash">—</span>';
        html += '</div>';
    });
    html += '</div>';

    // After Close row
    html += '<div class="cal-row">';
    html += '<div class="cal-timing-label cal-post-label">🌆 After<br>Close</div>';
    days.forEach(({ iso }) => {
        const bucket = data.days[iso] || {};
        const items  = bucket.post || [];
        html += `<div class="cal-cell ${items.length === 0 ? "cal-cell-empty" : ""}">`;
        items.forEach(({ ticker, name }) => {
            html += `<div class="cal-pill cal-pill-post" title="${name}" onclick="loadTickerFromCal('${ticker}')">`
                  + `<span class="cal-ticker">${ticker}</span>`
                  + `<span class="cal-company">${_trimName(name)}</span>`
                  + `</div>`;
        });
        if (items.length === 0) html += '<span class="cal-dash">—</span>';
        html += '</div>';
    });
    html += '</div>';

    html += '</div>';   // .cal-table

    if (grid) {
        grid.innerHTML = html;
        grid.classList.remove("hidden");
    }
}

function _trimName(name) {
    // Shorten long company names for the pill
    return name.length > 22 ? name.slice(0, 20) + "…" : name;
}

function loadTickerFromCal(ticker) {
    const input = document.getElementById("ticker-input");
    if (input) input.value = ticker;
    document.getElementById("fetch-chart-btn")?.click();
    window.scrollTo({ top: document.getElementById("chart-widget")?.offsetTop || 0, behavior: "smooth" });
}

async function _showCalendarImage() {
    const modal   = document.getElementById("cal-image-modal");
    const loading = document.getElementById("cal-img-loading");
    const img     = document.getElementById("cal-modal-img");
    const dlBtn   = document.getElementById("cal-download-btn");

    // Show modal + spinner
    modal?.classList.remove("hidden");
    loading?.classList.remove("hidden");
    img?.classList.add("hidden");

    const url = `/api/earnings/calendar/image?week=${_calWeek}&universe=${_calUniverse}`;
    try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const objectUrl = URL.createObjectURL(blob);

        if (img) {
            img.src = objectUrl;
            img.onload = () => {
                loading?.classList.add("hidden");
                img.classList.remove("hidden");
            };
        }
        if (dlBtn) {
            dlBtn.href = objectUrl;
            dlBtn.download = `earnings_calendar_${_calWeek}_week.png`;
        }
    } catch (e) {
        if (loading) loading.innerHTML = `<p style="color:#f87171">⚠️ ${e.message}</p>`;
    }
}

function _closeCalModal() {
    const modal = document.getElementById("cal-image-modal");
    modal?.classList.add("hidden");
    // Revoke any old object URL to free memory
    const img = document.getElementById("cal-modal-img");
    if (img?.src?.startsWith("blob:")) {
        URL.revokeObjectURL(img.src);
        img.src = "";
    }
}

function _calBuildWhatsAppText(data) {
    if (!data) return "";
    const mon = new Date(data.week_start + "T12:00:00");
    const fri = new Date(data.week_end   + "T12:00:00");
    const fmt = d => d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });

    let msg = `📅 *Earnings Calendar*\n${fmt(mon)} – ${fmt(fri)}\n`;
    msg += `━━━━━━━━━━━━━━━━━━━━\n`;

    const days = [];
    for (let i = 0; i < 5; i++) {
        const d = new Date(mon);
        d.setDate(mon.getDate() + i);
        days.push(d.toISOString().slice(0, 10));
    }

    const dayNames = ["Mon", "Tue", "Wed", "Thu", "Fri"];
    days.forEach((iso, idx) => {
        const bucket = data.days[iso] || {};
        const pre    = (bucket.pre || []).concat(bucket.unknown || []);
        const post   = bucket.post || [];
        if (pre.length === 0 && post.length === 0) return;
        msg += `\n*${dayNames[idx]}*\n`;
        if (pre.length)  msg += `  🌅 Before Open: ${pre.map(x => x.ticker).join(", ")}\n`;
        if (post.length) msg += `  🌆 After Close: ${post.map(x => x.ticker).join(", ")}\n`;
    });

    msg += `\n_via Stocks RSI Dashboard_`;
    return msg;
}

// Wire up calendar controls after DOM ready
document.addEventListener("DOMContentLoaded", () => {
    // Week toggle
    document.querySelectorAll(".cal-week-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".cal-week-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            _calWeek = btn.dataset.week;
            _calData = null;
            loadEarningsCalendar();
        });
    });

    // Universe toggle
    document.querySelectorAll(".cal-univ-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".cal-univ-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            _calUniverse = btn.dataset.universe;
            _calData = null;
            loadEarningsCalendar();
        });
    });

    // Refresh
    document.getElementById("cal-refresh-btn")?.addEventListener("click", () => {
        _calData = null;
        loadEarningsCalendar(true);
    });

    // WhatsApp share
    document.getElementById("cal-share-wa-btn")?.addEventListener("click", () => {
        const text = _calBuildWhatsAppText(_calData);
        if (!text) {
            alert("Please load the calendar first.");
            return;
        }
        const url = "https://wa.me/?text=" + encodeURIComponent(text);
        window.open(url, "_blank");
    });

    // Generate Image button
    document.getElementById("cal-image-btn")?.addEventListener("click", () => {
        _showCalendarImage();
    });

    // Modal close
    document.getElementById("cal-modal-close-btn")?.addEventListener("click", _closeCalModal);
    document.getElementById("cal-image-modal")?.addEventListener("click", e => {
        if (e.target === document.getElementById("cal-image-modal")) _closeCalModal();
    });

    // WhatsApp button inside modal (text fallback)
    document.getElementById("cal-wa-image-btn")?.addEventListener("click", () => {
        const text = _calBuildWhatsAppText(_calData);
        const url = "https://wa.me/?text=" + encodeURIComponent(text || "Check out next week's earnings!");
        window.open(url, "_blank");
    });
});

// ════════════════════════════════════════════════════
// SECTORS PANEL
// ════════════════════════════════════════════════════
let _sectorsInitialized = false;
let _activeSectorKey = "all";
let _sectorsData = null;      // last loaded data for the active sector
let _sectorSortCol = null;    // currently sorted column key (null = default/unsorted)
let _sectorSortDir = -1;      // -1 = desc, 1 = asc

// Numeric columns default to descending on first click
const _SECTOR_NUMERIC_COLS = new Set([
    "price", "change_pct", "rsi", "w52_high", "w52_low", "pct_from_high", "ytd_high", "ytd_low"
]);

async function setupSectorsPanel() {
    if (_sectorsInitialized) {
        // Just re-render whatever sector was active
        _renderSectorsTable(_sectorsData);
        return;
    }
    _sectorsInitialized = true;

    // Bind sort clicks on static header <th> elements
    document.querySelectorAll("#sectors-table thead th[data-col]").forEach(th => {
        th.addEventListener("click", () => {
            const col = th.dataset.col;
            if (_sectorSortCol === col) {
                _sectorSortDir *= -1;           // toggle direction
            } else {
                _sectorSortCol = col;
                // Numeric cols → desc first; string cols → asc first
                _sectorSortDir = _SECTOR_NUMERIC_COLS.has(col) ? -1 : 1;
            }
            _renderSectorsTable(_sectorsData);
        });
    });

    // Load sector list and build pills
    try {
        const res = await fetch("/api/watchlist/sectors");
        const data = await res.json();
        const sectors = data.sectors || [];
        const pillsEl = document.getElementById("sector-pills");
        if (!pillsEl) return;

        // "All" pill
        const allPill = _makeSectorPill("all", "🌐", "All Sectors", true);
        pillsEl.appendChild(allPill);

        sectors.forEach(s => {
            const pill = _makeSectorPill(s.key, s.icon, s.name, false);
            pillsEl.appendChild(pill);
        });
    } catch (e) {
        console.error("Failed to load sector list", e);
    }

    // Refresh button
    document.getElementById("sectors-refresh-btn")?.addEventListener("click", () => {
        _sectorsData = null;
        loadSectorStats(_activeSectorKey, true);
    });

    // Load default (all)
    loadSectorStats("all");
}

function _makeSectorPill(key, icon, name, active) {
    const btn = document.createElement("button");
    btn.className = "sector-pill" + (active ? " active" : "");
    btn.dataset.sector = key;
    btn.textContent = `${icon} ${name}`;
    btn.addEventListener("click", () => {
        document.querySelectorAll(".sector-pill").forEach(p => p.classList.remove("active"));
        btn.classList.add("active");
        _activeSectorKey = key;
        loadSectorStats(key);
    });
    return btn;
}

async function loadSectorStats(sectorKey, forceRefresh = false) {
    const loading = document.getElementById("sectors-loading");
    const error   = document.getElementById("sectors-error");
    const content = document.getElementById("sectors-content");
    const meta    = document.getElementById("sectors-meta");

    loading?.classList.remove("hidden");
    error?.classList.add("hidden");
    content?.classList.add("hidden");

    try {
        const url = `/api/sectors/stats?sector=${encodeURIComponent(sectorKey)}${forceRefresh ? "&_t=" + Date.now() : ""}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        _sectorsData = data;
        if (meta) meta.textContent = `${data.count} stocks · sector: ${sectorKey === "all" ? "All" : sectorKey}`;
        _renderSectorsTable(data);
    } catch (e) {
        loading?.classList.add("hidden");
        error?.classList.remove("hidden");
        if (error) error.textContent = `Failed to load sector data: ${e.message}`;
    }
}

function _renderSectorsTable(data) {
    const loading = document.getElementById("sectors-loading");
    const content = document.getElementById("sectors-content");
    const tbody   = document.getElementById("sectors-body");
    if (!tbody) return;

    loading?.classList.add("hidden");
    content?.classList.remove("hidden");

    let stocks = (data && data.stocks) ? [...data.stocks] : [];
    if (!stocks.length) {
        tbody.innerHTML = `<tr><td colspan="12" class="sectors-empty">No data available.</td></tr>`;
        return;
    }

    // Apply column sort
    if (_sectorSortCol) {
        const col = _sectorSortCol;
        const dir = _sectorSortDir;
        stocks.sort((a, b) => {
            let av = a[col], bv = b[col];
            if (av == null && bv == null) return 0;
            if (av == null) return 1;   // nulls always last
            if (bv == null) return -1;
            if (typeof av === "string") return dir * av.localeCompare(bv);
            return dir * (av - bv);
        });
    }

    // Update header sort indicators
    document.querySelectorAll("#sectors-table thead th[data-col]").forEach(th => {
        th.classList.remove("sort-asc", "sort-desc");
        const arrow = th.querySelector(".sort-arrow");
        if (th.dataset.col === _sectorSortCol) {
            th.classList.add(_sectorSortDir === 1 ? "sort-asc" : "sort-desc");
            if (arrow) arrow.textContent = _sectorSortDir === 1 ? "↑" : "↓";
        } else {
            if (arrow) arrow.textContent = "⇅";
        }
    });

    tbody.innerHTML = stocks.map((s, i) => buildSectorRow(s, i + 1)).join("");
}

function buildSectorRow(s, idx) {
    const rsi = s.rsi != null ? s.rsi : "—";
    const rsiClass = s.rsi == null ? "" : s.rsi <= 30 ? "rsi-oversold" : s.rsi >= 70 ? "rsi-overbought" : "rsi-neutral";
    const signal = _rsiSignalBadge(s.rsi);
    const chgClass = s.change_pct >= 0 ? "chg-up" : "chg-dn";
    const chgSign  = s.change_pct >= 0 ? "+" : "";
    const pctFromHigh = s.pct_from_high != null ? s.pct_from_high : null;
    const pctClass = pctFromHigh == null ? "" : pctFromHigh >= -5 ? "pct-near-high" : pctFromHigh >= -20 ? "pct-mid" : "pct-far";

    // Sector badge (only shown in "all" view)
    const sectorBadge = s.sector_name
        ? `<span class="sector-badge">${s.sector_icon || ""} ${s.sector_name}</span>`
        : "";

    return `<tr>
        <td class="row-num">${idx}</td>
        <td class="sectors-ticker">
            <a href="#" class="ticker-link" onclick="tickerJump('${s.ticker}'); return false;">${s.ticker}</a>
            ${sectorBadge}
        </td>
        <td class="sectors-name">${s.name || s.ticker}</td>
        <td class="sectors-price">$${s.price != null ? s.price.toFixed(2) : "—"}</td>
        <td class="sectors-chg ${chgClass}">${s.change_pct != null ? chgSign + s.change_pct.toFixed(2) + "%" : "—"}</td>
        <td class="sectors-rsi ${rsiClass}">${rsi}</td>
        <td>${signal}</td>
        <td class="sectors-stat">$${s.w52_high != null ? s.w52_high.toFixed(2) : "—"}</td>
        <td class="sectors-stat">$${s.w52_low  != null ? s.w52_low.toFixed(2)  : "—"}</td>
        <td class="sectors-pct-high ${pctClass}">${pctFromHigh != null ? pctFromHigh.toFixed(1) + "%" : "—"}</td>
        <td class="sectors-stat">$${s.ytd_high != null ? s.ytd_high.toFixed(2) : "—"}</td>
        <td class="sectors-stat">$${s.ytd_low  != null ? s.ytd_low.toFixed(2)  : "—"}</td>
    </tr>`;
}

function _rsiSignalBadge(rsi) {
    if (rsi == null) return '<span class="signal-badge signal-na">—</span>';
    if (rsi <= 30)   return '<span class="signal-badge signal-oversold">Oversold</span>';
    if (rsi >= 70)   return '<span class="signal-badge signal-overbought">Overbought</span>';
    if (rsi < 45)    return '<span class="signal-badge signal-weak">Weak</span>';
    if (rsi > 55)    return '<span class="signal-badge signal-strong">Strong</span>';
    return '<span class="signal-badge signal-neutral">Neutral</span>';
}

// Jump to chart widget and load ticker
function tickerJump(ticker) {
    const input = document.getElementById("ticker-input");
    if (input) {
        input.value = ticker;
        document.getElementById("fetch-chart-btn")?.click();
        document.getElementById("chart-widget")?.scrollIntoView({ behavior: "smooth" });
    }
}

// ════════════════════════════════════════════════════════════════════════════
// POLITICAL CYCLE CHART PANEL
// ════════════════════════════════════════════════════════════════════════════

let _cycleInitialized   = false;
let _cycleChart         = null;
let _cycleYearChart     = null;   // second chart — single-year price view
let _cycleNormalizedChart = null; // bottom chart — annual range-normalized view
let _cycleData          = null;   // last loaded API response
let _cycleFilter        = "election-midterm";  // current filter pill

// ── Distinct per-year colors (consistent regardless of filter) ────────────
// Assigned in ascending year order so each year always gets the same colour.
const _DISTINCT_YEAR_COLORS = [
    "#f43f5e",  // rose
    "#f97316",  // orange
    "#eab308",  // yellow
    "#22c55e",  // green
    "#06b6d4",  // cyan
    "#3b82f6",  // blue-500  ← election avg uses this
    "#a855f7",  // purple
    "#ec4899",  // pink
    "#14b8a6",  // teal
    "#84cc16",  // lime
    "#6366f1",  // indigo
    "#d946ef",  // fuchsia
    "#0ea5e9",  // sky
    "#10b981",  // emerald ← post-election avg uses this
    "#fb923c",  // amber
    "#e11d48",  // crimson
    "#0891b2",  // dark-cyan
];
const _CYCLE_AVG_COLOR = {
    "election":      "#3b82f6",
    "midterm":       "#f59e0b",
    "post-election": "#10b981",
    "pre-election":  "#8b5cf6",
};
const _CYCLE_LABEL = {
    "election":      "Election Year",
    "midterm":       "Midterm Year",
    "post-election": "Post-Election",
    "pre-election":  "Pre-Election",
};
const _CYCLE_ICON = {
    "election":      "🗳️",
    "midterm":       "⚖️",
    "post-election": "🌱",
    "pre-election":  "🔥",
};

// ── Panel setup (runs once per tab activation) ────────────────────────────
function setupCyclePanel() {
    if (_cycleInitialized) {
        // Re-entering the tab — just nudge chart to redraw at correct size
        if (_cycleChart) setTimeout(() => _cycleChart.resize(), 40);
        if (_cycleYearChart) setTimeout(() => _cycleYearChart.resize(), 40);
        if (_cycleNormalizedChart) setTimeout(() => _cycleNormalizedChart.resize(), 40);
        return;
    }
    _cycleInitialized = true;

    const input = document.getElementById("cycle-ticker-input");
    const btn   = document.getElementById("cycle-analyze-btn");

    btn?.addEventListener("click", () => {
        const t = (input?.value || "").trim().toUpperCase();
        if (t) loadCycleChart(t);
    });
    input?.addEventListener("keydown", e => {
        if (e.key === "Enter") {
            const t = (input.value || "").trim().toUpperCase();
            if (t) loadCycleChart(t);
        }
    });

    document.querySelectorAll(".cycle-pill").forEach(pill => {
        pill.addEventListener("click", () => {
            document.querySelectorAll(".cycle-pill").forEach(p => p.classList.remove("active"));
            pill.classList.add("active");
            _cycleFilter = pill.dataset.filter;
            _applyCycleFilter(_cycleFilter);
            if (_cycleData) _renderCycleTable(_cycleData, _cycleFilter);
        });
    });

    // Year-select dropdown → re-render single-year price chart
    document.getElementById("cycle-year-select")?.addEventListener("change", e => {
        if (_cycleData) _renderCycleYearChart(_cycleData, e.target.value);
    });

    document.getElementById("cycle-normalized-year-select")?.addEventListener("change", () => {
        if (_cycleData) _renderCycleNormalizedChart(_cycleData);
    });
    document.getElementById("cycle-normalized-latest-btn")?.addEventListener("click", () => {
        if (!_cycleData) return;
        _selectCycleNormalizedYears(_getDefaultCycleNormalizedYears(_cycleData));
        _renderCycleNormalizedChart(_cycleData);
    });
    document.getElementById("cycle-normalized-all-btn")?.addEventListener("click", () => {
        if (!_cycleData) return;
        _selectCycleNormalizedYears(Object.keys(_cycleData.years).sort());
        _renderCycleNormalizedChart(_cycleData);
    });
}

// ── Data fetch ────────────────────────────────────────────────────────────
async function loadCycleChart(ticker) {
    const loading = document.getElementById("cycle-loading");
    const error   = document.getElementById("cycle-error");
    const content = document.getElementById("cycle-content");
    const meta    = document.getElementById("cycle-meta");

    loading?.classList.remove("hidden");
    error?.classList.add("hidden");
    content?.classList.add("hidden");
    if (meta) meta.textContent = "";

    try {
        const res  = await fetch(`/api/cycle-chart?ticker=${encodeURIComponent(ticker)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        _cycleData = data;
        loading?.classList.add("hidden");
        content?.classList.remove("hidden");

        const yearCount = Object.keys(data.years).length;
        if (meta) meta.textContent = `${ticker} · ${yearCount} years · cycle breakdown since ${Object.keys(data.years).sort()[0]}`;

        _renderCycleSummary(data);
        _renderCycleChart(data);
        _renderCycleTable(data, _cycleFilter);
        _populateCycleYearSelect(data);
        _populateCycleNormalizedYearSelect(data);

    } catch (e) {
        loading?.classList.add("hidden");
        if (error) { error.classList.remove("hidden"); error.textContent = `Failed: ${e.message}`; }
    }
}

// ── Build full dataset list (all years + avg lines) ───────────────────────
function _buildAllCycleDatasets(yearsData) {
    const datasets   = [];
    const avgBuckets = {};

    // Initialise avg accumulators for all 4 types
    ["election","midterm","post-election","pre-election"].forEach(t => {
        avgBuckets[t] = Array.from({length: 12}, () => []);
    });

    const sortedYears = Object.keys(yearsData).sort();

    // Accumulate monthly close-pct values for category averages
    sortedYears.forEach(yr => {
        const yd = yearsData[yr];
        yd.months.forEach((m, i) => {
            if (m != null) avgBuckets[yd.year_type][i].push(m.pct_close);
        });
    });

    // Per-year line datasets — each year gets a unique colour from the palette
    sortedYears.forEach((yr, idx) => {
        const yd    = yearsData[yr];
        const type  = yd.year_type;
        const color = _DISTINCT_YEAR_COLORS[idx % _DISTINCT_YEAR_COLORS.length];
        const icon  = _CYCLE_ICON[type];

        datasets.push({
            label:            `${icon} ${yr}`,
            data:             yd.months.map(m => m != null ? m.pct_close : null),
            borderColor:      color,
            backgroundColor:  color + "18",
            tension:          0.35,
            borderWidth:      2.5,
            pointRadius:      4,
            pointHoverRadius: 8,
            hidden:           false,
            spanGaps:         false,
            _yearType:        type,
            _annualReturn:    yd.annual_return,
            _months:          yd.months,   // for tooltip High/Low
        });
    });

    // Dashed average lines for all 4 types
    Object.keys(_CYCLE_AVG_COLOR).forEach(type => {
        const avgData = avgBuckets[type].map(arr =>
            arr.length ? +(arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(2) : null
        );
        if (!avgData.some(v => v != null)) return;

        datasets.push({
            label:            `${_CYCLE_ICON[type]} Avg ${_CYCLE_LABEL[type]}`,
            data:             avgData,
            borderColor:      _CYCLE_AVG_COLOR[type],
            backgroundColor:  "transparent",
            borderWidth:      3,
            borderDash:       [7, 4],
            pointRadius:      4.5,
            pointHoverRadius: 9,
            tension:          0.35,
            hidden:           false,
            spanGaps:         true,
            _isAvg:           true,
            _yearType:        type,
        });
    });

    // Zero baseline (rendered first = bottom layer)
    datasets.unshift({
        label:           "── 0%",
        data:            Array(12).fill(0),
        borderColor:     "rgba(148,163,184,0.18)",
        borderWidth:     1,
        borderDash:      [4, 4],
        pointRadius:     0,
        backgroundColor: "transparent",
        hidden:          false,
        _isBaseline:     true,
    });

    return datasets;
}

// ── Helper: does a filter value match a given year type? ─────────────────
function _filterMatchesType(filter, type) {
    if (filter === "all")              return true;
    if (filter === "election-midterm") return type === "election" || type === "midterm";
    return filter === type;
}

// ── Toggle visibility based on filter pill (no rebuild) ───────────────────
function _applyCycleFilter(filter) {
    if (!_cycleChart) return;
    _cycleChart.data.datasets.forEach(ds => {
        if (ds._isBaseline) { ds.hidden = false; return; }
        ds.hidden = !_filterMatchesType(filter, ds._yearType);
    });
    _cycleChart.update("none");  // instant, no animation
}

// ── Chart render ──────────────────────────────────────────────────────────
function _renderCycleChart(data) {
    const canvas = document.getElementById("cycle-canvas");
    if (!canvas) return;

    const datasets = _buildAllCycleDatasets(data.years);
    const labels   = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

    if (_cycleChart) {
        // New ticker → replace datasets
        _cycleChart.data.datasets = datasets;
        _cycleChart.update();
        _applyCycleFilter(_cycleFilter);
        return;
    }

    _cycleChart = new Chart(canvas.getContext("2d"), {
        type: "line",
        data: { labels, datasets },
        options: {
            responsive:          true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: {
                    display:  true,
                    position: "top",
                    labels: {
                        color:           "#94a3b8",
                        usePointStyle:   true,
                        pointStyleWidth: 14,
                        font:            { size: 10 },
                        padding:         14,
                        // Hide baseline row AND any dataset hidden by the filter pill
                        filter: (item, chartData) => {
                            if (item.text.startsWith("──")) return false;
                            return !chartData.datasets[item.datasetIndex].hidden;
                        },
                    },
                },
                tooltip: {
                    backgroundColor: "rgba(15,23,42,0.95)",
                    titleColor:      "#f1f5f9",
                    bodyColor:       "#94a3b8",
                    borderColor:     "rgba(99,102,241,0.3)",
                    borderWidth:     1,
                    padding:         12,
                    callbacks: {
                        title: ctx => ctx[0] ? ctx[0].label : "",
                        label: ctx => {
                            if (ctx.dataset._isBaseline) return null;
                            const v = ctx.parsed.y;
                            if (v == null) return null;
                            const sign = v >= 0 ? "+" : "";
                            let line = ` ${ctx.dataset.label}: ${sign}${v.toFixed(1)}%`;
                            // Append monthly high/low range for individual year lines
                            if (ctx.dataset._months) {
                                const m = ctx.dataset._months[ctx.dataIndex];
                                if (m) {
                                    const hs = m.pct_high >= 0 ? "+" : "";
                                    const ls = m.pct_low  >= 0 ? "+" : "";
                                    line += `  (H: ${hs}${m.pct_high.toFixed(1)}% · L: ${ls}${m.pct_low.toFixed(1)}%)`;
                                }
                            }
                            return line;
                        },
                    },
                },
            },
            scales: {
                x: {
                    grid:  { color: "rgba(148,163,184,0.07)" },
                    ticks: { color: "#64748b", font: { size: 11 } },
                },
                y: {
                    grid:  { color: "rgba(148,163,184,0.07)" },
                    ticks: {
                        color: "#64748b",
                        font:  { size: 11 },
                        callback: v => `${v >= 0 ? "+" : ""}${v.toFixed(0)}%`,
                    },
                },
            },
        },
    });

    // Apply current filter immediately
    _applyCycleFilter(_cycleFilter);
}

// ── Summary stat cards ────────────────────────────────────────────────────
function _renderCycleSummary(data) {
    const el = document.getElementById("cycle-summary-cards");
    if (!el) return;

    const sums   = {};
    const counts = {};
    Object.values(data.years).forEach(yd => {
        const t = yd.year_type;
        sums[t]   = (sums[t]   || 0) + yd.annual_return;
        counts[t] = (counts[t] || 0) + 1;
    });

    el.innerHTML = ["election","midterm","post-election","pre-election"].map(type => {
        const n    = counts[type] || 0;
        const avg  = n ? sums[type] / n : null;
        const sign = avg != null && avg >= 0 ? "+" : "";
        const cls  = avg == null ? "na" : avg >= 0 ? "up" : "dn";
        return `<div class="cycle-summary-card" data-type="${type}">
            <div class="cycle-card-icon">${_CYCLE_ICON[type]}</div>
            <div class="cycle-card-label">${_CYCLE_LABEL[type]}</div>
            <div class="cycle-card-value ${cls}">${avg != null ? sign + avg.toFixed(1) + "%" : "—"}</div>
            <div class="cycle-card-sub">${n} yr${n !== 1 ? "s" : ""} · avg annual return</div>
        </div>`;
    }).join("");
}

// ── Monthly breakdown table ───────────────────────────────────────────────
function _renderCycleTable(data, filter) {
    const thead = document.getElementById("cycle-thead-row");
    const tbody = document.getElementById("cycle-tbody");
    if (!thead || !tbody) return;

    const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

    // Build sorted list of all years (globally) so colour index is stable
    const allSortedYears = Object.keys(data.years).sort();

    // Filter years for display based on current pill
    const sortedYears = allSortedYears.filter(yr =>
        _filterMatchesType(filter, data.years[yr].year_type)
    );

    // Header row — colour each year by its unique palette colour (same as the chart line)
    thead.innerHTML = `<th class="cycle-th-month">Month</th>` +
        sortedYears.map(yr => {
            const type  = data.years[yr].year_type;
            const color = _DISTINCT_YEAR_COLORS[allSortedYears.indexOf(yr) % _DISTINCT_YEAR_COLORS.length];
            return `<th class="cycle-th-year" style="color:${color}" title="${_CYCLE_LABEL[type]}">${_CYCLE_ICON[type]} ${yr}</th>`;
        }).join("");

    // Month rows
    const rows = MONTHS.map((mon, mi) =>
        `<tr>
            <td class="cycle-td-month">${mon}</td>
            ${sortedYears.map(yr => {
                const m = data.years[yr].months[mi];
                if (!m) return `<td class="cycle-td-na">—</td>`;
                const v    = m.pct_close;
                const sign = v >= 0 ? "+" : "";
                let cls    = v > 0 ? "cycle-td-up" : v < 0 ? "cycle-td-dn" : "cycle-td-flat";
                if (Math.abs(v) >= 10) cls += " big";
                const tip  = `title="H: ${m.pct_high >= 0 ? "+" : ""}${m.pct_high.toFixed(1)}% · L: ${m.pct_low >= 0 ? "+" : ""}${m.pct_low.toFixed(1)}%"`;
                return `<td class="${cls}" ${tip}>${sign}${v.toFixed(1)}%</td>`;
            }).join("")}
        </tr>`
    );

    // Full Year row
    const annRow = sortedYears.map(yr => {
        const ret  = data.years[yr].annual_return;
        const sign = ret >= 0 ? "+" : "";
        const cls  = ret > 0 ? "cycle-td-up" : ret < 0 ? "cycle-td-dn" : "cycle-td-flat";
        return `<td class="${cls}">${sign}${ret.toFixed(1)}%</td>`;
    }).join("");
    rows.push(`<tr class="cycle-annual-row">
        <td class="cycle-td-month cycle-td-annual">Full Year</td>${annRow}
    </tr>`);

    tbody.innerHTML = rows.join("");
}

// ── Populate year dropdown & render the default year ─────────────────────
function _populateCycleYearSelect(data) {
    const sel = document.getElementById("cycle-year-select");
    if (!sel) return;

    const allYears = Object.keys(data.years).sort().reverse(); // newest first
    sel.innerHTML = allYears.map(yr => {
        const type = data.years[yr].year_type;
        return `<option value="${yr}">${_CYCLE_ICON[type]} ${yr}  —  ${_CYCLE_LABEL[type]}</option>`;
    }).join("");

    // Default to the most recent complete year (skip current partial year if mid-year)
    const defaultYear = allYears[0];
    sel.value = defaultYear;
    _renderCycleYearChart(data, defaultYear);
}

function _getCompleteCycleYears(data) {
    return Object.keys(data.years).sort().filter(yr =>
        data.years[yr].months.filter(Boolean).length === 12
    );
}

function _getDefaultCycleNormalizedYears(data) {
    const completeYears = _getCompleteCycleYears(data);
    const sourceYears = completeYears.length ? completeYears : Object.keys(data.years).sort();
    return sourceYears.slice(-8);
}

function _selectCycleNormalizedYears(years) {
    const sel = document.getElementById("cycle-normalized-year-select");
    if (!sel) return;

    const selected = new Set(years.map(String));
    Array.from(sel.options).forEach(opt => {
        opt.selected = selected.has(opt.value);
    });
}

function _getSelectedCycleNormalizedYears(data) {
    const sel = document.getElementById("cycle-normalized-year-select");
    if (!sel) return _getDefaultCycleNormalizedYears(data);

    const selected = Array.from(sel.selectedOptions).map(opt => opt.value).sort();
    if (selected.length) return selected;

    const fallback = _getDefaultCycleNormalizedYears(data);
    _selectCycleNormalizedYears(fallback);
    return fallback;
}

function _populateCycleNormalizedYearSelect(data) {
    const sel = document.getElementById("cycle-normalized-year-select");
    if (!sel) return;

    const allYears = Object.keys(data.years).sort().reverse();
    sel.innerHTML = allYears.map(yr => {
        const type = data.years[yr].year_type;
        return `<option value="${yr}">${_CYCLE_ICON[type]} ${yr} — ${_CYCLE_LABEL[type]}</option>`;
    }).join("");

    _selectCycleNormalizedYears(_getDefaultCycleNormalizedYears(data));
    _renderCycleNormalizedChart(data);
}

function _yearRangeForNormalizedChart(yd) {
    const months = yd.months.filter(Boolean);
    const lows = months.map(m => m.low).filter(Number.isFinite);
    const highs = months.map(m => m.high).filter(Number.isFinite);
    if (!lows.length || !highs.length) return null;

    const low = Math.min(...lows);
    const high = Math.max(...highs);
    if (!Number.isFinite(low) || !Number.isFinite(high) || high <= low) return null;
    return { low, high };
}

function _buildCycleNormalizedDatasets(data, selectedYears) {
    const allYears = Object.keys(data.years).sort();

    return selectedYears.map(yr => {
        const yd = data.years[yr];
        if (!yd) return null;

        const range = _yearRangeForNormalizedChart(yd);
        if (!range) return null;

        const type = yd.year_type;
        const yearIdx = allYears.indexOf(yr);
        const color = _DISTINCT_YEAR_COLORS[yearIdx % _DISTINCT_YEAR_COLORS.length];

        return {
            label: `${_CYCLE_ICON[type]} ${yr}`,
            data: yd.months.map(m => {
                if (!m) return null;
                return +((m.close - range.low) / (range.high - range.low)).toFixed(3);
            }),
            borderColor: color,
            backgroundColor: color + "18",
            borderWidth: 2.5,
            pointRadius: 4,
            pointHoverRadius: 8,
            pointHitRadius: 10,
            tension: 0.35,
            spanGaps: false,
            _yearType: type,
            _months: yd.months,
            _rangeLow: range.low,
            _rangeHigh: range.high,
        };
    }).filter(Boolean);
}

function _renderCycleNormalizedChart(data) {
    const canvas = document.getElementById("cycle-normalized-canvas");
    if (!canvas) return;

    const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const selectedYears = _getSelectedCycleNormalizedYears(data);
    const datasets = _buildCycleNormalizedDatasets(data, selectedYears);

    if (_cycleNormalizedChart) {
        _cycleNormalizedChart.data.labels = MONTHS;
        _cycleNormalizedChart.data.datasets = datasets;
        _cycleNormalizedChart.update();
        return;
    }

    _cycleNormalizedChart = new Chart(canvas.getContext("2d"), {
        type: "line",
        data: { labels: MONTHS, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "nearest", intersect: true },
            plugins: {
                legend: {
                    display: true,
                    position: "top",
                    labels: {
                        color: "#94a3b8",
                        usePointStyle: true,
                        pointStyleWidth: 12,
                        font: { size: 10 },
                        padding: 14,
                    },
                },
                tooltip: {
                    mode: "nearest",
                    intersect: true,
                    backgroundColor: "rgba(15,23,42,0.95)",
                    titleColor: "#f1f5f9",
                    bodyColor: "#94a3b8",
                    borderColor: "rgba(99,102,241,0.3)",
                    borderWidth: 1,
                    padding: 12,
                    callbacks: {
                        label: ctx => {
                            const v = ctx.parsed.y;
                            if (v == null) return null;
                            const m = ctx.dataset._months?.[ctx.dataIndex];
                            const closeText = m ? ` · Close $${m.close.toFixed(2)}` : "";
                            const rangeText = ctx.dataset._rangeLow != null && ctx.dataset._rangeHigh != null
                                ? ` · Range $${ctx.dataset._rangeLow.toFixed(2)}-$${ctx.dataset._rangeHigh.toFixed(2)}`
                                : "";
                            return ` ${ctx.dataset.label}: ${v.toFixed(3)}${closeText}${rangeText}`;
                        },
                    },
                },
            },
            scales: {
                x: {
                    grid: { color: "rgba(148,163,184,0.07)" },
                    ticks: { color: "#64748b", font: { size: 11 } },
                },
                y: {
                    min: 0,
                    max: 1,
                    grid: { color: "rgba(148,163,184,0.07)" },
                    ticks: {
                        stepSize: 0.1,
                        color: "#64748b",
                        font: { size: 11 },
                        callback: v => Number(v).toFixed(1),
                    },
                },
            },
        },
    });
}

// ── Single-year monthly price chart (High/Low band + Close line) ──────────
function _renderCycleYearChart(data, year) {
    const canvas = document.getElementById("cycle-year-canvas");
    if (!canvas) return;

    const yd = data.years[year];
    if (!yd) return;

    const type       = yd.year_type;
    const allYears   = Object.keys(data.years).sort();
    const yearIdx    = allYears.indexOf(year);
    const color      = _DISTINCT_YEAR_COLORS[yearIdx % _DISTINCT_YEAR_COLORS.length];
    const MONTHS     = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

    const highData  = yd.months.map(m => m ? +m.high.toFixed(2)  : null);
    const lowData   = yd.months.map(m => m ? +m.low.toFixed(2)   : null);
    const closeData = yd.months.map(m => m ? +m.close.toFixed(2) : null);

    const titleText = `${_CYCLE_ICON[type]}  ${year}  ·  ${_CYCLE_LABEL[type]}  ·  Monthly Price  (band = High → Low, line = Close)`;

    const datasets = [
        // High — fills toward Low dataset (index +1)
        {
            label:           `${year} High`,
            data:            highData,
            borderColor:     color + "44",
            backgroundColor: color + "18",
            borderWidth:     1,
            borderDash:      [4, 3],
            pointRadius:     0,
            fill:            "+1",      // fill down to Low dataset
            tension:         0.3,
            spanGaps:        false,
        },
        // Low — bottom of the price band
        {
            label:           `${year} Low`,
            data:            lowData,
            borderColor:     color + "44",
            backgroundColor: "transparent",
            borderWidth:     1,
            borderDash:      [4, 3],
            pointRadius:     0,
            fill:            false,
            tension:         0.3,
            spanGaps:        false,
        },
        // Close — solid bold line
        {
            label:           `${year} Close`,
            data:            closeData,
            borderColor:     color,
            backgroundColor: "transparent",
            borderWidth:     2.5,
            pointRadius:     5,
            pointHoverRadius: 9,
            fill:            false,
            tension:         0.35,
            spanGaps:        false,
        },
    ];

    // Update existing chart instance rather than recreating
    if (_cycleYearChart) {
        _cycleYearChart.data.labels   = MONTHS;
        _cycleYearChart.data.datasets = datasets;
        _cycleYearChart.options.plugins.title.text = titleText;
        _cycleYearChart.update();
        return;
    }

    _cycleYearChart = new Chart(canvas.getContext("2d"), {
        type: "line",
        data: { labels: MONTHS, datasets },
        options: {
            responsive:          true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            plugins: {
                title: {
                    display: true,
                    text:    titleText,
                    color:   "#94a3b8",
                    font:    { size: 11 },
                    padding: { bottom: 16 },
                },
                legend: {
                    display: true,
                    position: "top",
                    labels: {
                        color:           "#94a3b8",
                        usePointStyle:   true,
                        pointStyleWidth: 12,
                        font:            { size: 10 },
                        padding:         14,
                    },
                },
                tooltip: {
                    backgroundColor: "rgba(15,23,42,0.95)",
                    titleColor:      "#f1f5f9",
                    bodyColor:       "#94a3b8",
                    borderColor:     "rgba(99,102,241,0.3)",
                    borderWidth:     1,
                    padding:         12,
                    callbacks: {
                        label: ctx => {
                            const v = ctx.parsed.y;
                            if (v == null) return null;
                            const part = ctx.dataset.label.split(" ").pop(); // "High"|"Low"|"Close"
                            return ` ${part}: $${v.toFixed(2)}`;
                        },
                        footer: ctx => {
                            // Show H-L range in tooltip footer
                            const hi = ctx.find(c => c.dataset.label.endsWith("High"))?.parsed.y;
                            const lo = ctx.find(c => c.dataset.label.endsWith("Low"))?.parsed.y;
                            if (hi != null && lo != null)
                                return `Range: $${(hi - lo).toFixed(2)}`;
                        },
                    },
                },
            },
            scales: {
                x: {
                    grid:  { color: "rgba(148,163,184,0.07)" },
                    ticks: { color: "#64748b", font: { size: 11 } },
                },
                y: {
                    grid:  { color: "rgba(148,163,184,0.07)" },
                    ticks: {
                        color: "#64748b",
                        font:  { size: 11 },
                        callback: v => `$${v >= 1000 ? (v/1000).toFixed(1)+"k" : v.toFixed(0)}`,
                    },
                },
            },
        },
    });
}

// ─── Events Sidebar ────────────────────────────────────────────────────────

function formatEventDate(dateStr) {
    const d = new Date(dateStr + "T12:00:00"); // noon to avoid TZ shift
    return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

function buildEventCard(ev) {
    const impClass  = ev.importance || "low";
    const typeClass = ev.type       || "economic";
    const timeStr   = ev.time && ev.time !== "TBD" ? `· ${ev.time}` : "";

    return `
    <div class="event-card">
        <div class="event-card-top">
            <span class="event-icon">${ev.icon || "📌"}</span>
            <span class="event-name">${ev.name}</span>
        </div>
        <div class="event-meta">
            <span class="event-date">${formatEventDate(ev.date)}</span>
            ${timeStr ? `<span class="event-time">${timeStr}</span>` : ""}
            <span class="event-imp ${impClass}">${impClass}</span>
            <span class="event-type ${typeClass}">${typeClass}</span>
        </div>
    </div>`;
}

function renderEventsList(listEl, emptyEl, events) {
    if (!events || events.length === 0) {
        listEl.innerHTML   = "";
        emptyEl.classList.remove("hidden");
        return;
    }
    emptyEl.classList.add("hidden");
    listEl.innerHTML = events.map(buildEventCard).join("");
}

async function loadMarketEvents() {
    const loadingEl   = document.getElementById("events-loading");
    const contentEl   = document.getElementById("events-content");
    const updatedEl   = document.getElementById("events-updated");
    const thisLabel   = document.getElementById("events-this-label");
    const nextLabel   = document.getElementById("events-next-label");
    const thisListEl  = document.getElementById("events-this-list");
    const nextListEl  = document.getElementById("events-next-list");
    const thisEmptyEl = document.getElementById("events-this-empty");
    const nextEmptyEl = document.getElementById("events-next-empty");

    if (!loadingEl || !contentEl) return;

    // Only fetch once per session (cached 1h on server anyway)
    if (!contentEl.classList.contains("hidden")) return;

    loadingEl.classList.remove("hidden");
    contentEl.classList.add("hidden");

    try {
        const res  = await fetch("/api/market-events");
        if (!res.ok) throw new Error("Failed to load events");
        const data = await res.json();

        if (thisLabel && data.week_label)      thisLabel.textContent = `THIS WEEK · ${data.week_label}`;
        if (nextLabel && data.next_week_label) nextLabel.textContent = `NEXT WEEK · ${data.next_week_label}`;
        if (updatedEl && data.updated_at)      updatedEl.textContent = `as of ${data.updated_at}`;

        renderEventsList(thisListEl, thisEmptyEl, data.this_week);
        renderEventsList(nextListEl, nextEmptyEl, data.next_week);

        loadingEl.classList.add("hidden");
        contentEl.classList.remove("hidden");

        // Load news in parallel
        loadMarketNews();

    } catch (err) {
        console.error("Events error:", err);
        if (loadingEl) loadingEl.innerHTML = `<p style="color:var(--text-muted);font-size:0.8rem">Failed to load events.</p>`;
    }
}

async function loadMarketNews() {
    const loadingEl = document.getElementById("news-loading");
    const listEl    = document.getElementById("news-list");
    const tsEl      = document.getElementById("news-updated");

    if (!listEl) return;

    try {
        const res  = await fetch("/api/market-news");
        if (!res.ok) throw new Error("News fetch failed");
        const data = await res.json();

        if (tsEl && data.updated_at) tsEl.textContent = `· ${data.updated_at}`;
        if (loadingEl) loadingEl.style.display = "none";

        if (!data.articles || data.articles.length === 0) {
            listEl.innerHTML = `<div class="events-empty">No news available.</div>`;
            return;
        }

        listEl.innerHTML = data.articles.map(a => `
            <a class="news-card" href="${a.link}" target="_blank" rel="noopener">
                <div class="news-card-title">${a.title}</div>
                <div class="news-card-meta">
                    ${a.publisher ? `<span class="news-cat">${a.publisher}</span>` : ""}
                    <span class="news-time">${a.time}</span>
                </div>
            </a>`).join("");

    } catch (err) {
        console.error("News error:", err);
        if (loadingEl) loadingEl.textContent = "Failed to load news.";
    }
}

document.addEventListener("DOMContentLoaded", () => {
    // Market events loaded on tab click, not on page load

    // ── Chart widget collapse/expand toggle ──────────────────────────────
    const _CW_KEY = "chart_widget_collapsed";
    const toggleBtn  = document.getElementById("chart-widget-toggle");
    const widgetBody = document.getElementById("chart-widget-body");
    const widget     = document.getElementById("chart-widget");
    const toggleLbl  = toggleBtn?.querySelector(".chart-toggle-label");

    function _setChartWidgetState(collapsed, save) {
        if (!widgetBody || !widget) return;
        if (collapsed) {
            widgetBody.classList.add("collapsed");
            widget.classList.add("collapsed");
            if (toggleLbl) toggleLbl.textContent = "Show";
        } else {
            widgetBody.classList.remove("collapsed");
            widget.classList.remove("collapsed");
            if (toggleLbl) toggleLbl.textContent = "Hide";
            // Nudge the chart to resize once the body is visible again
            setTimeout(() => { if (typeof priceChart !== "undefined" && priceChart) priceChart.resize(); }, 340);
        }
        if (save) localStorage.setItem(_CW_KEY, collapsed ? "1" : "0");
    }

    // Restore saved state
    _setChartWidgetState(localStorage.getItem(_CW_KEY) === "1", false);

    toggleBtn?.addEventListener("click", () => {
        const isCollapsed = widgetBody.classList.contains("collapsed");
        _setChartWidgetState(!isCollapsed, true);
    });
});

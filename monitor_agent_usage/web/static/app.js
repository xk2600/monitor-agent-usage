/**
 * Monitor Agent Usage — Interactive 15-Minute Time-Series Graph & Drill-Down Dashboard
 */

const state = {
  userId: "all",
  provider: "all",
  interface: "all",
  rangeHours: "24",
  selectedBucketEpoch: null,
  searchQuery: "",
  timeseries: [],
  sessions: [],
  users: [],
  accounts: [],
  kpis: {},
  expandedSessions: new Set(),
  hoveredBarIndex: null,
  barRects: [],
  hasDemoData: false,
};

const COLORS = {
  in: "#38bdf8",
  out: "#a855f7",
  cached: "#10b981",
  grid: "#1e293b",
  text: "#94a3b8",
  selectedStroke: "#f8fafc",
  selectedBg: "rgba(56, 189, 248, 0.14)",
};

function fmtNum(n) {
  return Number(n || 0).toLocaleString();
}

function fmtCompact(n) {
  const val = Number(n || 0);
  if (val >= 1_000_000) return (val / 1_000_000).toFixed(2) + "M";
  if (val >= 1_000) return (val / 1_000).toFixed(1) + "k";
  return String(val);
}

function fmtBucketWindow(epochSec) {
  if (!epochSec) return "—";
  const start = new Date(epochSec * 1000);
  const end = new Date((epochSec + 900) * 1000);
  const datePart = start.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const t1 = start.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
  const t2 = end.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${datePart}, ${t1} – ${t2}`;
}

function fmtShortTime(epochSec) {
  const d = new Date(epochSec * 1000);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
}

function fmtTimestamp(isoStr) {
  if (!isoStr) return "—";
  const d = new Date(isoStr);
  if (isNaN(d.getTime())) return isoStr;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function loadDashboard() {
  const params = new URLSearchParams({
    user_id: state.userId,
    provider: state.provider,
    interface: state.interface,
    range_hours: state.rangeHours,
    search: state.searchQuery,
  });
  if (state.selectedBucketEpoch !== null) {
    params.set("bucket_epoch", String(state.selectedBucketEpoch));
  }

  try {
    const resp = await fetch(`/api/dashboard?${params.toString()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    state.timeseries = data.timeseries_15m || [];
    state.sessions = data.sessions || [];
    state.users = data.users || [];
    state.accounts = data.accounts || [];
    state.kpis = data.kpis || {};
    state.hasDemoData = state.accounts.some((a) => a.auth_type === "demo");

    updateDemoButton();
    updateSyncStatus(data.last_sync_at);
    populateUserDropdowns();
    renderBucketBanner();
    renderKPIs();
    renderTimeSeriesChart();
    renderDistributions();
    renderSessionsTable();
  } catch (err) {
    console.error("Failed to load dashboard:", err);
  }
}

function updateDemoButton() {
  const btn = document.getElementById("btnToggleDemo");
  if (!btn) return;
  btn.textContent = state.hasDemoData ? "Clear Sample Data" : "Load Sample Data";
}

function updateSyncStatus(lastSyncIso) {
  const el = document.getElementById("syncStatusText");
  if (!el) return;
  el.textContent = lastSyncIso ? `Last sync: ${fmtTimestamp(lastSyncIso)}` : "Last sync: Ready";
}

function populateUserDropdowns() {
  const userSelect = document.getElementById("userSelect");
  const modalUserSelect = document.getElementById("modalUserSelect");
  if (!userSelect) return;

  const currentVal = state.userId;
  userSelect.innerHTML = `<option value="all">All Users (${state.users.length})</option>`;
  if (modalUserSelect) modalUserSelect.innerHTML = "";

  for (const u of state.users) {
    const opt = document.createElement("option");
    opt.value = u.user_id;
    opt.textContent = `${u.display_name} (${fmtCompact(u.total_tokens)} tok)`;
    if (u.user_id === currentVal) opt.selected = true;
    userSelect.appendChild(opt);

    if (modalUserSelect) {
      const mOpt = document.createElement("option");
      mOpt.value = u.user_id;
      mOpt.textContent = `${u.display_name} (@${u.username})`;
      modalUserSelect.appendChild(mOpt);
    }
  }
}

function renderBucketBanner() {
  const banner = document.getElementById("bucketSelectionBanner");
  const label = document.getElementById("selectedBucketLabel");
  const stats = document.getElementById("selectedBucketStats");
  const tableHeading = document.getElementById("tableHeading");
  const tableSubheading = document.getElementById("tableSubheading");

  if (state.selectedBucketEpoch === null) {
    banner.classList.add("hidden");
    tableHeading.textContent = "All Chats & Token Consumption";
    tableSubheading.textContent =
      "Showing all sessions in the active time window. Click any 15-minute bar above to filter to that interval, or click a chat row to expand per-turn details.";
    return;
  }

  banner.classList.remove("hidden");
  const windowLabel = fmtBucketWindow(state.selectedBucketEpoch);
  label.textContent = windowLabel;
  stats.textContent = `(${state.sessions.length} active chat${state.sessions.length === 1 ? "" : "s"}, ${fmtNum(state.kpis.total_tokens)} total tokens in this 15m block)`;
  tableHeading.textContent = `Chats Active in 15m Block: ${windowLabel}`;
  tableSubheading.textContent =
    "Token metrics below reflect consumption strictly within this selected 15-minute interval.";
}

function renderKPIs() {
  const k = state.kpis || {};
  const total = Number(k.total_tokens || 0);
  const tIn = Number(k.tokens_in || 0);
  const tOut = Number(k.tokens_out || 0);
  const tCached = Number(k.tokens_cached || 0);
  const tCacheWrite = Number(k.tokens_cache_write || 0);
  const cachePct = Number(k.cache_hit_pct || 0).toFixed(1);

  document.getElementById("kpiTokensIn").textContent = fmtNum(tIn);
  const inShare = total > 0 ? ((tIn / total) * 100).toFixed(1) : "0.0";
  document.getElementById("kpiTokensInSub").textContent = `${inShare}% of total volume` + (tCacheWrite > 0 ? ` (+${fmtCompact(tCacheWrite)} cache write)` : "");

  document.getElementById("kpiTokensOut").textContent = fmtNum(tOut);
  const turns = Number(k.turn_count || 0);
  const avgOut = turns > 0 ? Math.round(tOut / turns) : 0;
  document.getElementById("kpiTokensOutSub").textContent = `Avg ${fmtNum(avgOut)} output tokens / turn`;

  document.getElementById("kpiTokensCached").textContent = fmtNum(tCached);
  document.getElementById("kpiCacheHitRate").textContent = `Cache hit efficiency: ${cachePct}% of input context`;

  document.getElementById("kpiTotalTokens").textContent = fmtNum(total);
  document.getElementById("kpiActiveSessions").textContent = `${fmtNum(k.session_count || 0)} chats across ${fmtNum(turns)} turns (${fmtNum(k.user_count || 0)} user${k.user_count === 1 ? "" : "s"})`;
}

function renderDistributions() {
  const provEl = document.getElementById("providerBreakdownList");
  const ifaceEl = document.getElementById("interfaceBreakdownList");
  const byProv = state.kpis.by_provider || [];
  const byIface = state.kpis.by_interface || [];

  provEl.innerHTML = renderBreakdownItems(byProv, "provider");
  ifaceEl.innerHTML = renderBreakdownItems(byIface, "interface");
}

function renderBreakdownItems(items, keyName) {
  if (!items.length) {
    return `<div style="color: var(--text-muted); font-size: 0.8rem;">No token activity in selected range.</div>`;
  }
  return items
    .map((item) => {
      const name = item[keyName] || "unknown";
      const total = Number(item.total_tokens || 0);
      const tIn = Number(item.tokens_in || 0);
      const tOut = Number(item.tokens_out || 0);
      const tCached = Number(item.tokens_cached || 0);
      const pIn = total > 0 ? (tIn / total) * 100 : 0;
      const pOut = total > 0 ? (tOut / total) * 100 : 0;
      const pCached = total > 0 ? (tCached / total) * 100 : 0;

      return `
        <div class="breakdown-item">
          <div class="breakdown-item-top">
            <span><strong>${escapeHtml(name.toUpperCase())}</strong> <span style="color:var(--text-muted)">(${item.session_count} chat${item.session_count === 1 ? "" : "s"})</span></span>
            <span>In: ${fmtCompact(tIn)} &bull; Out: ${fmtCompact(tOut)} &bull; Cached: ${fmtCompact(tCached)} &bull; <strong>${fmtCompact(total)}</strong></span>
          </div>
          <div class="breakdown-bar-track">
            <div class="bar-seg-in" style="width:${pIn.toFixed(1)}%"></div>
            <div class="bar-seg-out" style="width:${pOut.toFixed(1)}%"></div>
            <div class="bar-seg-cached" style="width:${pCached.toFixed(1)}%"></div>
          </div>
        </div>
      `;
    })
    .join("");
}

// ------------------------------------------------------------------
// Interactive 15-Minute Bucketized Time-Series Canvas Chart
// ------------------------------------------------------------------

function renderTimeSeriesChart() {
  const canvas = document.getElementById("timeseriesCanvas");
  if (!canvas) return;
  const rect = canvas.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(320, rect.width);
  const height = Math.max(220, rect.height);

  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  ctx.clearRect(0, 0, width, height);
  state.barRects = [];

  const buckets = state.timeseries || [];
  if (!buckets.length) {
    ctx.fillStyle = COLORS.text;
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No 15-minute time-series data for the selected filters.", width / 2, height / 2);
    return;
  }

  const padLeft = 58;
  const padRight = 18;
  const padTop = 18;
  const padBottom = 42;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  let maxVal = 0;
  for (const b of buckets) {
    const stackTotal = Number(b.tokens_in || 0) + Number(b.tokens_out || 0) + Number(b.tokens_cached || 0);
    if (stackTotal > maxVal) maxVal = stackTotal;
  }
  if (maxVal === 0) maxVal = 1000;
  maxVal = Math.ceil(maxVal * 1.1);

  // Horizontal grid lines & Y axis labels
  const gridSteps = 4;
  ctx.font = "11px sans-serif";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";

  for (let i = 0; i <= gridSteps; i++) {
    const ratio = i / gridSteps;
    const y = padTop + plotH - ratio * plotH;
    const val = Math.round(ratio * maxVal);

    ctx.strokeStyle = COLORS.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(padLeft + plotW, y);
    ctx.stroke();

    ctx.fillStyle = COLORS.text;
    ctx.fillText(fmtCompact(val), padLeft - 8, y);
  }

  const count = buckets.length;
  const slotW = plotW / count;
  const barW = Math.max(3, Math.min(28, slotW * 0.72));

  for (let i = 0; i < count; i++) {
    const b = buckets[i];
    const centerX = padLeft + i * slotW + slotW / 2;
    const x = centerX - barW / 2;

    const isSelected = state.selectedBucketEpoch === b.bucket_epoch;
    const isHovered = state.hoveredBarIndex === i;

    // Highlight slot background if selected or hovered
    if (isSelected || isHovered) {
      ctx.fillStyle = isSelected ? COLORS.selectedBg : "rgba(148, 163, 184, 0.08)";
      ctx.fillRect(padLeft + i * slotW, padTop, slotW, plotH);
    }

    const tIn = Number(b.tokens_in || 0);
    const tOut = Number(b.tokens_out || 0);
    const tCached = Number(b.tokens_cached || 0);

    const hIn = (tIn / maxVal) * plotH;
    const hOut = (tOut / maxVal) * plotH;
    const hCached = (tCached / maxVal) * plotH;

    let currentTop = padTop + plotH;

    // Draw Tokens In (bottom segment)
    if (hIn > 0) {
      currentTop -= hIn;
      ctx.fillStyle = COLORS.in;
      ctx.fillRect(x, currentTop, barW, hIn);
    }

    // Draw Tokens Out (middle segment)
    if (hOut > 0) {
      currentTop -= hOut;
      ctx.fillStyle = COLORS.out;
      ctx.fillRect(x, currentTop, barW, hOut);
    }

    // Draw Cached Tokens (top segment)
    if (hCached > 0) {
      currentTop -= hCached;
      ctx.fillStyle = COLORS.cached;
      ctx.fillRect(x, currentTop, barW, hCached);
    }

    // Draw selection outline around active 15m bucket
    if (isSelected) {
      ctx.strokeStyle = COLORS.selectedStroke;
      ctx.lineWidth = 2;
      ctx.strokeRect(padLeft + i * slotW + 1, padTop + 1, Math.max(2, slotW - 2), plotH - 2);
    }

    state.barRects.push({
      index: i,
      x: padLeft + i * slotW,
      w: slotW,
      y: padTop,
      h: plotH,
      bucket: b,
    });
  }

  // X-Axis Time Labels
  const maxLabels = Math.max(4, Math.floor(plotW / 85));
  const step = Math.max(1, Math.ceil(count / maxLabels));
  ctx.fillStyle = COLORS.text;
  ctx.textAlign = "center";
  ctx.textBaseline = "top";

  for (let i = 0; i < count; i += step) {
    const b = buckets[i];
    const centerX = padLeft + i * slotW + slotW / 2;
    ctx.fillText(fmtShortTime(b.bucket_epoch), centerX, padTop + plotH + 10);
  }
}

function setupChartInteractions() {
  const canvas = document.getElementById("timeseriesCanvas");
  const tooltip = document.getElementById("chartTooltip");
  if (!canvas || !tooltip) return;

  canvas.addEventListener("mousemove", (ev) => {
    const rect = canvas.getBoundingClientRect();
    const mx = ev.clientX - rect.left;
    const my = ev.clientY - rect.top;

    let hit = null;
    for (const r of state.barRects) {
      if (mx >= r.x && mx <= r.x + r.w && my >= r.y && my <= r.y + r.h) {
        hit = r;
        break;
      }
    }

    if (!hit) {
      if (state.hoveredBarIndex !== null) {
        state.hoveredBarIndex = null;
        renderTimeSeriesChart();
      }
      tooltip.classList.add("hidden");
      return;
    }

    if (state.hoveredBarIndex !== hit.index) {
      state.hoveredBarIndex = hit.index;
      renderTimeSeriesChart();
    }

    const b = hit.bucket;
    const provLines = Object.entries(b.by_provider || {})
      .map(([p, metrics]) => `<div class="tooltip-row"><span>&bull; ${escapeHtml(p)}</span><span>${fmtNum(metrics.total_tokens)}</span></div>`)
      .join("");

    tooltip.innerHTML = `
      <div class="tooltip-title">${fmtBucketWindow(b.bucket_epoch)} (15m)</div>
      <div class="tooltip-row"><span style="color:var(--color-in)">Tokens In:</span><strong>${fmtNum(b.tokens_in)}</strong></div>
      <div class="tooltip-row"><span style="color:var(--color-out)">Tokens Out:</span><strong>${fmtNum(b.tokens_out)}</strong></div>
      <div class="tooltip-row"><span style="color:var(--color-cached)">Cached Tokens:</span><strong>${fmtNum(b.tokens_cached)} (${b.cache_hit_pct}%)</strong></div>
      <div class="tooltip-row" style="border-top:1px solid #1e293b;padding-top:3px;margin-top:3px;">
        <span>Total Tokens:</span><strong>${fmtNum(b.total_tokens)}</strong>
      </div>
      <div class="tooltip-row"><span>Active Chats:</span><span>${b.session_count} (${b.turn_count} turns)</span></div>
      ${provLines ? `<div style="margin-top:4px;padding-top:4px;border-top:1px solid #1e293b;">${provLines}</div>` : ""}
      <div class="tooltip-hint">${
        state.selectedBucketEpoch === b.bucket_epoch ? "Click to clear 15m filter" : "Click bar to filter chats below to this 15m block"
      }</div>
    `;
    tooltip.classList.remove("hidden");

    const tooltipW = tooltip.offsetWidth || 230;
    let leftPos = mx + 14;
    if (leftPos + tooltipW > rect.width - 10) {
      leftPos = Math.max(8, mx - tooltipW - 14);
    }
    tooltip.style.left = `${leftPos}px`;
    tooltip.style.top = `${Math.max(8, my - 20)}px`;
  });

  canvas.addEventListener("mouseleave", () => {
    state.hoveredBarIndex = null;
    tooltip.classList.add("hidden");
    renderTimeSeriesChart();
  });

  canvas.addEventListener("click", (ev) => {
    const rect = canvas.getBoundingClientRect();
    const mx = ev.clientX - rect.left;
    const my = ev.clientY - rect.top;

    for (const r of state.barRects) {
      if (mx >= r.x && mx <= r.x + r.w && my >= r.y && my <= r.y + r.h) {
        const clickedEpoch = r.bucket.bucket_epoch;
        if (state.selectedBucketEpoch === clickedEpoch) {
          state.selectedBucketEpoch = null;
        } else {
          state.selectedBucketEpoch = clickedEpoch;
        }
        loadDashboard();
        break;
      }
    }
  });

  window.addEventListener("resize", () => {
    renderTimeSeriesChart();
  });
}

// ------------------------------------------------------------------
// Sessions & Consumption Table + Turn Drill-Down
// ------------------------------------------------------------------

function providerBadgeClass(prov) {
  const p = String(prov || "").toLowerCase();
  return `badge badge-provider-${p}`;
}

function interfaceBadgeClass(iface) {
  const key = String(iface || "").toLowerCase().replace(/\s+/g, "");
  return `badge badge-iface-${key}`;
}

function renderSessionsTable() {
  const tbody = document.getElementById("sessionsTableBody");
  if (!tbody) return;

  if (!state.sessions.length) {
    tbody.innerHTML = `
      <tr>
        <td colspan="11" class="empty-cell">
          No chat sessions found for the active filter${state.selectedBucketEpoch ? " in this 15-minute block" : ""}.
        </td>
      </tr>
    `;
    return;
  }

  const rowsHtml = [];
  for (const s of state.sessions) {
    const total = Number(s.total_tokens || 0);
    const tIn = Number(s.tokens_in || 0);
    const tOut = Number(s.tokens_out || 0);
    const tCached = Number(s.tokens_cached || 0);

    const pIn = total > 0 ? (tIn / total) * 100 : 0;
    const pOut = total > 0 ? (tOut / total) * 100 : 0;
    const pCached = total > 0 ? (tCached / total) * 100 : 0;
    const isExpanded = state.expandedSessions.has(s.session_id);

    rowsHtml.push(`
      <tr class="session-row" data-session-id="${escapeHtml(s.session_id)}">
        <td>
          <div class="session-name-cell">
            <span class="session-title">${isExpanded ? "&#9662;" : "&#9656;"} ${escapeHtml(s.session_name)}</span>
            <span class="session-sub">${escapeHtml(s.session_id.slice(0, 18))}${s.workspace_path ? " &bull; " + escapeHtml(s.workspace_path) : ""}</span>
          </div>
        </td>
        <td>
          <div>${escapeHtml(s.user_display_name)}</div>
          <div class="session-sub">${escapeHtml(s.account_label)}</div>
        </td>
        <td><span class="${providerBadgeClass(s.provider)}">${escapeHtml(s.provider.toUpperCase())}</span></td>
        <td><span class="${interfaceBadgeClass(s.interface)}">${escapeHtml(s.interface)}</span></td>
        <td><span class="session-sub">${escapeHtml(s.model)}</span></td>
        <td class="num" style="color:var(--color-in);font-weight:600;">${fmtNum(tIn)}</td>
        <td class="num" style="color:var(--color-out);font-weight:600;">${fmtNum(tOut)}</td>
        <td class="num" style="color:var(--color-cached);font-weight:600;">
          ${fmtNum(tCached)}
          <span class="cache-pill">${s.cache_hit_pct}%</span>
        </td>
        <td class="num">
          <div><strong>${fmtNum(total)}</strong></div>
          <div class="mini-mix-bar">
            <div class="bar-seg-in" style="width:${pIn.toFixed(1)}%"></div>
            <div class="bar-seg-out" style="width:${pOut.toFixed(1)}%"></div>
            <div class="bar-seg-cached" style="width:${pCached.toFixed(1)}%"></div>
          </div>
        </td>
        <td class="num">${fmtNum(s.turn_count)}</td>
        <td>${fmtTimestamp(s.last_event_at)}</td>
      </tr>
    `);

    if (isExpanded) {
      rowsHtml.push(`
        <tr class="turns-detail-row" id="turns-row-${escapeHtml(s.session_id)}">
          <td colspan="11">
            <div id="turns-container-${escapeHtml(s.session_id)}">Loading turn breakdown...</div>
          </td>
        </tr>
      `);
    }
  }

  tbody.innerHTML = rowsHtml.join("");

  tbody.querySelectorAll(".session-row").forEach((tr) => {
    tr.addEventListener("click", async () => {
      const sid = tr.getAttribute("data-session-id");
      if (!sid) return;
      if (state.expandedSessions.has(sid)) {
        state.expandedSessions.delete(sid);
        renderSessionsTable();
      } else {
        state.expandedSessions.add(sid);
        renderSessionsTable();
        await fetchAndRenderSessionTurns(sid);
      }
    });
  });

  for (const sid of state.expandedSessions) {
    fetchAndRenderSessionTurns(sid);
  }
}

async function fetchAndRenderSessionTurns(sessionId) {
  const container = document.getElementById(`turns-container-${sessionId}`);
  if (!container) return;

  const qs = new URLSearchParams();
  if (state.selectedBucketEpoch !== null) {
    qs.set("bucket_epoch", String(state.selectedBucketEpoch));
  }

  try {
    const resp = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/turns?${qs.toString()}`);
    const data = await resp.json();
    const turns = data.turns || [];
    if (!turns.length) {
      container.innerHTML = `<em>No turns recorded in this interval.</em>`;
      return;
    }

    container.innerHTML = `
      <div style="margin-bottom:0.4rem;font-weight:600;color:var(--text-secondary);">
        Turn-by-Turn Token Breakdown (${turns.length} event${turns.length === 1 ? "" : "s"})
      </div>
      <table class="turns-subtable">
        <thead>
          <tr>
            <th>Turn #</th>
            <th>Timestamp</th>
            <th>15m Bucket</th>
            <th>Interface</th>
            <th>Model</th>
            <th class="num">Tokens In</th>
            <th class="num">Tokens Out</th>
            <th class="num">Cached Tokens</th>
            <th class="num">Cache Write</th>
            <th class="num">Total</th>
          </tr>
        </thead>
        <tbody>
          ${turns
            .map(
              (t) => `
            <tr>
              <td>#${t.turn_index}</td>
              <td>${fmtTimestamp(t.timestamp)}</td>
              <td>${fmtBucketWindow(t.bucket_15m_epoch)}</td>
              <td>${escapeHtml(t.interface)}</td>
              <td>${escapeHtml(t.model)}</td>
              <td class="num" style="color:var(--color-in)">${fmtNum(t.tokens_in)}</td>
              <td class="num" style="color:var(--color-out)">${fmtNum(t.tokens_out)}</td>
              <td class="num" style="color:var(--color-cached)">${fmtNum(t.tokens_cached)}</td>
              <td class="num">${fmtNum(t.tokens_cache_write)}</td>
              <td class="num"><strong>${fmtNum(t.total_tokens)}</strong></td>
            </tr>
          `
            )
            .join("")}
        </tbody>
      </table>
    `;
  } catch (err) {
    container.innerHTML = `<span style="color:#f87171">Failed to load turns: ${escapeHtml(err.message)}</span>`;
  }
}

// ------------------------------------------------------------------
// Filter & Modal Event Wiring
// ------------------------------------------------------------------

function setupControls() {
  document.getElementById("userSelect").addEventListener("change", (e) => {
    state.userId = e.target.value;
    loadDashboard();
  });

  document.getElementById("rangeSelect").addEventListener("change", (e) => {
    state.rangeHours = e.target.value;
    state.selectedBucketEpoch = null;
    loadDashboard();
  });

  document.querySelectorAll("#providerPills .pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#providerPills .pill").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.provider = btn.getAttribute("data-value") || "all";
      loadDashboard();
    });
  });

  document.querySelectorAll("#interfacePills .pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#interfacePills .pill").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.interface = btn.getAttribute("data-value") || "all";
      loadDashboard();
    });
  });

  let searchTimer = null;
  document.getElementById("sessionSearchInput").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.searchQuery = e.target.value;
      loadDashboard();
    }, 200);
  });

  document.getElementById("btnClearBucket").addEventListener("click", () => {
    state.selectedBucketEpoch = null;
    loadDashboard();
  });

  document.getElementById("btnPrevBucket").addEventListener("click", () => {
    if (state.selectedBucketEpoch !== null) {
      state.selectedBucketEpoch -= 900;
      loadDashboard();
    }
  });

  document.getElementById("btnNextBucket").addEventListener("click", () => {
    if (state.selectedBucketEpoch !== null) {
      state.selectedBucketEpoch += 900;
      loadDashboard();
    }
  });

  document.getElementById("btnSyncNow").addEventListener("click", async () => {
    const btn = document.getElementById("btnSyncNow");
    btn.disabled = true;
    try {
      await fetch("/api/sync", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      await loadDashboard();
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("btnToggleDemo").addEventListener("click", async () => {
    const endpoint = state.hasDemoData ? "/api/demo/clear" : "/api/demo/seed";
    await fetch(endpoint, { method: "POST" });
    await loadDashboard();
  });

  // Accounts Modal
  const modal = document.getElementById("accountsModal");
  document.getElementById("btnOpenAccounts").addEventListener("click", () => {
    renderConfiguredAccountsModal();
    modal.classList.remove("hidden");
  });
  document.getElementById("btnCloseAccounts").addEventListener("click", () => {
    modal.classList.add("hidden");
  });

  const authTypeSelect = document.getElementById("modalAuthTypeSelect");
  authTypeSelect.addEventListener("change", () => {
    const val = authTypeSelect.value;
    document.getElementById("fieldRootPath").classList.toggle("hidden", val !== "local_fs");
    document.getElementById("fieldApiKey").classList.toggle("hidden", val !== "api_key");
    document.getElementById("fieldSessionToken").classList.toggle("hidden", val !== "session_cookie");
  });

  document.getElementById("addUserForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    await fetch("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: fd.get("username"),
        display_name: fd.get("display_name"),
        home_dir: fd.get("home_dir"),
      }),
    });
    e.target.reset();
    await loadDashboard();
    renderConfiguredAccountsModal();
  });

  document.getElementById("addAccountForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const authType = fd.get("auth_type");
    const prov = fd.get("provider");
    const cfg = {};
    if (authType === "local_fs") {
      cfg.root_path = fd.get("root_path") || `~/.${prov}`;
    } else if (authType === "api_key") {
      cfg.api_key = fd.get("api_key");
    } else if (authType === "session_cookie") {
      const tok = fd.get("session_token");
      if (prov === "claude") cfg.session_key = tok;
      else if (prov === "codex") cfg.bearer_token = tok;
      else cfg.cookie = tok;
    }

    await fetch("/api/accounts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: fd.get("user_id"),
        provider: prov,
        account_label: fd.get("account_label"),
        auth_type: authType,
        config_json: cfg,
      }),
    });
    e.target.reset();
    await loadDashboard();
    renderConfiguredAccountsModal();
  });
}

function renderConfiguredAccountsModal() {
  const listEl = document.getElementById("configuredAccountsList");
  if (!listEl) return;
  if (!state.accounts.length) {
    listEl.innerHTML = `<div class="account-card-meta">No accounts configured yet.</div>`;
    return;
  }

  listEl.innerHTML = state.accounts
    .map(
      (a) => `
      <div class="account-card">
        <div>
          <div>
            <span class="${providerBadgeClass(a.provider)}">${escapeHtml(a.provider.toUpperCase())}</span>
            <strong style="margin-left:0.4rem;">${escapeHtml(a.account_label)}</strong>
          </div>
          <div class="account-card-meta" style="margin-top:0.2rem;">
            User: <code>${escapeHtml(a.user_id)}</code> &bull; Type: <code>${escapeHtml(a.auth_type)}</code>
            ${a.last_sync_at ? `&bull; Synced: ${fmtTimestamp(a.last_sync_at)}` : ""}
          </div>
        </div>
        <button class="btn btn-xs btn-danger btn-delete-acct" data-account-id="${escapeHtml(a.account_id)}">Remove</button>
      </div>
    `
    )
    .join("");

  listEl.querySelectorAll(".btn-delete-acct").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-account-id");
      if (!id) return;
      await fetch(`/api/accounts/${encodeURIComponent(id)}`, { method: "DELETE" });
      await loadDashboard();
      renderConfiguredAccountsModal();
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupControls();
  setupChartInteractions();
  loadDashboard();
  setInterval(() => loadDashboard(), 30000);
});

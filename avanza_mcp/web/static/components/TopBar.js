// Session/account switchers, metric cards, clock, mode badges, tab nav.
import { defineComponent, ref, computed, onMounted, onUnmounted } from "vue";
import { store } from "../store.js";
import { activateSession, selectAccount, manualRefresh, logoutSession } from "../actions.js";
import { api } from "../api.js";
import { currentTheme, toggleTheme } from "../theme.js";
import { toast } from "../store.js";

const PROFIT_ORDER = ["day", "week", "month", "year", "since_start", "total"];

const PROFIT_HINTS = {
  day: "Account development over the last trading day (Avanza performance figure).",
  week: "Account development over the last week (time-weighted, includes realized gains).",
  month: "Account development over the last month (time-weighted, includes realized gains).",
  year: "Account development over the last year (time-weighted, includes realized gains and dividends).",
  since_start: "Account development since the account was opened.",
  total: "Unrealized profit on CURRENT holdings vs their acquisition cost. Realized gains from closed trades are not included — after selling winners this resets toward zero.",
};

export default defineComponent({
  name: "TopBar",
  props: { tab: { type: String, required: true } },
  emits: ["change-tab", "add-session", "reauth-session", "logout-session", "open-order", "open-stoploss", "open-overlay"],
  setup(props, { emit }) {
    const profitMode = ref("day");
    const clock = ref("");
    let clockTimer = 0;

    const activeSession = computed(() => store.sessions.find((s) => s.session_id === store.activeSessionId));
    const account = computed(() => store.portfolio?.account || {});
    const metrics = computed(() => store.portfolio?.metrics || {});
    const profit = computed(() => metrics.value[profitMode.value] || { label: "1D P/L", amount: null, percent: null, unit: "SEK" });

    function cycleProfit() {
      const index = PROFIT_ORDER.indexOf(profitMode.value);
      profitMode.value = PROFIT_ORDER[(index + 1) % PROFIT_ORDER.length];
    }

    function fmtProfit(p) {
      if (p.amount === null && p.percent === null) return "-";
      const parts = [];
      if (p.amount !== null) parts.push(`${p.amount >= 0 ? "+" : ""}${p.amount.toFixed(2)} ${p.unit}`);
      if (p.percent !== null) parts.push(`(${p.percent >= 0 ? "+" : ""}${p.percent.toFixed(2)}%)`);
      return parts.join(" ");
    }

    function profitClass(p) {
      const v = p.amount ?? p.percent;
      if (v === null || v === undefined) return "";
      return v >= 0 ? "up" : "down";
    }

    onMounted(() => {
      clock.value = store.portfolio?.clock || "";
      clockTimer = setInterval(() => {
        if (store.portfolio?.clock) clock.value = store.portfolio.clock;
      }, 1000);
    });
    onUnmounted(() => clearInterval(clockTimer));

    async function onSessionChange(event) {
      const value = event.target.value;
      if (value === "__add__") { emit("add-session"); event.target.value = store.activeSessionId; return; }
      if (value && value !== store.activeSessionId) await activateSession(value);
    }

    const theme = ref(currentTheme());
    function onToggleTheme() { theme.value = toggleTheme(); }

    async function togglePaperMode() {
      const next = !store.meta.paper_mode;
      try {
        const result = await api.post("/api/paper/mode", { enabled: next, acknowledge: !next });
        store.meta.paper_mode = result.paper_mode;
        toast(result.paper_mode ? "Paper mode ON" : "Paper mode OFF — live tickets", result.paper_mode ? "info" : "warning");
      } catch (exc) { toast(exc.message, "error"); }
    }

    async function logoutActive() {
      if (!store.activeSessionId) return;
      if (!confirm("Log out the active session?")) return;
      try { await logoutSession(store.activeSessionId); } catch (exc) { toast(exc.message, "error"); }
    }

    async function onAccountChange(event) {
      const value = event.target.value;
      if (value && value !== store.portfolio?.account_id) await selectAccount(value);
    }

    return {
      store, props, emit, profitMode, cycleProfit, fmtProfit, profitClass, PROFIT_HINTS,
      activeSession, account, profit, clock, onSessionChange, onAccountChange, manualRefresh,
      togglePaperMode, logoutActive, theme, onToggleTheme,
    };
  },
  template: `
    <header class="topbar">
      <div class="topbar-main">
        <div class="brand">
          <span class="dot" :style="{ background: activeSession?.color || 'var(--accent)' }"></span>
          <strong>Avanza-MCP</strong>
          <span class="muted mono">v{{ store.meta.app_version }}</span>
        </div>

        <div class="switchers">
          <label class="switcher">
            <span>Session</span>
            <select :value="store.activeSessionId || ''" @change="onSessionChange">
              <option v-for="s in store.sessions" :key="s.session_id" :value="s.session_id">
                {{ s.label }}{{ s.auth_valid ? "" : " [EXPIRED]" }}
              </option>
              <option value="__add__">+ Add session…</option>
            </select>
          </label>
          <label class="switcher">
            <span>Account</span>
            <select :value="store.portfolio?.account_id || ''" @change="onAccountChange">
              <option v-for="a in (activeSession?.accounts || [])" :key="a.id" :value="a.id">
                {{ a.name }} [{{ a.type }}]
              </option>
            </select>
          </label>
          <span v-if="activeSession && !activeSession.auth_valid" class="badge expired"
                role="button" tabindex="0" @click="emit('reauth-session', activeSession.session_id)">
            EXPIRED — re-auth
          </span>
        </div>

        <div class="metrics">
          <div class="metric"><span class="metric-label">Total</span><span class="metric-value">{{ account.total_value || "-" }}</span></div>
          <div class="metric"><span class="metric-label">Buying</span><span class="metric-value">{{ account.buying_power || "-" }}</span></div>
          <button class="metric metric-btn" @click="cycleProfit"
                  :title="(PROFIT_HINTS[profitMode] || '') + ' Click to cycle.'">
            <span class="metric-label">{{ profit.label }}</span>
            <span class="metric-value" :class="profitClass(profit)">{{ fmtProfit(profit) }}</span>
          </button>
          <div class="metric"><span class="metric-label">Status</span><span class="metric-value">{{ account.status || "-" }}</span></div>
        </div>

        <div class="topbar-right">
          <span v-if="store.meta.update?.outdated" class="badge warn-text" :title="store.meta.update.text">update</span>
          <span class="clock mono muted">{{ clock }}</span>
          <button class="ghost icon-btn" @click="onToggleTheme" :title="theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'">
            {{ theme === "light" ? "☾" : "☀" }}
          </button>
          <button class="ghost icon-btn" @click="manualRefresh" title="Refresh now (r)">⟳</button>
          <button class="ghost icon-btn" @click="logoutActive" title="Log out active session">⎋</button>
        </div>
      </div>

      <div class="topbar-tools">
        <nav class="tabs" role="tablist">
          <button v-for="t in ['dashboard', 'paper', 'mcp']" :key="t"
                  role="tab" :aria-selected="props.tab === t"
                  :class="{ active: props.tab === t }" @click="emit('change-tab', t)">
            {{ t === 'mcp' ? 'MCP' : t.charAt(0).toUpperCase() + t.slice(1) }}
          </button>
        </nav>
        <div class="toolbar-group">
          <button class="ghost" @click="emit('open-overlay', 'orders')">Orders</button>
          <button class="ghost" @click="emit('open-overlay', 'transactions')">Transactions</button>
          <button class="ghost" @click="emit('open-overlay', 'tv')">TradingView lists</button>
        </div>
        <div class="toolbar-group trade-toolbar">
          <button class="primary" @click="emit('open-order')" title="Order ticket (o)">+ Order</button>
          <button class="warn" @click="emit('open-stoploss')" title="Stop-loss ticket (s)">+ Stop-Loss</button>
        </div>
        <button class="badge mode-toggle" :class="store.meta.paper_mode ? 'paper' : 'live'"
                @click="togglePaperMode" :title="store.meta.paper_mode ? 'Paper mode on — click for LIVE ticket mode' : 'LIVE tickets — click for paper mode'">
          {{ store.meta.paper_mode ? "PAPER" : "LIVE" }}
        </button>
      </div>
    </header>
  `,
});

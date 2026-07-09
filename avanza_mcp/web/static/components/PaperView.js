// Paper trading workspace: positions, orders, trades, summary, risk state.
import { defineComponent, ref, computed, onMounted } from "vue";
import { api } from "../api.js";
import { store } from "../store.js";
import DataTable from "./DataTable.js";

export default defineComponent({
  name: "PaperView",
  components: { DataTable },
  setup() {
    const state = ref(null);
    const error = ref("");
    const loading = ref(true);

    async function load() {
      loading.value = true;
      try {
        state.value = await api.get("/api/paper/state");
        error.value = "";
      } catch (exc) {
        error.value = exc.payload?.detail || exc.message;
      } finally { loading.value = false; }
    }
    onMounted(load);

    const positionCols = [
      { key: "instrument", label: "Instrument" },
      { key: "state", label: "State" },
      { key: "volume", label: "Volume", numeric: true },
      { key: "entry_price", label: "Entry", numeric: true },
      { key: "exit_price", label: "Exit", numeric: true },
      { key: "realized_pnl", label: "P/L", numeric: true, cellClass: (r) => (Number(r.realized_pnl) >= 0 ? "up" : "down") },
      { key: "opened_at", label: "Opened" },
    ];
    const orderCols = [
      { key: "instrument", label: "Instrument" },
      { key: "kind", label: "Kind" },
      { key: "state", label: "State" },
      { key: "created_at", label: "Created" },
      { key: "id", label: "ID" },
    ];
    const tradeCols = [
      { key: "instrument", label: "Instrument" },
      { key: "side", label: "Side" },
      { key: "volume", label: "Volume", numeric: true },
      { key: "price", label: "Price", numeric: true },
      { key: "pnl", label: "P/L", numeric: true, cellClass: (r) => (Number(r.pnl) >= 0 ? "up" : "down") },
      { key: "at", label: "Time" },
    ];

    const summary = computed(() => state.value?.summary || {});
    const risk = computed(() => state.value?.risk || null);

    return { store, state, error, loading, load, positionCols, orderCols, tradeCols, summary, risk };
  },
  template: `
    <div class="paper-grid">
      <section class="panel" style="grid-column: 1 / -1;">
        <div class="panel-title">
          <h2>Paper Session</h2>
          <div style="display:flex; gap:8px; align-items:center">
            <span class="badge paper">LEDGER</span>
            <button class="ghost" @click="load">⟳</button>
          </div>
        </div>
        <div v-if="error" class="error">{{ error }}</div>
        <div v-else-if="loading" class="skeleton" style="height: 60px"></div>
        <div v-else class="summary-cards">
          <div class="metric"><span class="metric-label">Orders</span><span class="metric-value">{{ (state.orders || []).length }}</span></div>
          <div class="metric"><span class="metric-label">Active</span><span class="metric-value">{{ (state.active_orders || []).length }}</span></div>
          <div class="metric"><span class="metric-label">Trades</span><span class="metric-value">{{ (state.trades || []).length }}</span></div>
          <div class="metric" v-for="(value, key) in summary" :key="key" v-show="typeof value !== 'object'">
            <span class="metric-label">{{ key.replaceAll("_", " ") }}</span>
            <span class="metric-value num">{{ value }}</span>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-title"><h2>Positions</h2></div>
        <DataTable :columns="positionCols" :rows="state?.positions || []" rowKey="id" emptyText="No paper positions" />
      </section>
      <section class="panel">
        <div class="panel-title"><h2>Orders</h2></div>
        <DataTable :columns="orderCols" :rows="state?.orders || []" rowKey="id" emptyText="No paper orders" />
      </section>
      <section class="panel" style="grid-column: 1 / -1;">
        <div class="panel-title"><h2>Trades</h2></div>
        <DataTable :columns="tradeCols" :rows="state?.trades || []" emptyText="No paper trades" />
      </section>

      <section v-if="risk" class="panel" style="grid-column: 1 / -1;">
        <div class="panel-title"><h2>Risk State</h2></div>
        <pre class="mono muted" style="margin:0; font-size: var(--fs-tiny); white-space: pre-wrap;">{{ JSON.stringify(risk, null, 2) }}</pre>
      </section>
    </div>
  `,
});

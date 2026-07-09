// Selected-account holdings with buy/sell shortcuts and realtime dots.
import { defineComponent, computed } from "vue";
import { store } from "../store.js";
import DataTable from "./DataTable.js";

export default defineComponent({
  name: "PortfolioTable",
  components: { DataTable },
  emits: ["trade"],
  setup(_, { emit }) {
    const columns = [
      { key: "Stock", label: "Stock" },
      { key: "_buy", label: "B" },
      { key: "_sell", label: "S" },
      { key: "Volume", label: "Volume", numeric: true },
      { key: "Value", label: "Value", numeric: true },
      { key: "Avg Price", label: "Avg Price", numeric: true },
      { key: "Day %", label: "Day %", numeric: true, cellClass: (r) => signClass(r["Day %"]) },
      { key: "Day SEK", label: "Day", numeric: true, cellClass: (r) => signClass(r["Day SEK"]) },
      { key: "Profit %", label: "Profit %", numeric: true, cellClass: (r) => signClass(r["Profit %"]) },
      { key: "Profit", label: "Profit", numeric: true, cellClass: (r) => signClass(r["Profit"]) },
      { key: "Real-time", label: "RT" },
    ];

    function signClass(value) {
      const n = parseFloat(String(value ?? "").replace(/[^\d.-]/g, ""));
      if (Number.isNaN(n) || n === 0) return "";
      return n > 0 ? "up" : "down";
    }

    const rows = computed(() => store.portfolio?.rows || []);
    const loading = computed(() => store.portfolio === null && store.sessions.length > 0);

    function rtClass(value) {
      const v = String(value || "").toLowerCase();
      if (v.includes("real")) return "realtime";
      if (v.includes("delay")) return "delayed";
      return "unknown";
    }

    return { columns, rows, loading, emit, rtClass };
  },
  template: `
    <section class="panel">
      <div class="panel-title">
        <h2>Selected Account Stocks</h2>
        <span class="muted">{{ rows.length }} positions</span>
      </div>
      <div v-if="loading" class="skeleton" style="height: 180px;"></div>
      <DataTable v-else :columns="columns" :rows="rows" rowKey="Order Book ID" emptyText="No positions">
        <template #cell-_buy="{ row }">
          <button class="cell-btn buy" @click="emit('trade', { side: 'buy', row })" :aria-label="'Buy ' + row.Stock">B</button>
        </template>
        <template #cell-_sell="{ row }">
          <button class="cell-btn sell" @click="emit('trade', { side: 'sell', row })" :aria-label="'Sell ' + row.Stock">S</button>
        </template>
        <template #cell-Real-time="{ row }">
          <span class="rt-dot" :class="rtClass(row['Real-time'])" :title="row['Real-time']"></span>
        </template>
      </DataTable>
    </section>
  `,
});

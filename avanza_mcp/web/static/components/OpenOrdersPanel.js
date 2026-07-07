// Ongoing (open) orders with cancel shortcuts.
import { defineComponent, computed } from "vue";
import { store } from "../store.js";
import DataTable from "./DataTable.js";

export default defineComponent({
  name: "OpenOrdersPanel",
  components: { DataTable },
  emits: ["cancel"],
  setup(_, { emit }) {
    const columns = [
      { key: "Stock", label: "Stock" },
      { key: "Side", label: "Side", cellClass: (r) => (String(r.Side).toUpperCase() === "BUY" ? "up" : "down") },
      { key: "Volume", label: "Volume", numeric: true },
      { key: "Price", label: "Price", numeric: true },
      { key: "Valid Until", label: "Valid Until" },
      { key: "Status", label: "Status" },
      { key: "_cancel", label: "" },
    ];
    const rows = computed(() => store.openOrders || []);
    return { columns, rows, emit };
  },
  template: `
    <section class="panel">
      <div class="panel-title"><h2>Ongoing Orders</h2><span class="muted">{{ rows.length }}</span></div>
      <DataTable :columns="columns" :rows="rows" rowKey="Order ID" emptyText="No open orders">
        <template #cell-_cancel="{ row }">
          <button class="cell-btn cancel" @click="emit('cancel', { kind: 'order', row })" :aria-label="'Cancel order for ' + row.Stock">×</button>
        </template>
      </DataTable>
    </section>
  `,
});

// Active stop-losses: live + paper merged, Mode column, cancel/edit.
import { defineComponent, computed } from "vue";
import { store } from "../store.js";
import DataTable from "./DataTable.js";

export default defineComponent({
  name: "ActiveTradesPanel",
  components: { DataTable },
  emits: ["cancel", "edit"],
  setup(_, { emit }) {
    const columns = [
      { key: "mode", label: "Mode", cellClass: (r) => (r.mode === "Paper" ? "warn-text" : "") },
      { key: "stock", label: "Stock" },
      { key: "side", label: "Side", cellClass: (r) => (String(r.side).toUpperCase() === "BUY" ? "up" : "down") },
      { key: "volume", label: "Vol", numeric: true },
      { key: "trigger", label: "Trigger/Price" },
      { key: "valid", label: "Valid/Created" },
      { key: "status", label: "Status" },
      { key: "_act", label: "" },
    ];

    const rows = computed(() => {
      const live = (store.stoplosses || []).map((item) => ({
        mode: "Live",
        id: item.stop_loss_id || item["Stop Loss ID"],
        kind: "Stop-loss",
        stock: item.stock || item.Stock,
        side: item.side || item.Side || "",
        volume: item.volume ?? item.Volume,
        trigger: item.Trigger || item.trigger || "",
        valid: item["Valid Until"] || "",
        status: item.status || item.Status || "",
        account_id: item.account_id || item["Account ID"] || "",
        raw: item,
      }));
      const paper = (store.paperStoplosses || []).map((item) => ({
        mode: "Paper",
        id: item.id,
        kind: item.kind,
        stock: item.stock,
        side: item.side,
        volume: item.volume,
        trigger: item.trigger_or_price,
        valid: item.valid_or_created,
        status: item.status,
        account_id: "",
        raw: item,
      }));
      return [...live, ...paper];
    });

    return { columns, rows, emit };
  },
  template: `
    <section class="panel">
      <div class="panel-title"><h2>Active Stop-Losses</h2><span class="muted">{{ rows.length }}</span></div>
      <DataTable :columns="columns" :rows="rows" rowKey="id" emptyText="No active stop-losses">
        <template #cell-_act="{ row }">
          <button v-if="row.mode === 'Live'" class="cell-btn" style="color: var(--info)"
                  @click="emit('edit', row)" :aria-label="'Edit stop-loss for ' + row.stock">✎</button>
          <button class="cell-btn cancel" @click="emit('cancel', { kind: row.mode === 'Paper' ? 'paper' : 'stoploss', row })"
                  :aria-label="'Cancel ' + row.stock">×</button>
        </template>
      </DataTable>
    </section>
  `,
});

// Full-page stop-loss list: readable overview of configured live/paper stops.
import { defineComponent, computed, watch } from "vue";
import { store } from "../store.js";
import { hydrateStoplosses } from "../actions.js";
import DataTable from "./DataTable.js";

function liveStopLossRow(item) {
  return {
    mode: "Live",
    id: item.stop_loss_id || item["Stop Loss ID"],
    kind: "Stop-loss",
    account: item.account_name || item.Account || "",
    account_id: item.account_id || item["Account ID"] || "",
    stock: item.stock || item.Stock || "",
    orderbook_id: item.orderbook_id || item["Order Book ID"] || "",
    side: item.side || item.Side || "",
    volume: item.volume ?? item.Volume,
    trigger: item.Trigger || item.trigger || "",
    order: item.Order || "",
    valid_until: item.valid_until || item["Valid Until"] || "",
    status: item.status || item.Status || "",
    order_valid_days: item.order_valid_days || "",
    raw: item,
  };
}

function paperStopLossRow(item) {
  return {
    mode: "Paper",
    id: item.id,
    kind: item.kind || "Stop-loss",
    account: "",
    account_id: "",
    stock: item.stock || "",
    orderbook_id: "",
    side: item.side || "",
    volume: item.volume,
    trigger: item.trigger_or_price || "",
    order: "",
    valid_until: item.valid_or_created || "",
    status: item.status || "",
    order_valid_days: "",
    raw: item,
  };
}

export default defineComponent({
  name: "StopLossesOverlay",
  components: { DataTable },
  props: {
    open: { type: Boolean, default: false },
  },
  emits: ["close", "cancel", "edit"],
  setup(props, { emit }) {
    const columns = [
      { key: "mode", label: "Mode", cellClass: (r) => (r.mode === "Paper" ? "warn-text" : "up") },
      { key: "status", label: "Status" },
      { key: "account", label: "Account" },
      { key: "stock", label: "Stock" },
      { key: "side", label: "Side", cellClass: (r) => (String(r.side).toUpperCase() === "BUY" ? "up" : "down") },
      { key: "volume", label: "Volume", numeric: true },
      { key: "trigger", label: "Trigger" },
      { key: "order", label: "Order" },
      { key: "valid_until", label: "Valid Until" },
      { key: "order_valid_days", label: "Order Days", numeric: true },
      { key: "_actions", label: "" },
    ];

    const rows = computed(() => [
      ...(store.stoplosses || []).map(liveStopLossRow),
      ...(store.paperStoplosses || []).map(paperStopLossRow),
    ]);

    watch(() => props.open, (isOpen) => {
      if (isOpen) hydrateStoplosses();
    });

    return { props, emit, columns, rows, hydrateStoplosses };
  },
  template: `
    <div v-if="props.open" class="overlay fade-in" role="dialog" aria-modal="true">
      <div class="overlay-head">
        <h2>Configured Stop-Losses</h2>
        <span class="muted">{{ rows.length }} active</span>
        <button class="ghost" @click="hydrateStoplosses">Refresh</button>
        <button class="ghost" @click="emit('close')" aria-label="Close">✕</button>
      </div>
      <div class="overlay-body">
        <DataTable :columns="columns" :rows="rows" rowKey="id" emptyText="No configured stop-losses">
          <template #cell-_actions="{ row }">
            <button v-if="row.mode === 'Live'" class="cell-btn" style="color: var(--info)"
                    @click="emit('edit', row)" :aria-label="'Edit stop-loss for ' + row.stock">Edit</button>
            <button class="cell-btn cancel"
                    @click="emit('cancel', { kind: row.mode === 'Paper' ? 'paper' : 'stoploss', row })"
                    :aria-label="'Cancel stop-loss for ' + row.stock">×</button>
          </template>
        </DataTable>
      </div>
    </div>
  `,
});

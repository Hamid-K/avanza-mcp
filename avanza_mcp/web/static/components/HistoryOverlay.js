// Full-screen overlay for orders history / transactions (shared shell).
import { defineComponent, ref, watch } from "vue";
import { api } from "../api.js";
import { store } from "../store.js";
import DataTable from "./DataTable.js";

const ALL_TRANSACTION_TYPES = "DIVIDEND,BUY,SELL,WITHDRAW,DEPOSIT,UNKNOWN";

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

function defaultDateRange() {
  const today = new Date();
  let year = today.getFullYear();
  let month = today.getMonth() - 1;
  if (month < 0) {
    month = 11;
    year -= 1;
  }
  const lastDay = new Date(year, month + 1, 0).getDate();
  const from = new Date(year, month, Math.min(today.getDate(), lastDay));
  return { from: isoDate(from), to: isoDate(today) };
}

function firstValue(row, keys, fallback = "") {
  for (const key of keys) {
    const value = row?.[key];
    if (value !== null && value !== undefined && value !== "") return value;
  }
  return fallback;
}

function signClass(value) {
  const number = parseFloat(String(value ?? "").replace(/[^\d.-]/g, ""));
  if (Number.isNaN(number) || number === 0) return "";
  return number > 0 ? "up" : "down";
}

function normalizeHistoryRow(row) {
  const stock = firstValue(row, ["stock", "Stock"]);
  const description = firstValue(row, ["description", "Description"], stock);
  const result = firstValue(row, ["pl_sek", "P/L SEK", "profit_loss_sek", "result", "Result"]);
  return {
    ...row,
    trade_date: firstValue(row, ["trade_date", "Trade Date", "date", "Date"]),
    account_name: firstValue(row, ["account_name", "Account", "account"]),
    side: firstValue(row, ["side", "Side", "type", "Type"]),
    stock,
    type: firstValue(row, ["type", "Type"]),
    description,
    volume: firstValue(row, ["volume", "Volume"]),
    price: firstValue(row, ["price", "Price"]),
    amount: firstValue(row, ["amount", "Amount"]),
    result,
    pl_sek: result,
    isin: firstValue(row, ["isin", "ISIN"]),
  };
}

export default defineComponent({
  name: "HistoryOverlay",
  components: { DataTable },
  props: {
    open: { type: Boolean, default: false },
    mode: { type: String, default: "orders" }, // orders | transactions
  },
  emits: ["close"],
  setup(props, { emit }) {
    const rows = ref([]);
    const loading = ref(false);
    const error = ref("");
    const fromDate = ref("");
    const toDate = ref("");

    const orderCols = [
      { key: "trade_date", label: "Date" },
      { key: "side", label: "Side", cellClass: (r) => (String(r.side).toUpperCase() === "BUY" ? "up" : "down") },
      { key: "stock", label: "Stock" },
      { key: "volume", label: "Qty", numeric: true },
      { key: "price", label: "Price", numeric: true },
      { key: "amount", label: "Amount", numeric: true },
      { key: "account_name", label: "Account" },
    ];
    const txCols = [
      { key: "trade_date", label: "Date" },
      { key: "account_name", label: "Account" },
      { key: "type", label: "Type" },
      { key: "description", label: "Description" },
      { key: "volume", label: "Qty", numeric: true },
      { key: "price", label: "Price", numeric: true },
      { key: "amount", label: "Amount", numeric: true },
      { key: "pl_sek", label: "P/L SEK", numeric: true, cellClass: (r) => signClass(r.pl_sek) },
      { key: "isin", label: "ISIN" },
    ];

    async function load() {
      loading.value = true; error.value = "";
      try {
        const params = new URLSearchParams();
        if (!fromDate.value || !toDate.value) {
          const range = defaultDateRange();
          if (!fromDate.value) fromDate.value = range.from;
          if (!toDate.value) toDate.value = range.to;
        }
        if (fromDate.value) params.set("from_date", fromDate.value);
        if (toDate.value) params.set("to_date", toDate.value);
        if (props.mode === "orders") params.set("types", "BUY,SELL");
        else params.set("types", ALL_TRANSACTION_TYPES);
        const payload = await api.get(`/api/transactions?${params}`);
        rows.value = (payload.transactions || payload.items || []).map(normalizeHistoryRow);
      } catch (exc) {
        error.value = exc.payload?.detail || exc.message;
        rows.value = [];
      } finally { loading.value = false; }
    }

    watch(() => props.open, (isOpen) => {
      if (isOpen) {
        const range = defaultDateRange();
        if (!fromDate.value) fromDate.value = range.from;
        if (!toDate.value) toDate.value = range.to;
        load();
      }
    });
    watch(() => store.contextRevision, () => {
      if (props.open) load();
    });

    return { props, emit, rows, loading, error, fromDate, toDate, load, orderCols, txCols };
  },
  template: `
    <div v-if="props.open" class="overlay fade-in" role="dialog" aria-modal="true">
      <div class="overlay-head">
        <h2>{{ props.mode === "orders" ? "Completed Orders" : "Transactions" }}</h2>
        <div class="overlay-filters">
          <label>From <input type="date" v-model="fromDate"></label>
          <label>To <input type="date" v-model="toDate"></label>
          <button @click="load" :disabled="loading">Apply</button>
        </div>
        <button class="ghost" @click="emit('close')" aria-label="Close">✕</button>
      </div>
      <div class="overlay-body">
        <div v-if="error" class="error">{{ error }}</div>
        <div v-else-if="loading" class="skeleton" style="height: 200px"></div>
        <DataTable v-else :columns="props.mode === 'orders' ? orderCols : txCols" :rows="rows"
                   :emptyText="props.mode === 'orders' ? 'No completed orders' : 'No transactions'" />
      </div>
    </div>
  `,
});

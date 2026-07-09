// TradingView custom lists overlay: 15s auto-refresh while open.
import { defineComponent, ref, watch, onUnmounted } from "vue";
import { api } from "../api.js";
import { store } from "../store.js";
import DataTable from "./DataTable.js";

const REFRESH_MS = 15000;

export default defineComponent({
  name: "TvListsOverlay",
  components: { DataTable },
  props: { open: { type: Boolean, default: false } },
  emits: ["close"],
  setup(props, { emit }) {
    const lists = ref([]);
    const selected = ref("");
    const rows = ref([]);
    const loading = ref(false);
    const error = ref("");
    const notice = ref("");
    let timer = 0;

    const columns = [
      { key: "symbol", label: "Symbol" },
      { key: "last", label: "Last", numeric: true },
      { key: "change", label: "Chg", numeric: true, cellClass: (r) => (Number(r.change) >= 0 ? "up" : "down") },
      { key: "change_percent", label: "Chg%", numeric: true, cellClass: (r) => (Number(r.change_percent) >= 0 ? "up" : "down") },
      { key: "volume", label: "Volume", numeric: true },
      { key: "market_state", label: "Status" },
    ];

    async function load() {
      loading.value = true; error.value = ""; notice.value = "";
      try {
        const params = selected.value ? `?list_id=${encodeURIComponent(selected.value)}` : "";
        const payload = await api.get(`/api/tv/lists${params}`);
        lists.value = payload.lists || payload.watchlists || [];
        rows.value = payload.rows || payload.items || payload.symbols || [];
        notice.value = payload.warning || "";
        const selectedId = payload.selected_list ? String(payload.selected_list.id || payload.selected_list.list_id || "") : "";
        const knownSelected = lists.value.some((item) => String(item.id || item.list_id || "") === selected.value);
        if (selectedId) selected.value = selectedId;
        else if ((!selected.value || !knownSelected) && lists.value.length) {
          selected.value = String(lists.value[0].id || lists.value[0].list_id || "");
        }
      } catch (exc) {
        error.value = exc.payload?.detail || exc.message;
      } finally { loading.value = false; }
    }

    function schedule() {
      clearInterval(timer);
      timer = setInterval(() => { if (props.open) load(); }, REFRESH_MS);
    }

    watch(() => props.open, (isOpen) => {
      if (isOpen) { load(); schedule(); } else { clearInterval(timer); }
    });
    watch(selected, () => { if (props.open) load(); });
    watch(() => store.contextRevision, () => { if (props.open) load(); });
    onUnmounted(() => clearInterval(timer));

    return { props, emit, lists, selected, rows, loading, error, notice, columns, load };
  },
  template: `
    <div v-if="props.open" class="overlay fade-in" role="dialog" aria-modal="true">
      <div class="overlay-head">
        <h2>TradingView Lists <span class="muted" style="font-weight:400">(auto-refresh 15s)</span></h2>
        <div class="overlay-filters">
          <select v-model="selected" style="min-width: 200px">
            <option v-for="l in lists" :key="l.id || l.list_id" :value="String(l.id || l.list_id)">
              {{ l.name || l.title || (l.id || l.list_id) }}
            </option>
          </select>
          <button @click="load" :disabled="loading">⟳</button>
        </div>
        <button class="ghost" @click="emit('close')" aria-label="Close">✕</button>
      </div>
      <div class="overlay-body">
        <div v-if="error" class="error">{{ error }}</div>
        <div v-if="notice" class="notice warn-text">{{ notice }}</div>
        <div v-else-if="loading && !rows.length" class="skeleton" style="height: 200px"></div>
        <DataTable v-if="!error && !(loading && !rows.length)" :columns="columns" :rows="rows"
                   emptyText="No TradingView symbols available" />
      </div>
    </div>
  `,
});

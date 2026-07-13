// Research candidates overlay: source-ranked read-only stock ideas.
import { computed, defineComponent, ref, watch } from "vue";
import { api } from "../api.js";
import { store } from "../store.js";
import DataTable from "./DataTable.js";

const CORE_SOURCE_FILTERS = [
  "TradingView heatmap",
  "TradingView technicals",
  "Zacks",
];

function rowSources(row) {
  return Array.isArray(row?.sources)
    ? row.sources.map((source) => String(source || "").trim()).filter(Boolean)
    : [];
}

function fmtNumber(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(number);
}

function fmtPct(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return `${number >= 0 ? "+" : ""}${number.toFixed(2)}%`;
}

function fmtCompact(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 2 }).format(number);
}

export default defineComponent({
  name: "RecommendationsOverlay",
  components: { DataTable },
  props: { open: { type: Boolean, default: false } },
  emits: ["close"],
  setup(props, { emit }) {
    const rows = ref([]);
    const warnings = ref([]);
    const sources = ref([]);
    const sourceHealth = ref([]);
    const disclaimer = ref("");
    const asOf = ref("");
    const loading = ref(false);
    const error = ref("");
    const limit = ref(25);
    const enrichLimit = ref(8);
    const includeFmp = ref(false);
    const enabledSources = ref({});

    const sourceFilters = computed(() => {
      const labels = [...CORE_SOURCE_FILTERS];
      for (const source of sources.value) {
        const label = String(source || "").trim();
        if (label && !labels.includes(label)) labels.push(label);
      }
      return labels.map((label) => {
        const health = sourceHealth.value.find((entry) => entry?.source === label) || {};
        return {
          label,
          count: rows.value.filter((row) => rowSources(row).includes(label)).length,
          attempted: Number(health.attempted || 0),
          succeeded: Number(health.succeeded || 0),
          failed: Number(health.failed || 0),
        };
      });
    });

    function sourceFilterTitle(filter) {
      const parts = [`${filter.label}: ${filter.count} candidates`];
      if (filter.attempted) parts.push(`${filter.succeeded}/${filter.attempted} enrichment checks succeeded`);
      if (filter.failed) parts.push(`${filter.failed} failed; hover row warning icons for details`);
      return parts.join(" · ");
    }

    function isSourceEnabled(source) {
      return enabledSources.value[source] !== false;
    }

    function toggleSource(source) {
      enabledSources.value = {
        ...enabledSources.value,
        [source]: !isSourceEnabled(source),
      };
    }

    const filteredRows = computed(() => {
      const enabled = new Set(
        sourceFilters.value
          .filter(({ label }) => isSourceEnabled(label))
          .map(({ label }) => label),
      );
      if (!enabled.size) return [];
      return rows.value.filter((row) => rowSources(row).some((source) => enabled.has(source)));
    });

    const filterEmptyText = computed(() => (
      rows.value.length
        ? "No candidates match the enabled sources"
        : "No research candidates available"
    ));

    const columns = [
      { key: "rank", label: "#", numeric: true },
      { key: "score", label: "Score", numeric: true, format: fmtNumber },
      { key: "symbol_full", label: "Symbol" },
      { key: "name", label: "Name" },
      { key: "tv_rating", label: "TradingView" },
      { key: "zacks_rank", label: "Zacks" },
      { key: "change_percent", label: "Chg%", numeric: true, format: fmtPct, cellClass: (r) => (Number(r.change_percent) >= 0 ? "up" : "down") },
      { key: "last", label: "Last", numeric: true, format: fmtNumber },
      { key: "relative_volume", label: "Rel vol", numeric: true, format: fmtNumber },
      { key: "volume", label: "Volume", numeric: true, format: fmtCompact },
      { key: "sector", label: "Sector" },
      { key: "reason", label: "Why" },
    ];

    async function load() {
      loading.value = true;
      error.value = "";
      try {
        const params = new URLSearchParams();
        params.set("limit", String(limit.value));
        params.set("enrich_limit", String(enrichLimit.value));
        if (includeFmp.value) params.set("include_fmp", "true");
        const payload = await api.get(`/api/recommendations/stocks?${params}`);
        rows.value = payload.rows || [];
        warnings.value = payload.warnings || [];
        sources.value = payload.sources || [];
        sourceHealth.value = payload.source_health || [];
        disclaimer.value = payload.disclaimer || "";
        asOf.value = payload.as_of || "";
      } catch (exc) {
        error.value = exc.payload?.detail || exc.message;
        rows.value = [];
      } finally {
        loading.value = false;
      }
    }

    watch(() => props.open, (isOpen) => { if (isOpen) load(); });
    watch(() => store.contextRevision, () => { if (props.open) load(); });

    return {
      props, emit, rows, warnings, sources, disclaimer, asOf, loading, error,
      limit, enrichLimit, includeFmp, columns, load, sourceFilters, filteredRows,
      filterEmptyText, isSourceEnabled, toggleSource, sourceFilterTitle,
    };
  },
  template: `
    <div v-if="props.open" class="overlay fade-in research-overlay" role="dialog" aria-modal="true">
      <div class="overlay-head">
        <h2>Research candidates <span class="muted" style="font-weight:400">source-ranked</span></h2>
        <div class="overlay-filters">
          <label>Rows <input type="number" min="1" max="50" v-model.number="limit"></label>
          <label>Deep checks <input type="number" min="0" max="12" v-model.number="enrichLimit"></label>
          <label class="checkline"><input type="checkbox" v-model="includeFmp"> FMP</label>
          <button @click="load" :disabled="loading">⟳</button>
        </div>
        <button class="ghost" @click="emit('close')" aria-label="Close">✕</button>
      </div>
      <div class="overlay-body research-body">
        <div class="research-summary">
          <div>
            <div class="muted">Assembled from</div>
            <div class="source-chip-row" role="group" aria-label="Filter candidates by source">
              <button v-for="filter in sourceFilters" :key="filter.label" type="button"
                      class="source-filter" :class="{ active: isSourceEnabled(filter.label), empty: !filter.count }"
                      :aria-pressed="isSourceEnabled(filter.label)"
                      :title="sourceFilterTitle(filter)"
                      @click="toggleSource(filter.label)">
                <span class="source-filter-state" aria-hidden="true">{{ isSourceEnabled(filter.label) ? "✓" : "×" }}</span>
                <span>{{ filter.label }}</span>
                <span class="source-filter-count mono">{{ filter.count }}</span>
                <span v-if="filter.failed" class="source-filter-fail mono" aria-label="failed enrichment checks">!{{ filter.failed }}</span>
              </button>
            </div>
            <div class="source-filter-result muted">Showing {{ filteredRows.length }} of {{ rows.length }}</div>
          </div>
          <div>
            <div class="muted">Last update</div>
            <div class="mono">{{ asOf || "-" }}</div>
          </div>
          <div>
            <div class="muted">Use</div>
            <div :title="disclaimer">Research input only</div>
          </div>
        </div>
        <div v-for="warning in warnings" :key="warning" class="notice warn-text">{{ warning }}</div>
        <div v-if="error" class="error">{{ error }}</div>
        <div v-else-if="loading && !rows.length" class="skeleton" style="height: 240px"></div>
        <DataTable v-else :columns="columns" :rows="filteredRows" rowKey="symbol_full"
                   :emptyText="filterEmptyText">
          <template #cell-score="{ row }">
            <span class="score-pill" :class="Number(row.score) >= 25 ? 'hot' : Number(row.score) <= 0 ? 'cold' : ''">
              {{ Number(row.score || 0).toFixed(1) }}
            </span>
          </template>
          <template #cell-symbol_full="{ row }">
            <span class="mono">{{ row.symbol_full || row.symbol }}</span>
          </template>
          <template #cell-name="{ row }">
            <span>{{ row.name }}</span>
            <span v-if="row.errors?.length" class="warn-text" :title="row.errors.map(e => e.source + ': ' + e.error).join('\\n')"> ⚠</span>
          </template>
          <template #cell-zacks_rank="{ row }">
            <span :title="[row.zacks_note, row.zacks_error].filter(Boolean).join(' · ')">
              {{ row.zacks_rank || "n/a" }}
            </span>
          </template>
          <template #cell-reason="{ row }">
            <span :title="row.reason">{{ row.reason || "-" }}</span>
          </template>
        </DataTable>
      </div>
    </div>
  `,
});

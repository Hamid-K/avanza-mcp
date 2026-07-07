// Account performance area chart via TradingView lightweight-charts.
import { defineComponent, ref, onMounted, onUnmounted, watch } from "vue";
import { api } from "../api.js";
import { store } from "../store.js";

const PERIODS = [
  ["ONE_WEEK", "1W"], ["ONE_MONTH", "1M"], ["THREE_MONTHS", "3M"],
  ["YTD", "YTD"], ["ONE_YEAR", "1Y"], ["THREE_YEARS", "3Y"], ["SINCE_START", "All"],
];

export default defineComponent({
  name: "PerformanceChart",
  setup() {
    const host = ref(null);
    const period = ref("ONE_MONTH");
    const loading = ref(false);
    const error = ref("");
    const summaryText = ref("");
    let chart = null;
    let series = null;
    let resizeObserver = null;

    async function load() {
      if (!store.sessions.length) return;
      loading.value = true;
      error.value = "";
      try {
        const payload = await api.get(`/api/performance?period=${period.value}`);
        const points = (payload.chart_points || [])
          .map((p) => {
            let time = String(p.date || "").slice(0, 10);
            if (!time && p.timestamp) time = new Date(Number(p.timestamp)).toISOString().slice(0, 10);
            const value = p.account_value?.value ?? p.development_absolute?.value;
            return { time, value: Number(value) };
          })
          .filter((p) => p.time && Number.isFinite(p.value));
        const percent = payload.development_relative?.value;
        if (percent !== undefined && percent !== null) {
          summaryText.value = `${percent >= 0 ? "+" : ""}${Number(percent).toFixed(2)}%`;
        }
        draw(points);
      } catch (exc) {
        error.value = exc.payload?.detail || exc.message;
      } finally {
        loading.value = false;
      }
    }

    let lastPoints = [];

    function cssVar(name) {
      return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    function draw(points) {
      lastPoints = points;
      if (!host.value || typeof LightweightCharts === "undefined") return;
      if (!chart) {
        chart = LightweightCharts.createChart(host.value, {
          layout: { background: { color: "transparent" }, textColor: cssVar("--muted"), fontSize: 11 },
          grid: { vertLines: { color: cssVar("--panel-3") }, horzLines: { color: cssVar("--panel-3") } },
          rightPriceScale: { borderColor: cssVar("--border") },
          timeScale: { borderColor: cssVar("--border") },
          height: 200,
          autoSize: true,
        });
        const accent = cssVar("--session-color") || "#3b82f6";
        series = chart.addAreaSeries({
          lineColor: accent, topColor: accent + "40", bottomColor: accent + "05",
          lineWidth: 2, priceLineVisible: false,
        });
      }
      series.setData(points);
      chart.timeScale().fitContent();
    }

    function rebuildChart() {
      if (chart) { chart.remove(); chart = null; series = null; }
      draw(lastPoints);
    }

    onMounted(() => {
      load();
      resizeObserver = new ResizeObserver(() => chart && chart.applyOptions({}));
      if (host.value) resizeObserver.observe(host.value);
      window.addEventListener("themechange", rebuildChart);
    });
    onUnmounted(() => {
      if (resizeObserver) resizeObserver.disconnect();
      window.removeEventListener("themechange", rebuildChart);
      if (chart) { chart.remove(); chart = null; series = null; }
    });
    watch(period, load);
    watch(() => store.activeSessionId, load);
    watch(() => store.portfolio?.account_id, load);

    return { host, period, loading, error, summaryText, PERIODS };
  },
  template: `
    <section class="panel">
      <div class="panel-title">
        <h2>Performance <span class="muted num" style="font-weight: 400">{{ summaryText }}</span></h2>
        <div class="pill-row" role="tablist">
          <button v-for="[value, label] in PERIODS" :key="value" class="pill"
                  :class="{ active: period === value }" @click="period = value">{{ label }}</button>
        </div>
      </div>
      <div v-if="error" class="muted" style="padding: 12px">{{ error }}</div>
      <div ref="host" class="chart-host" :class="{ dim: loading }"></div>
    </section>
  `,
});

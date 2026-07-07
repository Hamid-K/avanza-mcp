// Account performance area chart via TradingView lightweight-charts.
import { defineComponent, ref, onMounted, onUnmounted, watch } from "vue";
import { api } from "../api.js";
import { store } from "../store.js";

const PERIODS = [
  ["week", "1W"], ["month", "1M"], ["three_months", "3M"],
  ["this_year", "YTD"], ["one_year", "1Y"], ["three_years", "3Y"],
];

export default defineComponent({
  name: "PerformanceChart",
  setup() {
    const host = ref(null);
    const period = ref("month");
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
        const points = (payload.chart_points || payload.points || [])
          .map((p) => ({ time: String(p.date || p.time || "").slice(0, 10), value: Number(p.value ?? p.equity ?? 0) }))
          .filter((p) => p.time && Number.isFinite(p.value));
        const summary = payload.summary || {};
        if (summary.percent !== undefined && summary.percent !== null) {
          summaryText.value = `${summary.percent >= 0 ? "+" : ""}${Number(summary.percent).toFixed(2)}%`;
        }
        draw(points);
      } catch (exc) {
        error.value = exc.payload?.detail || exc.message;
      } finally {
        loading.value = false;
      }
    }

    function draw(points) {
      if (!host.value || typeof LightweightCharts === "undefined") return;
      if (!chart) {
        chart = LightweightCharts.createChart(host.value, {
          layout: { background: { color: "transparent" }, textColor: "#7d8899", fontSize: 11 },
          grid: { vertLines: { color: "#1b2130" }, horzLines: { color: "#1b2130" } },
          rightPriceScale: { borderColor: "#232a38" },
          timeScale: { borderColor: "#232a38" },
          height: 200,
          autoSize: true,
        });
        series = chart.addAreaSeries({
          lineColor: "#3b82f6", topColor: "rgba(59,130,246,0.25)", bottomColor: "rgba(59,130,246,0.02)",
          lineWidth: 2, priceLineVisible: false,
        });
      }
      series.setData(points);
      chart.timeScale().fitContent();
    }

    onMounted(() => {
      load();
      resizeObserver = new ResizeObserver(() => chart && chart.applyOptions({}));
      if (host.value) resizeObserver.observe(host.value);
    });
    onUnmounted(() => {
      if (resizeObserver) resizeObserver.disconnect();
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

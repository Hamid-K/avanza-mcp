// Account performance chart: P/L (SEK or %) with balance + cash-flow overlay.
//
// SEK mode: profit/loss as a zero-baseline area (green above / red below),
// account balance as a subdued dashed line on the left scale, and deposits/
// withdrawals as markers. % mode: relative development. Axis and legend
// numbers use sv-SE grouping ("934 671"), never unbroken zero-runs.
import { defineComponent, ref, computed, nextTick, onMounted, onUnmounted, watch } from "vue";
import { api } from "../api.js";
import { store } from "../store.js";

const PERIODS = [
  ["ONE_WEEK", "1W"], ["ONE_MONTH", "1M"], ["THREE_MONTHS", "3M"],
  ["YTD", "YTD"], ["ONE_YEAR", "1Y"], ["THREE_YEARS", "3Y"], ["SINCE_START", "All"],
];

const sekFormat = new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 0 });
const sekFormatFine = new Intl.NumberFormat("sv-SE", { minimumFractionDigits: 0, maximumFractionDigits: 2 });

function fmtSek(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "–";
  return sekFormat.format(Number(value));
}

function fmtSekSigned(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "–";
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${sekFormatFine.format(n)}`;
}

function fmtPercentSigned(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "–";
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function pointTime(point) {
  let time = String(point.date || "").slice(0, 10);
  if (!time && point.timestamp) time = new Date(Number(point.timestamp)).toISOString().slice(0, 10);
  return time;
}

export default defineComponent({
  name: "PerformanceChart",
  setup() {
    const host = ref(null);
    const period = ref("ONE_MONTH");
    const mode = ref("SEK"); // SEK | %
    const loading = ref(false);
    const error = ref("");
    const payload = ref(null);
    let chart = null;
    let resizeObserver = null;
    let loadSequence = 0;

    const summary = computed(() => {
      const p = payload.value;
      if (!p) return null;
      const points = p.chart_points || [];
      const last = [...points].reverse().find(
        (pt) => pt.account_value?.value !== null && pt.account_value?.value !== undefined
      );
      return {
        plSek: p.development_absolute?.value,
        plPct: p.development_relative?.value,
        balance: last?.account_value?.value,
        cashEventCount: (p.cash_events || []).length,
      };
    });

    async function load() {
      if (!store.sessions.length) return;
      const sequence = ++loadSequence;
      const selectedPeriod = period.value;
      loading.value = true;
      error.value = "";
      try {
        const params = new URLSearchParams();
        params.set("period", selectedPeriod);
        if (store.portfolio?.account_id) params.set("account_id", store.portfolio.account_id);
        params.set("_", String(Date.now()));
        const nextPayload = await api.get(`/api/performance?${params}`);
        if (sequence !== loadSequence || selectedPeriod !== period.value) return;
        payload.value = nextPayload;
        await nextTick();
        draw();
      } catch (exc) {
        if (sequence !== loadSequence) return;
        error.value = exc.payload?.detail || exc.message;
      } finally {
        if (sequence === loadSequence) loading.value = false;
      }
    }

    function setPeriod(value) {
      if (period.value === value) {
        load();
      } else {
        period.value = value;
      }
    }

    function cssVar(name) {
      return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    }

    function seriesData(field) {
      return (payload.value?.chart_points || [])
        .map((pt) => ({ time: pointTime(pt), value: pt[field]?.value }))
        .filter((pt) => pt.time && pt.value !== null && pt.value !== undefined && Number.isFinite(Number(pt.value)))
        .map((pt) => ({ time: pt.time, value: Number(pt.value) }));
    }

    function cashMarkers(availableTimes) {
      const events = payload.value?.cash_events || [];
      if (!events.length || !availableTimes.length) return [];
      const times = [...availableTimes].sort();
      // Snap each event to the nearest charted day at or after it — markers
      // must land on an existing bar time or lightweight-charts drops them.
      return events
        .map((event) => {
          const day = String(event.date || "").slice(0, 10);
          if (!day) return null;
          const snapped = times.find((t) => t >= day) || times[times.length - 1];
          const isDeposit = event.type === "DEPOSIT";
          const amount = Math.abs(Number(event.amount) || 0);
          return {
            time: snapped,
            position: isDeposit ? "belowBar" : "aboveBar",
            shape: isDeposit ? "arrowUp" : "arrowDown",
            color: isDeposit ? cssVar("--up") : cssVar("--down"),
            text: `${isDeposit ? "+" : "−"}${fmtSek(amount)}`,
          };
        })
        .filter(Boolean)
        .sort((a, b) => (a.time < b.time ? -1 : 1));
    }

    function draw() {
      if (!host.value || typeof LightweightCharts === "undefined" || !payload.value) return;
      if (chart) { chart.remove(); chart = null; }
      host.value.replaceChildren();
      const isSek = mode.value === "SEK";
      chart = LightweightCharts.createChart(host.value, {
        layout: { background: { color: "transparent" }, textColor: cssVar("--muted"), fontSize: 11 },
        grid: { vertLines: { color: cssVar("--panel-3") }, horzLines: { color: cssVar("--panel-3") } },
        rightPriceScale: { borderColor: cssVar("--border") },
        leftPriceScale: { visible: isSek, borderColor: cssVar("--border") },
        timeScale: { borderColor: cssVar("--border") },
        localization: {
          priceFormatter: isSek ? (v) => fmtSek(v) : (v) => `${v.toFixed(2)}%`,
        },
        height: 220,
        autoSize: true,
      });

      if (isSek) {
        const up = cssVar("--up");
        const down = cssVar("--down");
        const pl = chart.addBaselineSeries({
          baseValue: { type: "price", price: 0 },
          topLineColor: up,
          topFillColor1: up + "40",
          topFillColor2: up + "08",
          bottomLineColor: down,
          bottomFillColor1: down + "08",
          bottomFillColor2: down + "40",
          lineWidth: 2,
          priceLineVisible: false,
        });
        const plData = seriesData("development_absolute");
        pl.setData(plData);

        const balance = chart.addLineSeries({
          priceScaleId: "left",
          color: cssVar("--faint"),
          lineWidth: 1,
          lineStyle: 2, // dashed
          priceLineVisible: false,
          lastValueVisible: false,
        });
        const balanceData = seriesData("account_value");
        balance.setData(balanceData);
        const markerHost = balanceData.length ? balance : pl;
        const markerTimes = (balanceData.length ? balanceData : plData).map((p) => p.time);
        markerHost.setMarkers(cashMarkers(markerTimes));
      } else {
        const accent = cssVar("--session-color") || "#3b82f6";
        const rel = chart.addAreaSeries({
          lineColor: accent,
          topColor: accent + "40",
          bottomColor: accent + "05",
          lineWidth: 2,
          priceLineVisible: false,
        });
        rel.setData(seriesData("development_relative"));
      }
      chart.timeScale().fitContent();
    }

    onMounted(() => {
      load();
      resizeObserver = new ResizeObserver(() => chart && chart.applyOptions({}));
      if (host.value) resizeObserver.observe(host.value);
      window.addEventListener("themechange", draw);
    });
    onUnmounted(() => {
      if (resizeObserver) resizeObserver.disconnect();
      window.removeEventListener("themechange", draw);
      if (chart) { chart.remove(); chart = null; }
    });
    watch(period, load);
    watch(mode, draw);
    watch(() => store.activeSessionId, load);
    watch(() => store.portfolio?.account_id, load);

    return {
      host, period, mode, loading, error, summary, PERIODS,
      fmtSek, fmtSekSigned, fmtPercentSigned, setPeriod,
    };
  },
  template: `
    <section class="panel">
      <div class="panel-title">
        <h2>Performance</h2>
        <div class="pill-row" role="tablist">
          <button class="pill" :class="{ active: mode === 'SEK' }" @click="mode = 'SEK'"
                  title="Profit/loss in SEK with balance and cash-flow overlay">SEK</button>
          <button class="pill" :class="{ active: mode === '%' }" @click="mode = '%'"
                  title="Relative development">%</button>
          <span class="pill-divider" aria-hidden="true"></span>
          <button v-for="[value, label] in PERIODS" :key="value" class="pill"
                  :class="{ active: period === value }" @click="setPeriod(value)">{{ label }}</button>
        </div>
      </div>
      <div v-if="summary" class="chart-legend num">
        <span :class="(summary.plSek ?? 0) >= 0 ? 'up' : 'down'">
          P/L {{ fmtSekSigned(summary.plSek) }} SEK ({{ fmtPercentSigned(summary.plPct) }})
        </span>
        <span class="muted" v-if="mode === 'SEK' && summary.balance !== null && summary.balance !== undefined">
          Balance <span class="legend-dash">┄</span> {{ fmtSek(summary.balance) }} SEK
        </span>
        <span class="muted" v-if="mode === 'SEK' && summary.cashEventCount">
          <span class="up">▲</span>/<span class="down">▼</span> {{ summary.cashEventCount }} cash flow{{ summary.cashEventCount === 1 ? "" : "s" }} in window
        </span>
      </div>
      <div v-if="error" class="muted" style="padding: 12px">{{ error }}</div>
      <div ref="host" class="chart-host" :class="{ dim: loading }"></div>
    </section>
  `,
});

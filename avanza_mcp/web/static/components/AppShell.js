// Authenticated application shell: topbar + dashboard grid + modals.
import { defineComponent, ref, computed, onMounted, onUnmounted } from "vue";
import { store } from "../store.js";
import { manualRefresh, logoutSession } from "../actions.js";
import TopBar from "./TopBar.js";
import SessionLoginModal from "./SessionLoginModal.js";
import PortfolioTable from "./PortfolioTable.js";
import OpenOrdersPanel from "./OpenOrdersPanel.js";
import ActiveTradesPanel from "./ActiveTradesPanel.js";
import PerformanceChart from "./PerformanceChart.js";
import ActivityLog from "./ActivityLog.js";

export default defineComponent({
  name: "AppShell",
  components: {
    TopBar, SessionLoginModal, PortfolioTable, OpenOrdersPanel,
    ActiveTradesPanel, PerformanceChart, ActivityLog,
  },
  setup() {
    const tab = ref("dashboard");
    const sessionModalOpen = ref(false);
    const reauthSessionId = ref("");
    const needsFirstSession = computed(() => !store.sessions.length);

    function openAddSession() { reauthSessionId.value = ""; sessionModalOpen.value = true; }
    function openReauth(sessionId) { reauthSessionId.value = sessionId; sessionModalOpen.value = true; }

    function onTrade() { /* order ticket lands in the trading phase */ }
    function onCancel() { /* cancel dialog lands in the trading phase */ }
    function onEditStopLoss() { /* stop-loss editor lands in the trading phase */ }

    function onKey(event) {
      if (event.target.matches("input, select, textarea")) return;
      if (event.key === "r") manualRefresh();
      else if (event.key === "p") tab.value = "paper";
      else if (event.key === "m") tab.value = "mcp";
      else if (event.key === "d") tab.value = "dashboard";
      else if (event.key === "Escape") sessionModalOpen.value = false;
    }
    onMounted(() => window.addEventListener("keydown", onKey));
    onUnmounted(() => window.removeEventListener("keydown", onKey));

    return {
      store, tab, sessionModalOpen, reauthSessionId, needsFirstSession,
      openAddSession, openReauth, onTrade, onCancel, onEditStopLoss, logoutSession,
    };
  },
  template: `
    <div class="shell">
      <TopBar :tab="tab" @change-tab="tab = $event" @add-session="openAddSession" @reauth-session="openReauth" />

      <main v-if="needsFirstSession" class="first-session">
        <div class="login-card fade-in">
          <h1>No Avanza session</h1>
          <div class="sub">Sign in to an Avanza account to load your portfolio.</div>
          <button class="primary" @click="openAddSession">Sign in to Avanza</button>
        </div>
      </main>

      <main v-else-if="tab === 'dashboard'" class="dashboard-grid">
        <div class="col-main">
          <PortfolioTable @trade="onTrade" />
          <OpenOrdersPanel @cancel="onCancel" />
        </div>
        <div class="col-side">
          <PerformanceChart />
          <ActiveTradesPanel @cancel="onCancel" @edit="onEditStopLoss" />
          <ActivityLog />
        </div>
      </main>

      <main v-else-if="tab === 'paper'" class="single-panel">
        <div class="panel"><div class="panel-title"><h2>Paper Trading</h2></div>
          <div class="muted" style="padding: 16px">Paper view lands in a later build phase.</div>
        </div>
      </main>

      <main v-else class="single-panel">
        <div class="panel"><div class="panel-title"><h2>MCP</h2></div>
          <div class="muted" style="padding: 16px">MCP management lands in a later build phase.</div>
        </div>
      </main>

      <SessionLoginModal :open="sessionModalOpen" :reauthSessionId="reauthSessionId"
                         @close="sessionModalOpen = false" />
    </div>
  `,
});

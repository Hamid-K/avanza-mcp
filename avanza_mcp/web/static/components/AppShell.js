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
import OrderTicket from "./OrderTicket.js";
import StopLossTicket from "./StopLossTicket.js";
import CancelDialog from "./CancelDialog.js";
import McpPanel from "./McpPanel.js";
import PaperView from "./PaperView.js";
import HistoryOverlay from "./HistoryOverlay.js";
import TvListsOverlay from "./TvListsOverlay.js";

export default defineComponent({
  name: "AppShell",
  components: {
    TopBar, SessionLoginModal, PortfolioTable, OpenOrdersPanel,
    ActiveTradesPanel, PerformanceChart, ActivityLog,
    OrderTicket, StopLossTicket, CancelDialog, McpPanel,
    PaperView, HistoryOverlay, TvListsOverlay,
  },
  setup() {
    const tab = ref("dashboard");
    const sessionModalOpen = ref(false);
    const reauthSessionId = ref("");
    const needsFirstSession = computed(() => !store.sessions.length);
    const sideWidth = ref(loadLayoutNumber("sideWidth", 360));
    const portfolioHeight = ref(loadLayoutNumber("portfolioHeight", 420));
    const ongoingHeight = ref(loadLayoutNumber("ongoingHeight", 210));
    const resizing = ref(null);

    function loadLayoutNumber(key, fallback) {
      try {
        const value = Number(localStorage.getItem(`avanza.web.layout.${key}`));
        return Number.isFinite(value) && value > 0 ? value : fallback;
      } catch {
        return fallback;
      }
    }

    function saveLayoutNumber(key, value) {
      try { localStorage.setItem(`avanza.web.layout.${key}`, String(Math.round(value))); } catch {}
    }

    function clamp(value, min, max) {
      return Math.max(min, Math.min(max, value));
    }

    const dashboardStyle = computed(() => ({
      "--side-pane-width": `${sideWidth.value}px`,
      "--portfolio-pane-height": `${portfolioHeight.value}px`,
      "--ongoing-pane-height": `${ongoingHeight.value}px`,
    }));

    function startResize(kind, event) {
      event.preventDefault();
      resizing.value = {
        kind,
        startX: event.clientX,
        startY: event.clientY,
        sideWidth: sideWidth.value,
        portfolioHeight: portfolioHeight.value,
        ongoingHeight: ongoingHeight.value,
      };
      document.body.classList.add("is-resizing");
      window.addEventListener("pointermove", onResizeMove);
      window.addEventListener("pointerup", stopResize, { once: true });
    }

    function onResizeMove(event) {
      const state = resizing.value;
      if (!state) return;
      if (state.kind === "side") {
        sideWidth.value = clamp(state.sideWidth + (state.startX - event.clientX), 280, 760);
      } else if (state.kind === "portfolio") {
        portfolioHeight.value = clamp(state.portfolioHeight + (event.clientY - state.startY), 220, 760);
      } else if (state.kind === "ongoing") {
        ongoingHeight.value = clamp(state.ongoingHeight + (event.clientY - state.startY), 120, 520);
      }
    }

    function stopResize() {
      if (resizing.value) {
        saveLayoutNumber("sideWidth", sideWidth.value);
        saveLayoutNumber("portfolioHeight", portfolioHeight.value);
        saveLayoutNumber("ongoingHeight", ongoingHeight.value);
      }
      resizing.value = null;
      document.body.classList.remove("is-resizing");
      window.removeEventListener("pointermove", onResizeMove);
    }

    function openAddSession() { reauthSessionId.value = ""; sessionModalOpen.value = true; }
    function openReauth(sessionId) { reauthSessionId.value = sessionId; sessionModalOpen.value = true; }

    const orderTicketOpen = ref(false);
    const orderPrefill = ref(null);
    const stopLossOpen = ref(false);
    const stopLossEditTarget = ref(null);
    const cancelTarget = ref(null);
    const overlay = ref(""); // "" | orders | transactions | tv

    function onTrade({ side, row }) {
      orderPrefill.value = {
        side,
        order_book_id: row["Order Book ID"],
        name: row.Stock,
        volume: side === "sell" ? row.volume : "",
      };
      orderTicketOpen.value = true;
    }
    function openOrderTicket() { orderPrefill.value = null; orderTicketOpen.value = true; }
    function openStopLoss() { stopLossEditTarget.value = null; stopLossOpen.value = true; }
    function onCancel({ kind, row }) { cancelTarget.value = { kind, row }; }
    function onEditStopLoss(row) { stopLossEditTarget.value = row; stopLossOpen.value = true; }

    function onKey(event) {
      if (event.target.matches("input, select, textarea")) return;
      if (event.key === "r") manualRefresh();
      else if (event.key === "o") openOrderTicket();
      else if (event.key === "s") openStopLoss();
      else if (event.key === "p") tab.value = "paper";
      else if (event.key === "m") tab.value = "mcp";
      else if (event.key === "d") tab.value = "dashboard";
      else if (event.key === "Escape") {
        sessionModalOpen.value = false;
        orderTicketOpen.value = false;
        stopLossOpen.value = false;
        cancelTarget.value = null;
        overlay.value = "";
      }
    }
    onMounted(() => window.addEventListener("keydown", onKey));
    onUnmounted(() => {
      window.removeEventListener("keydown", onKey);
      stopResize();
    });

    return {
      store, tab, sessionModalOpen, reauthSessionId, needsFirstSession,
      openAddSession, openReauth, onTrade, onCancel, onEditStopLoss, logoutSession,
      orderTicketOpen, orderPrefill, stopLossOpen, stopLossEditTarget, cancelTarget,
      openOrderTicket, openStopLoss, overlay, dashboardStyle, startResize,
    };
  },
  template: `
    <div class="shell">
      <TopBar :tab="tab"
              @change-tab="tab = $event"
              @add-session="openAddSession"
              @reauth-session="openReauth"
              @open-order="openOrderTicket"
              @open-stoploss="openStopLoss"
              @open-overlay="overlay = $event" />

      <main v-if="needsFirstSession && tab === 'dashboard'" class="first-session">
        <div class="login-card fade-in">
          <h1>No Avanza session</h1>
          <div class="sub">Sign in to an Avanza account to load your portfolio.</div>
          <button class="primary" @click="openAddSession">Sign in to Avanza</button>
        </div>
      </main>

      <main v-else-if="tab === 'dashboard'" class="dashboard-grid" :style="dashboardStyle">
        <div class="col-main dashboard-stack">
          <PortfolioTable @trade="onTrade" />
          <div class="resize-bar horizontal" role="separator" aria-label="Resize portfolio pane"
               @pointerdown="startResize('portfolio', $event)"></div>
          <OpenOrdersPanel @cancel="onCancel" />
          <div class="resize-bar horizontal" role="separator" aria-label="Resize ongoing orders pane"
               @pointerdown="startResize('ongoing', $event)"></div>
          <ActivityLog />
        </div>
        <div class="resize-bar vertical" role="separator" aria-label="Resize active stop-loss pane"
             @pointerdown="startResize('side', $event)"></div>
        <div class="col-side">
          <ActiveTradesPanel @cancel="onCancel" @edit="onEditStopLoss" />
          <PerformanceChart />
        </div>
      </main>

      <main v-else-if="tab === 'paper'" class="single-panel">
        <PaperView />
      </main>

      <main v-else class="single-panel">
        <McpPanel />
      </main>

      <HistoryOverlay :open="overlay === 'orders'" mode="orders" @close="overlay = ''" />
      <HistoryOverlay :open="overlay === 'transactions'" mode="transactions" @close="overlay = ''" />
      <TvListsOverlay :open="overlay === 'tv'" @close="overlay = ''" />

      <OrderTicket :open="orderTicketOpen" :prefill="orderPrefill" @close="orderTicketOpen = false" />
      <StopLossTicket :open="stopLossOpen" :editTarget="stopLossEditTarget" @close="stopLossOpen = false" />
      <CancelDialog :target="cancelTarget" @close="cancelTarget = null" />
      <SessionLoginModal :open="sessionModalOpen" :reauthSessionId="reauthSessionId"
                         @close="sessionModalOpen = false" />
    </div>
  `,
});

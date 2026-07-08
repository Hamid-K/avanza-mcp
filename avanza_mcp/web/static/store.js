// Single reactive store. WS frames land here; components read from here.
import { reactive } from "vue";

export const store = reactive({
  auth: { authenticated: false, checking: true },
  meta: { app_version: "", paper_mode: true, update: {}, has_session: false },
  wsState: "disconnected", // disconnected | connecting | connected
  sessions: [],
  activeSessionId: null,
  contextRevision: 0,
  accounts: [],
  selectedAccountId: null,
  portfolio: null, // { rows, metrics, realtime }
  openOrders: [],
  stoplosses: [],
  paperStoplosses: [],
  activityLog: [],
  paper: null,
  mcp: { running: false, read_write: false, live_trading: false, url: "", proxy_command: "" },
  mcpLog: [],
  toasts: [],
  loginProgress: null, // { message, index }
});

let toastId = 0;

export function toast(message, kind = "info", timeoutMs = 5000) {
  const id = ++toastId;
  store.toasts.push({ id, message, kind });
  if (timeoutMs > 0) {
    setTimeout(() => dismissToast(id), timeoutMs);
  }
}

export function dismissToast(id) {
  const index = store.toasts.findIndex((t) => t.id === id);
  if (index >= 0) store.toasts.splice(index, 1);
}

export function applySessionAccent() {
  const active = store.sessions.find((s) => s.session_id === store.activeSessionId);
  if (active && active.color) {
    document.documentElement.style.setProperty("--session-color", active.color);
  }
}

export function bumpContextRevision() {
  store.contextRevision += 1;
}

const MCP_LOG_LIMIT = 500;

export function handleWsFrame(frame) {
  const { type, payload } = frame;
  switch (type) {
    case "portfolio":
      if (payload) store.portfolio = payload;
      break;
    case "sessions":
      if (payload && payload.sessions) {
        store.sessions = payload.sessions;
        store.activeSessionId = payload.active_session_id;
        applySessionAccent();
      }
      break;
    case "orders":
      if (payload) store.openOrders = payload.items || [];
      break;
    case "stoplosses":
      if (payload) {
        store.stoplosses = payload.items || [];
        store.paperStoplosses = payload.paper_items || [];
      }
      break;
    case "paper":
      if (payload) store.paper = payload;
      break;
    case "mcp_status":
      if (payload) store.mcp = { ...store.mcp, ...payload };
      break;
    case "mcp_log":
      store.mcpLog.push(payload);
      if (store.mcpLog.length > MCP_LOG_LIMIT) store.mcpLog.shift();
      break;
    case "notice":
      if (payload) {
        store.activityLog.push(payload);
        if (store.activityLog.length > 300) store.activityLog.shift();
        if (payload.severity && payload.severity !== "information") {
          toast(payload.message, payload.severity === "error" ? "error" : "warning");
        }
      }
      break;
    case "login_progress":
      store.loginProgress = payload;
      break;
    case "update_check":
      if (payload) store.meta.update = payload;
      break;
    default:
      break;
  }
}

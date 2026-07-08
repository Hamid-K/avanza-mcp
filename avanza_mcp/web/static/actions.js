// REST hydration + commands. WS keeps the store fresh afterwards.
import { api } from "./api.js";
import { store, toast, applySessionAccent } from "./store.js";

export async function hydrateAll() {
  store.meta = await api.get("/api/meta");
  await hydrateSessions();
  const tasks = [hydrateMcp()];
  if (store.sessions.length) {
    tasks.push(hydratePortfolio(), hydrateOrders(), hydrateStoplosses());
  }
  await Promise.all(tasks);
}

export async function hydrateMcp() {
  store.mcp = await api.get("/api/mcp/status");
  store.mcpLog = (await api.get("/api/mcp/log")).entries || [];
}

export async function hydrateSessions() {
  const payload = await api.get("/api/sessions");
  store.sessions = payload.sessions;
  store.activeSessionId = payload.active_session_id;
  applySessionAccent();
}

export async function hydratePortfolio() {
  store.portfolio = await api.get("/api/portfolio");
}

export async function hydrateOrders() {
  store.openOrders = (await api.get("/api/orders/open")).items || [];
}

export async function hydrateStoplosses() {
  const payload = await api.get("/api/stoplosses");
  store.stoplosses = payload.items || [];
  store.paperStoplosses = payload.paper_items || [];
}

export async function addSession(body) {
  const result = await api.post("/api/sessions", body);
  store.sessions = result.sessions;
  store.activeSessionId = result.active_session_id;
  applySessionAccent();
  await hydrateAll();
  return result;
}

export async function activateSession(sessionId) {
  const result = await api.post(`/api/sessions/${encodeURIComponent(sessionId)}/activate`);
  store.sessions = result.sessions;
  store.activeSessionId = result.active_session_id;
  applySessionAccent();
  await Promise.all([hydratePortfolio(), hydrateOrders(), hydrateStoplosses()]);
}

export async function logoutSession(sessionId) {
  const result = await api.del(`/api/sessions/${encodeURIComponent(sessionId)}`);
  store.sessions = result.sessions;
  store.activeSessionId = result.active_session_id;
  applySessionAccent();
  if (store.sessions.length) {
    await Promise.all([hydratePortfolio(), hydrateOrders(), hydrateStoplosses()]);
  } else {
    store.portfolio = null;
    store.openOrders = [];
    store.stoplosses = [];
  }
}

export async function selectAccount(accountId) {
  await api.post(`/api/accounts/${encodeURIComponent(accountId)}/select`);
  await Promise.all([hydratePortfolio(), hydrateOrders(), hydrateStoplosses()]);
}

export async function manualRefresh() {
  try {
    await api.post("/api/refresh");
    toast("Refresh requested", "info", 1500);
  } catch (exc) {
    toast(`Refresh failed: ${exc.message}`, "error");
  }
}

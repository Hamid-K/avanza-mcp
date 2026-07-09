// WebSocket client with exponential-backoff reconnect.
import { store, handleWsFrame } from "./store.js";

let socket = null;
let backoffMs = 1000;
let closedByApp = false;
const MAX_BACKOFF_MS = 30000;

export function connectWs(onReconnected) {
  closedByApp = false;
  open(onReconnected);
}

export function disconnectWs() {
  closedByApp = true;
  if (socket) {
    socket.close();
    socket = null;
  }
  store.wsState = "disconnected";
}

function open(onReconnected) {
  if (closedByApp) return;
  store.wsState = "connecting";
  const url = `ws://${location.host}/ws`;
  socket = new WebSocket(url);

  socket.onopen = () => {
    const wasReconnect = backoffMs > 1000;
    store.wsState = "connected";
    backoffMs = 1000;
    if (wasReconnect && onReconnected) onReconnected();
  };

  socket.onmessage = (event) => {
    try {
      handleWsFrame(JSON.parse(event.data));
    } catch {
      /* malformed frame — ignore */
    }
  };

  socket.onclose = () => {
    socket = null;
    store.wsState = "disconnected";
    if (!closedByApp) {
      setTimeout(() => open(onReconnected), backoffMs);
      backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
    }
  };

  socket.onerror = () => {
    if (socket) socket.close();
  };
}

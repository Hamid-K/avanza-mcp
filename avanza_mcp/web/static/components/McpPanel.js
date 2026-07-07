// MCP management: bridge toggle, R/W, live-trading arming, token, log stream.
import { defineComponent, ref, onMounted, computed, nextTick, watch } from "vue";
import { api } from "../api.js";
import { store, toast } from "../store.js";

export default defineComponent({
  name: "McpPanel",
  setup() {
    const busy = ref(false);
    const error = ref("");
    const armAcknowledged = ref(false);
    const logHost = ref(null);
    const status = computed(() => store.mcp);

    async function refreshStatus() {
      store.mcp = await api.get("/api/mcp/status");
      const log = await api.get("/api/mcp/log");
      store.mcpLog = log.entries || [];
    }

    async function call(path, body) {
      busy.value = true; error.value = "";
      try {
        store.mcp = await api.post(path, body);
      } catch (exc) {
        error.value = exc.payload?.detail || exc.message;
      } finally { busy.value = false; }
    }

    const toggleBridge = () => call("/api/mcp/bridge", { enabled: !status.value.running });
    const toggleReadWrite = () => call("/api/mcp/read-write", { enabled: !status.value.read_write });

    async function armLive() {
      if (!armAcknowledged.value) return;
      await call("/api/mcp/live-trading", { enabled: true, acknowledge: true });
      if (!error.value) { armAcknowledged.value = false; toast("Live trading authorized for this session", "warning"); }
    }
    const revokeLive = () => call("/api/mcp/live-trading", { enabled: false });

    async function copy(text, label) {
      try { await navigator.clipboard.writeText(text); toast(`${label} copied`, "info", 1800); }
      catch { toast("Clipboard unavailable", "warning"); }
    }

    watch(() => store.mcpLog.length, async () => {
      await nextTick();
      if (logHost.value) logHost.value.scrollTop = logHost.value.scrollHeight;
    });

    onMounted(refreshStatus);
    return { store, status, busy, error, armAcknowledged, logHost,
             toggleBridge, toggleReadWrite, armLive, revokeLive, copy };
  },
  template: `
    <div class="mcp-grid">
      <section class="panel">
        <div class="panel-title"><h2>MCP Bridge</h2>
          <span class="badge" :class="status.running ? 'ok' : ''">{{ status.running ? "RUNNING" : "STOPPED" }}</span>
        </div>
        <div class="mcp-controls">
          <label class="toggle-row">
            <input type="checkbox" :checked="status.running" :disabled="busy" @change="toggleBridge">
            <div><strong>MCP bridge</strong>
              <div class="muted">Local HTTP bridge for MCP clients. Started on demand, random port, ephemeral token.</div>
            </div>
          </label>
          <label class="toggle-row">
            <input type="checkbox" :checked="status.read_write" :disabled="busy" @change="toggleReadWrite">
            <div><strong>Read/Write mode</strong>
              <div class="muted">Allow MCP tools to request mutations. Disabling revokes live-trading authorization.</div>
            </div>
          </label>
          <div class="toggle-row" :class="{ disabled: !status.read_write }">
            <input type="checkbox" :checked="status.live_trading" disabled>
            <div style="flex:1"><strong>Live trading authorization</strong>
              <div class="muted">Per-session arming for live MCP mutations. Requires R/W; each mutating call still needs confirm:true.</div>
              <div v-if="!status.live_trading" class="arm-live-box" :class="{ inactive: !status.read_write }">
                <label class="check-row arm-live-check">
                  <input type="checkbox" v-model="armAcknowledged" :disabled="busy || !status.read_write">
                  I understand MCP tools may place and cancel REAL orders
                </label>
                <button class="danger" :disabled="busy || !status.read_write || !armAcknowledged"
                        @click="armLive">Authorize live trading</button>
              </div>
              <div v-else style="margin-top:6px">
                <button class="warn" :disabled="busy" @click="revokeLive">Revoke live trading</button>
              </div>
            </div>
          </div>
        </div>
        <div class="error" role="alert">{{ error }}</div>
      </section>

      <section class="panel">
        <div class="panel-title"><h2>Connection</h2></div>
        <dl class="review-grid">
          <dt>Bridge URL</dt><dd class="mono">{{ status.url || "-" }}</dd>
          <dt>Token</dt>
          <dd>
            <button class="ghost mono" v-if="status.token" @click="copy(status.token, 'Token')" title="Copy token">
              {{ status.token.slice(0, 6) }}… ⧉
            </button>
            <span v-else>-</span>
          </dd>
          <dt>Proxy command</dt>
          <dd><button class="ghost mono" @click="copy(status.proxy_command, 'Proxy command')">{{ status.proxy_command }} ⧉</button></dd>
          <dt>Paper mode</dt><dd>{{ status.paper_mode ? "on" : "off" }}</dd>
        </dl>
        <div class="muted" style="font-size: var(--fs-tiny)">
          Register in an MCP client with the proxy command; it reads .avanza_mcp_session.json automatically.
        </div>
      </section>

      <section class="panel mcp-log-panel">
        <div class="panel-title"><h2>MCP Log</h2><span class="muted">{{ store.mcpLog.length }}</span></div>
        <div ref="logHost" class="log-scroll mono">
          <div v-if="!store.mcpLog.length" class="muted" style="padding: 8px">Tool calls and bridge events appear here.</div>
          <div v-for="(entry, i) in store.mcpLog" :key="i" class="log-line">
            <span class="muted">{{ entry.timestamp }}</span> {{ entry.message }}
          </div>
        </div>
      </section>
    </div>
  `,
});

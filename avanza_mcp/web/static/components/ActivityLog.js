// Dual console pane mirroring the TUI: app activity + live MCP interactions.
import { defineComponent, computed, ref, watch, nextTick, onUnmounted } from "vue";
import { store } from "../store.js";
import { highlightLog } from "../loghl.js";

export default defineComponent({
  name: "ActivityLog",
  setup() {
    const entries = computed(() => store.activityLog || []);
    const mcpEntries = computed(() => store.mcpLog || []);
    const appHost = ref(null);
    const mcpHost = ref(null);
    const rowHost = ref(null);
    const activityWidth = ref(loadWidth());
    const resizing = ref(null);

    function loadWidth() {
      try {
        const value = Number(localStorage.getItem("avanza.web.layout.activityLogWidth"));
        return Number.isFinite(value) && value > 0 ? value : 50;
      } catch {
        return 50;
      }
    }

    function saveWidth() {
      try { localStorage.setItem("avanza.web.layout.activityLogWidth", String(Math.round(activityWidth.value))); } catch {}
    }

    const rowStyle = computed(() => ({ "--activity-log-width": `${activityWidth.value}%` }));

    function startConsoleResize(event) {
      event.preventDefault();
      const rect = rowHost.value?.getBoundingClientRect();
      if (!rect || rect.width <= 0) return;
      resizing.value = { left: rect.left, width: rect.width };
      document.body.classList.add("is-resizing");
      window.addEventListener("pointermove", onConsoleResize);
      window.addEventListener("pointerup", stopConsoleResize, { once: true });
    }

    function onConsoleResize(event) {
      const state = resizing.value;
      if (!state) return;
      const percent = ((event.clientX - state.left) / state.width) * 100;
      activityWidth.value = Math.max(25, Math.min(75, percent));
    }

    function stopConsoleResize() {
      if (resizing.value) saveWidth();
      resizing.value = null;
      document.body.classList.remove("is-resizing");
      window.removeEventListener("pointermove", onConsoleResize);
    }

    function follow(hostRef) {
      return async () => {
        const element = hostRef.value;
        const shouldFollow = !element || element.scrollTop + element.clientHeight >= element.scrollHeight - 24;
        await nextTick();
        if (shouldFollow && hostRef.value) hostRef.value.scrollTop = hostRef.value.scrollHeight;
      };
    }
    watch(() => entries.value.length, follow(appHost));
    watch(() => mcpEntries.value.length, follow(mcpHost));
    onUnmounted(stopConsoleResize);

    return { entries, mcpEntries, appHost, mcpHost, rowHost, rowStyle, startConsoleResize, highlightLog };
  },
  template: `
    <section class="panel log-panel">
      <div ref="rowHost" class="console-row" :style="rowStyle">
        <div class="console-pane">
          <div class="panel-title"><h2>Activity</h2></div>
          <div ref="appHost" class="log-scroll mono" aria-live="polite">
            <div v-if="!entries.length" class="muted" style="padding: 8px">Session activity appears here.</div>
            <div v-for="(entry, i) in entries" :key="i" class="log-line">
              <span class="muted">{{ entry.timestamp }}</span> <span v-html="highlightLog(entry.message)"></span>
            </div>
          </div>
        </div>
        <div class="resize-bar vertical console-splitter" role="separator" aria-label="Resize Activity and MCP Live logs"
             @pointerdown="startConsoleResize"></div>
        <div class="console-pane mcp-pane">
          <div class="panel-title"><h2>MCP Live</h2><span class="muted">{{ mcpEntries.length }}</span></div>
          <div ref="mcpHost" class="log-scroll mono" aria-live="polite">
            <div v-if="!mcpEntries.length" class="muted" style="padding: 8px">MCP tool calls stream here when the bridge is active.</div>
            <div v-for="(entry, i) in mcpEntries" :key="i" class="log-line">
              <span class="muted">{{ entry.timestamp }}</span> <span v-html="highlightLog(entry.message)"></span>
            </div>
          </div>
        </div>
      </div>
    </section>
  `,
});

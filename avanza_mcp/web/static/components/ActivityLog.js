// Dual console pane mirroring the TUI: app activity + live MCP interactions.
import { defineComponent, computed, ref, watch, nextTick } from "vue";
import { store } from "../store.js";
import { highlightLog } from "../loghl.js";

export default defineComponent({
  name: "ActivityLog",
  setup() {
    const entries = computed(() => store.activityLog || []);
    const mcpEntries = computed(() => store.mcpLog || []);
    const appHost = ref(null);
    const mcpHost = ref(null);

    function follow(hostRef) {
      return async () => {
        await nextTick();
        if (hostRef.value) hostRef.value.scrollTop = hostRef.value.scrollHeight;
      };
    }
    watch(() => entries.value.length, follow(appHost));
    watch(() => mcpEntries.value.length, follow(mcpHost));

    return { entries, mcpEntries, appHost, mcpHost, highlightLog };
  },
  template: `
    <section class="panel log-panel">
      <div class="console-row">
        <div class="console-pane">
          <div class="panel-title"><h2>Activity</h2></div>
          <div ref="appHost" class="log-scroll mono" aria-live="polite">
            <div v-if="!entries.length" class="muted" style="padding: 8px">Session activity appears here.</div>
            <div v-for="(entry, i) in entries" :key="i" class="log-line">
              <span class="muted">{{ entry.timestamp }}</span> <span v-html="highlightLog(entry.message)"></span>
            </div>
          </div>
        </div>
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

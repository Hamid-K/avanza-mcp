// Rolling activity/notice log (mirrors the TUI console pane).
import { defineComponent, computed } from "vue";
import { store } from "../store.js";

export default defineComponent({
  name: "ActivityLog",
  setup() {
    const entries = computed(() => store.activityLog || []);
    return { entries };
  },
  template: `
    <section class="panel log-panel">
      <div class="panel-title"><h2>Activity</h2></div>
      <div class="log-scroll mono" aria-live="polite">
        <div v-if="!entries.length" class="muted" style="padding: 8px">Session activity appears here.</div>
        <div v-for="(entry, i) in entries" :key="i" class="log-line">
          <span class="muted">{{ entry.timestamp }}</span> {{ entry.message }}
        </div>
      </div>
    </section>
  `,
});

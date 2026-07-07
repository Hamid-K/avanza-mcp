// App entry: token gate, then the trading shell (Phase 3+).
import { createApp, defineComponent, computed, onMounted } from "vue";
import { api } from "./api.js";
import { store, dismissToast } from "./store.js";
import { connectWs } from "./ws.js";
import LoginView from "./components/LoginView.js";

const Toasts = defineComponent({
  name: "Toasts",
  setup() {
    return { store, dismissToast };
  },
  template: `
    <div class="toasts" aria-live="polite">
      <div v-for="t in store.toasts" :key="t.id" class="toast" :class="t.kind" @click="dismissToast(t.id)">
        {{ t.message }}
      </div>
    </div>
  `,
});

const PlaceholderShell = defineComponent({
  name: "PlaceholderShell",
  setup() {
    return { store };
  },
  template: `
    <div style="display:grid;place-items:center;height:100%;">
      <div class="login-card fade-in" style="text-align:center;">
        <h1>Authenticated</h1>
        <div class="sub">Dashboard loads here (next build phase).</div>
        <div class="muted">v{{ store.meta.app_version }}</div>
      </div>
    </div>
  `,
});

const Root = defineComponent({
  name: "Root",
  components: { LoginView, PlaceholderShell, Toasts },
  setup() {
    const view = computed(() => {
      if (store.auth.checking) return "checking";
      return store.auth.authenticated ? "shell" : "login";
    });

    async function bootstrap() {
      try {
        const me = await api.get("/api/auth/me");
        store.auth.authenticated = !!me.authenticated;
      } catch {
        store.auth.authenticated = false;
      } finally {
        store.auth.checking = false;
      }
      if (store.auth.authenticated) {
        await afterAuth();
      }
    }

    async function afterAuth() {
      store.meta = await api.get("/api/meta");
      connectWs(() => afterAuth());
    }

    onMounted(bootstrap);
    return { view, afterAuth };
  },
  template: `
    <div v-if="store.wsState === 'disconnected' && store.auth.authenticated" class="conn-banner">
      Connection lost — reconnecting...
    </div>
    <template v-if="view === 'login'">
      <LoginView @authenticated="afterAuth" />
    </template>
    <template v-else-if="view === 'shell'">
      <PlaceholderShell />
    </template>
    <Toasts />
  `,
  data() {
    return { store };
  },
});

createApp(Root).mount("#app");

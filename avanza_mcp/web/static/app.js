// App entry: token gate, then the trading shell (Phase 3+).
import { createApp, defineComponent, computed, onMounted } from "vue";
import { api, setCsrfToken } from "./api.js";
import { store, dismissToast } from "./store.js";
import { connectWs } from "./ws.js";
import LoginView from "./components/LoginView.js";
import AppShell from "./components/AppShell.js";
import { hydrateAll } from "./actions.js";
import { applyStoredTheme } from "./theme.js";

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

const Root = defineComponent({
  name: "Root",
  components: { LoginView, AppShell, Toasts },
  setup() {
    const view = computed(() => {
      if (store.auth.checking) return "checking";
      return store.auth.authenticated ? "shell" : "login";
    });

    async function bootstrap() {
      try {
        const me = await api.get("/api/auth/me");
        store.auth.authenticated = !!me.authenticated;
        if (me.csrf_token) setCsrfToken(me.csrf_token);
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
      await hydrateAll();
      connectWs(() => hydrateAll());
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
      <AppShell />
    </template>
    <Toasts />
  `,
  data() {
    return { store };
  },
});

applyStoredTheme();
createApp(Root).mount("#app");

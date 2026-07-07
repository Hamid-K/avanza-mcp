// Web-token gate: the terminal prints a one-time token at startup.
import { defineComponent, ref } from "vue";
import { api, setCsrfToken } from "../api.js";
import { store } from "../store.js";

export default defineComponent({
  name: "LoginView",
  emits: ["authenticated"],
  setup(_, { emit }) {
    const token = ref("");
    const error = ref("");
    const busy = ref(false);

    async function submit() {
      if (!token.value.trim() || busy.value) return;
      busy.value = true;
      error.value = "";
      try {
        const result = await api.post("/api/auth/login", { token: token.value.trim() });
        setCsrfToken(result.csrf_token);
        store.auth.authenticated = true;
        emit("authenticated");
      } catch (exc) {
        error.value = exc.status === 401 ? "Invalid token. Check the terminal output." : `Login failed: ${exc.message}`;
      } finally {
        busy.value = false;
      }
    }

    return { token, error, busy, submit };
  },
  template: `
    <div class="login-gate">
      <div class="login-card fade-in">
        <h1>Avanza-MCP Trading Console</h1>
        <div class="sub">Web access token required</div>
        <form @submit.prevent="submit">
          <div class="field">
            <label for="web-token">Access token</label>
            <input id="web-token" v-model="token" type="password" autocomplete="off"
                   placeholder="Paste the token from the terminal" autofocus>
          </div>
          <div class="error" role="alert" aria-live="polite">{{ error }}</div>
          <button class="primary" type="submit" :disabled="busy || !token.trim()">
            {{ busy ? "Checking..." : "Unlock" }}
          </button>
        </form>
        <div class="login-hint">
          The token was printed when you ran <code>python avanza_cli.py web</code>.
          It is also stored in <code>.avanza_web_session.json</code> next to the script.
        </div>
      </div>
    </div>
  `,
});

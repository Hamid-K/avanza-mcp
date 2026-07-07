// Add an Avanza tenant session (or re-authenticate one) from the web.
import { defineComponent, ref, computed, watch } from "vue";
import { store, toast } from "../store.js";
import { addSession } from "../actions.js";

export default defineComponent({
  name: "SessionLoginModal",
  props: {
    open: { type: Boolean, default: false },
    reauthSessionId: { type: String, default: "" },
  },
  emits: ["close", "done"],
  setup(props, { emit }) {
    const mode = ref("credentials");
    const username = ref(""); const password = ref(""); const totp = ref("");
    const opItem = ref(""); const opVault = ref(""); const label = ref("");
    const busy = ref(false); const error = ref("");

    const reauthTarget = computed(() =>
      store.sessions.find((s) => s.session_id === props.reauthSessionId));

    watch(() => props.open, (isOpen) => {
      if (isOpen) { error.value = ""; password.value = ""; totp.value = ""; }
    });

    async function submit() {
      if (busy.value) return;
      busy.value = true;
      error.value = "";
      try {
        const body = { mode: mode.value, label: label.value };
        if (props.reauthSessionId) body.refresh_session_id = props.reauthSessionId;
        if (mode.value === "credentials") {
          body.username = username.value; body.password = password.value; body.totp = totp.value;
        } else {
          body.op_item = opItem.value; body.op_vault = opVault.value;
        }
        await addSession(body);
        toast(props.reauthSessionId ? "Session re-authenticated" : "Session added", "success");
        password.value = ""; totp.value = "";
        emit("done"); emit("close");
      } catch (exc) {
        error.value = exc.payload?.detail || exc.message;
      } finally {
        busy.value = false;
        store.loginProgress = null;
      }
    }

    return { props, emit, mode, username, password, totp, opItem, opVault, label, busy, error, submit, reauthTarget, store };
  },
  template: `
    <div v-if="props.open" class="modal-backdrop" @click.self="!busy && emit('close')">
      <div class="modal-card fade-in" role="dialog" aria-modal="true">
        <h2>{{ reauthTarget ? "Re-authenticate " + reauthTarget.label : "Add Avanza session" }}</h2>
        <div class="tab-row">
          <button :class="{ active: mode === 'credentials' }" @click="mode = 'credentials'">Credentials</button>
          <button :class="{ active: mode === '1password' }" @click="mode = '1password'">1Password CLI</button>
        </div>
        <form @submit.prevent="submit">
          <template v-if="mode === 'credentials'">
            <div class="field"><label>Username</label><input v-model="username" autocomplete="off"></div>
            <div class="field-row">
              <div class="field"><label>Password</label><input v-model="password" type="password" autocomplete="off"></div>
              <div class="field"><label>TOTP</label><input v-model="totp" inputmode="numeric" maxlength="8" autocomplete="off"></div>
            </div>
          </template>
          <template v-else>
            <div class="field"><label>1Password item</label><input v-model="opItem" autocomplete="off"></div>
            <div class="field"><label>Vault (optional)</label><input v-model="opVault" autocomplete="off"></div>
          </template>
          <div class="field" v-if="!reauthTarget"><label>Session label (optional)</label><input v-model="label" autocomplete="off"></div>
          <div class="progress-line" aria-live="polite">
            <template v-if="busy">
              <span class="spinner"></span>
              {{ store.loginProgress?.message || "Connecting..." }}
            </template>
          </div>
          <div class="error" role="alert">{{ error }}</div>
          <div class="modal-actions">
            <button type="button" class="ghost" :disabled="busy" @click="emit('close')">Cancel</button>
            <button type="submit" class="primary" :disabled="busy">{{ busy ? "Signing in..." : "Sign in" }}</button>
          </div>
        </form>
      </div>
    </div>
  `,
});

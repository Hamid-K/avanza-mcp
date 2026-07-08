// Add an Avanza tenant session (or re-authenticate one) from the web.
import { defineComponent, ref, computed, watch } from "vue";
import { store, toast } from "../store.js";
import { addSession } from "../actions.js";

const PROFILE_STORAGE_KEY = "avanza.web.onePasswordProfiles.v1";

function loadProfiles() {
  try {
    const parsed = JSON.parse(localStorage.getItem(PROFILE_STORAGE_KEY) || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => item && typeof item === "object" && item.op_item)
      .map((item) => ({
        id: String(item.id || `${item.op_item}:${item.op_vault || ""}`),
        label: String(item.label || item.op_item),
        op_item: String(item.op_item || ""),
        op_vault: String(item.op_vault || ""),
      }));
  } catch {
    return [];
  }
}

function saveProfiles(profiles) {
  const cleaned = profiles.map((item) => ({
    id: item.id,
    label: item.label,
    op_item: item.op_item,
    op_vault: item.op_vault,
  }));
  localStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(cleaned));
}

function newProfileId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `profile-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

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
    const savedProfiles = ref(loadProfiles());
    const selectedProfileId = ref("");
    const rememberProfile = ref(true);

    const reauthTarget = computed(() =>
      store.sessions.find((s) => s.session_id === props.reauthSessionId));

    watch(() => props.open, (isOpen) => {
      if (isOpen) { error.value = ""; password.value = ""; totp.value = ""; }
    });

    function persistProfiles() {
      saveProfiles(savedProfiles.value);
    }

    function applySelectedProfile() {
      const profile = savedProfiles.value.find((item) => item.id === selectedProfileId.value);
      if (!profile) return;
      mode.value = "1password";
      opItem.value = profile.op_item;
      opVault.value = profile.op_vault;
      label.value = profile.label;
    }

    function forgetSelectedProfile() {
      if (!selectedProfileId.value) return;
      savedProfiles.value = savedProfiles.value.filter((item) => item.id !== selectedProfileId.value);
      selectedProfileId.value = "";
      persistProfiles();
      toast("Saved 1Password profile forgotten", "info");
    }

    function rememberCurrentProfile(result) {
      if (mode.value !== "1password" || !rememberProfile.value || !opItem.value.trim()) return;
      const session = (result.sessions || []).find((item) => item.session_id === result.session_id);
      const profile = {
        id: selectedProfileId.value || newProfileId(),
        label: label.value.trim() || session?.label || opItem.value.trim(),
        op_item: opItem.value.trim(),
        op_vault: opVault.value.trim(),
      };
      const existingIndex = savedProfiles.value.findIndex(
        (item) => item.op_item === profile.op_item && item.op_vault === profile.op_vault
      );
      if (existingIndex >= 0) {
        profile.id = savedProfiles.value[existingIndex].id;
        savedProfiles.value.splice(existingIndex, 1, profile);
      } else {
        savedProfiles.value.push(profile);
      }
      selectedProfileId.value = profile.id;
      persistProfiles();
    }

    async function submitSelectedProfile() {
      applySelectedProfile();
      await submit();
    }

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
        const result = await addSession(body);
        rememberCurrentProfile(result);
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

    return {
      props, emit, mode, username, password, totp, opItem, opVault, label, busy, error,
      savedProfiles, selectedProfileId, rememberProfile, applySelectedProfile,
      forgetSelectedProfile, submitSelectedProfile, submit, reauthTarget, store,
    };
  },
  template: `
    <div v-if="props.open" class="modal-backdrop" @click.self="!busy && emit('close')">
      <div class="modal-card fade-in" role="dialog" aria-modal="true">
        <h2>{{ reauthTarget ? "Re-authenticate " + reauthTarget.label : "Add Avanza session" }}</h2>
        <div class="tab-row">
          <button :class="{ active: mode === 'credentials' }" @click="mode = 'credentials'">Credentials</button>
          <button :class="{ active: mode === '1password' }" @click="mode = '1password'">1Password CLI</button>
        </div>
        <div v-if="savedProfiles.length" class="profile-picker">
          <div class="field">
            <label>Saved 1Password profile</label>
            <select v-model="selectedProfileId" @change="applySelectedProfile">
              <option value="">Select saved profile…</option>
              <option v-for="profile in savedProfiles" :key="profile.id" :value="profile.id">
                {{ profile.label }}
              </option>
            </select>
          </div>
          <button type="button" class="primary" :disabled="busy || !selectedProfileId" @click="submitSelectedProfile">
            Sign in saved
          </button>
          <button type="button" class="ghost" :disabled="busy || !selectedProfileId" @click="forgetSelectedProfile">
            Forget
          </button>
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
            <label class="check-row">
              <input type="checkbox" v-model="rememberProfile">
              Remember this 1Password item name on this browser
            </label>
            <div class="form-hint">Only the 1Password item name, vault, and display label are stored locally.</div>
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

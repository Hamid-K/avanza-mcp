// Cancel confirmation: paper cancels locally, live requires typed CANCEL.
import { defineComponent, ref, computed, watch } from "vue";
import { api } from "../api.js";
import { toast } from "../store.js";
import { hydrateOrders, hydrateStoplosses } from "../actions.js";
import ConfirmTyped from "./ConfirmTyped.js";

export default defineComponent({
  name: "CancelDialog",
  components: { ConfirmTyped },
  props: {
    target: { type: Object, default: null }, // { kind: order|stoploss|paper, row }
  },
  emits: ["close"],
  setup(props, { emit }) {
    const confirmText = ref("");
    const busy = ref(false);
    const error = ref("");
    const isPaper = computed(() => props.target?.kind === "paper");

    watch(() => props.target, () => { confirmText.value = ""; error.value = ""; });

    async function confirm() {
      if (busy.value || !props.target) return;
      busy.value = true; error.value = "";
      const { kind, row } = props.target;
      try {
        await api.post("/api/orders/cancel", {
          kind,
          id: row.id || row["Order ID"] || row["Stop Loss ID"],
          account_id: row.account_id || row["Account ID"] || "",
          stock: row.stock || row.Stock || "",
          item_kind: row.kind || "",
          confirm_text: confirmText.value,
        });
        toast(isPaper.value ? "Paper order cancelled" : "Live cancellation sent", "success");
        await Promise.all([hydrateOrders(), hydrateStoplosses()]);
        emit("close");
      } catch (exc) {
        error.value = exc.payload?.detail || exc.message;
      } finally { busy.value = false; }
    }

    return { props, emit, confirmText, busy, error, isPaper, confirm };
  },
  template: `
    <div v-if="props.target" class="modal-backdrop" @click.self="!busy && emit('close')">
      <div class="modal-card fade-in" role="dialog" aria-modal="true">
        <h2>Cancel {{ isPaper ? "paper" : "live" }} {{ props.target.kind === "stoploss" ? "stop-loss" : "order" }}</h2>
        <dl class="review-grid">
          <dt>Stock</dt><dd>{{ props.target.row.stock || props.target.row.Stock || "-" }}</dd>
          <dt>ID</dt><dd class="mono">{{ props.target.row.id || props.target.row["Order ID"] || props.target.row["Stop Loss ID"] }}</dd>
          <dt>Mode</dt><dd>{{ isPaper ? "Paper (local, immediate)" : "Live (sent to Avanza)" }}</dd>
        </dl>
        <ConfirmTyped v-if="!isPaper" word="CANCEL" @armed="confirmText = $event" />
        <div class="error" role="alert">{{ error }}</div>
        <div class="modal-actions">
          <button class="ghost" :disabled="busy" @click="emit('close')">Keep it</button>
          <button :class="isPaper ? 'warn' : 'danger'"
                  :disabled="busy || (!isPaper && confirmText !== 'CANCEL')" @click="confirm">
            {{ busy ? "Cancelling..." : "Confirm cancellation" }}
          </button>
        </div>
      </div>
    </div>
  `,
});

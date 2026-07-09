// Stop-loss ticket drawer: holding select, trigger config, dry-run -> place; edit = replace.
import { defineComponent, ref, computed, watch } from "vue";
import { api } from "../api.js";
import { store, toast } from "../store.js";
import { hydrateStoplosses } from "../actions.js";
import ConfirmTyped from "./ConfirmTyped.js";

function isoPlusDays(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export default defineComponent({
  name: "StopLossTicket",
  components: { ConfirmTyped },
  props: {
    open: { type: Boolean, default: false },
    editTarget: { type: Object, default: null }, // active-trades row for edit mode
  },
  emits: ["close"],
  setup(props, { emit }) {
    const orderBookId = ref("");
    const volume = ref("");
    const triggerType = ref("follow_upwards");
    const triggerValue = ref("");
    const triggerValueType = ref("percentage");
    const validUntil = ref(isoPlusDays(30));
    const orderType = ref("sell");
    const orderPrice = ref("");
    const orderPriceType = ref("percentage");
    const orderValidDays = ref("1");
    const mmQuote = ref(false);
    const shortSelling = ref(false);
    const review = ref(null);
    const confirmText = ref("");
    const busy = ref(false);
    const error = ref("");

    const holdings = computed(() =>
      (store.portfolio?.rows || []).map((r) => ({ id: r["Order Book ID"], label: r.Stock, volume: r.volume })));
    const isEdit = computed(() => !!props.editTarget?.id);

    watch(orderBookId, (id) => {
      const holding = holdings.value.find((h) => h.id === id);
      if (holding && holding.volume) volume.value = String(holding.volume);
    });

    watch(() => props.editTarget, (target) => {
      review.value = null; error.value = "";
      if (target && target.raw) {
        orderBookId.value = String(target.raw.orderbook_id || target.raw["Order Book ID"] || "");
        if (target.volume) volume.value = String(target.volume);
      }
    });

    async function dryRun() {
      busy.value = true; error.value = ""; review.value = null;
      try {
        review.value = await api.post("/api/stoplosses/dry-run", {
          order_book_id: orderBookId.value,
          volume: parseFloat(volume.value),
          trigger_type: triggerType.value,
          trigger_value: parseFloat(triggerValue.value),
          trigger_value_type: triggerValueType.value,
          valid_until: validUntil.value,
          order_type: orderType.value,
          order_price: parseFloat(orderPrice.value),
          order_price_type: orderPriceType.value,
          order_valid_days: parseInt(orderValidDays.value, 10),
          trigger_on_market_maker_quote: mmQuote.value,
          short_selling_allowed: shortSelling.value,
          replace_stoploss_id: isEdit.value ? props.editTarget.id : undefined,
        });
      } catch (exc) {
        error.value = exc.payload?.detail || exc.message;
      } finally { busy.value = false; }
    }

    async function place() {
      if (!review.value || busy.value) return;
      busy.value = true; error.value = "";
      try {
        const result = await api.post("/api/stoplosses/place", {
          review_id: review.value.review_id, confirm_text: confirmText.value,
        });
        toast(result.mode === "paper" ? "Paper stop-loss created" : (isEdit.value ? "Live stop-loss replaced" : "Live stop-loss sent"), "success");
        review.value = null; confirmText.value = "";
        await hydrateStoplosses();
        emit("close");
      } catch (exc) {
        error.value = exc.payload?.detail || exc.message;
        if (exc.status === 409) review.value = null;
      } finally { busy.value = false; }
    }

    return {
      props, emit, store, holdings, isEdit, orderBookId, volume, triggerType, triggerValue,
      triggerValueType, validUntil, orderType, orderPrice, orderPriceType, orderValidDays,
      mmQuote, shortSelling, review, confirmText, busy, error, dryRun, place,
    };
  },
  template: `
    <aside class="drawer warn-border" :class="{ open: props.open }" role="dialog" aria-label="Stop-loss ticket">
      <div class="drawer-head">
        <h2>{{ isEdit ? "Edit Stop-Loss" : "New Stop-Loss" }}</h2>
        <span class="badge" :class="store.meta.paper_mode ? 'paper' : 'live'">{{ store.meta.paper_mode ? "PAPER" : "LIVE" }}</span>
        <button class="ghost" @click="emit('close')" aria-label="Close">✕</button>
      </div>
      <div class="drawer-body">
        <div class="field">
          <label>Portfolio holding</label>
          <select v-model="orderBookId">
            <option value="" disabled>Select holding…</option>
            <option v-for="h in holdings" :key="h.id" :value="h.id">{{ h.label }}</option>
          </select>
        </div>
        <div class="field-row">
          <div class="field"><label>Volume</label><input v-model="volume" inputmode="decimal"></div>
          <div class="field">
            <label>Trigger type</label>
            <select v-model="triggerType">
              <option value="follow_upwards">Follow upwards (trailing)</option>
              <option value="follow_downwards">Follow downwards</option>
              <option value="less_or_equal">Less or equal</option>
              <option value="more_or_equal">More or equal</option>
            </select>
          </div>
        </div>
        <div class="field-row">
          <div class="field"><label>Trigger value</label><input v-model="triggerValue" inputmode="decimal"></div>
          <div class="field">
            <label>Trigger unit</label>
            <select v-model="triggerValueType"><option value="percentage">%</option><option value="monetary">SEK</option></select>
          </div>
        </div>
        <div class="field-row">
          <div class="field">
            <label>Order side</label>
            <select v-model="orderType"><option value="sell">Sell</option><option value="buy">Buy</option></select>
          </div>
          <div class="field"><label>Order price</label><input v-model="orderPrice" inputmode="decimal"></div>
        </div>
        <div class="field-row">
          <div class="field">
            <label>Price unit</label>
            <select v-model="orderPriceType"><option value="percentage">%</option><option value="monetary">SEK</option></select>
          </div>
          <div class="field"><label>Order valid days</label><input v-model="orderValidDays" inputmode="numeric"></div>
        </div>
        <div class="field"><label>Stop-loss valid until</label><input v-model="validUntil" type="date"></div>
        <label class="check-row"><input type="checkbox" v-model="mmQuote"> Trigger on market-maker quote</label>
        <label class="check-row"><input type="checkbox" v-model="shortSelling"> Short selling allowed</label>

        <div class="ticket-actions">
          <button @click="dryRun" :disabled="busy || !orderBookId">Review (dry-run)</button>
        </div>

        <div v-if="review" class="review-card fade-in">
          <h3>Review{{ isEdit ? " — replaces existing stop-loss" : "" }}</h3>
          <div v-for="w in review.warnings" :key="w" class="warn-text" style="font-size: var(--fs-small)">⚠ {{ w }}</div>
          <dl class="review-grid num">
            <dt>Trigger</dt><dd>{{ review.preview.stop_loss_trigger.type }} {{ review.preview.stop_loss_trigger.value }} ({{ review.preview.stop_loss_trigger.value_type }})</dd>
            <dt>Order</dt><dd>{{ review.preview.stop_loss_order_event.type }} {{ review.preview.stop_loss_order_event.volume }} @ {{ review.preview.stop_loss_order_event.price }} ({{ review.preview.stop_loss_order_event.price_type }})</dd>
            <dt>Valid days</dt><dd>{{ review.preview.stop_loss_order_event.valid_days }}</dd>
            <dt>Valid until</dt><dd>{{ review.preview.stop_loss_trigger.valid_until }}</dd>
          </dl>
          <ConfirmTyped v-if="review.confirm_required" :word="review.confirm_required" @armed="confirmText = $event" />
          <button :class="review.confirm_required ? 'danger' : 'warn'" style="width: 100%"
                  :disabled="busy || (review.confirm_required && confirmText !== review.confirm_required)"
                  @click="place">
            {{ busy ? "Submitting..." : (review.confirm_required ? "Submit Live Stop-Loss" : "Create Paper Stop-Loss") }}
          </button>
        </div>
        <div class="error" role="alert">{{ error }}</div>
      </div>
    </aside>
  `,
});

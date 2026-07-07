// Buy/sell order ticket drawer with search, live value calc, dry-run -> place.
import { defineComponent, ref, computed, watch, onUnmounted } from "vue";
import { api } from "../api.js";
import { store, toast } from "../store.js";
import { hydrateOrders, hydratePortfolio } from "../actions.js";
import ConfirmTyped from "./ConfirmTyped.js";

function isoPlusDays(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export default defineComponent({
  name: "OrderTicket",
  components: { ConfirmTyped },
  props: {
    open: { type: Boolean, default: false },
    prefill: { type: Object, default: null }, // { side, order_book_id, name, volume }
  },
  emits: ["close"],
  setup(props, { emit }) {
    const search = ref("");
    const results = ref([]);
    const orderBookId = ref("");
    const side = ref("buy");
    const volume = ref("");
    const price = ref("");
    const condition = ref("normal");
    const validUntil = ref(isoPlusDays(0));
    const review = ref(null);
    const confirmText = ref("");
    const busy = ref(false);
    const error = ref("");
    const quote = ref(null);
    let searchTimer = 0;

    const orderValue = computed(() => {
      const v = parseFloat(volume.value); const p = parseFloat(price.value);
      if (!Number.isFinite(v) || !Number.isFinite(p)) return "";
      return `${(v * p).toFixed(2)} SEK`;
    });

    watch(() => props.prefill, (pf) => {
      if (!pf) return;
      review.value = null; error.value = "";
      side.value = pf.side || "buy";
      orderBookId.value = pf.order_book_id || "";
      search.value = pf.name || "";
      if (pf.side === "sell" && pf.volume) volume.value = String(pf.volume);
      if (orderBookId.value) loadQuote();
    });

    watch(search, () => {
      clearTimeout(searchTimer);
      if (search.value.trim().length < 2) { results.value = []; return; }
      searchTimer = setTimeout(runSearch, 350);
    });
    onUnmounted(() => clearTimeout(searchTimer));

    async function runSearch() {
      try {
        const payload = await api.get(`/api/search?q=${encodeURIComponent(search.value.trim())}`);
        results.value = payload.results || [];
      } catch { results.value = []; }
    }

    function pick(result) {
      orderBookId.value = result.order_book_id;
      search.value = result.name || result.label;
      results.value = [];
      loadQuote();
    }

    async function loadQuote() {
      quote.value = null;
      if (!orderBookId.value) return;
      try {
        const payload = await api.get(`/api/quote/${encodeURIComponent(orderBookId.value)}`);
        quote.value = payload.quote;
        const last = payload.quote?.last ?? payload.quote?.lastPrice;
        if (last && !price.value) price.value = String(last);
      } catch { /* quote optional */ }
    }

    async function dryRun() {
      busy.value = true; error.value = ""; review.value = null;
      try {
        review.value = await api.post("/api/orders/dry-run", {
          order_book_id: orderBookId.value, order_type: side.value,
          price: parseFloat(price.value), volume: parseInt(volume.value, 10),
          condition: condition.value, valid_until: validUntil.value,
        });
      } catch (exc) {
        error.value = exc.payload?.detail || exc.message;
      } finally { busy.value = false; }
    }

    async function place() {
      if (!review.value || busy.value) return;
      busy.value = true; error.value = "";
      try {
        const result = await api.post("/api/orders/place", {
          review_id: review.value.review_id, confirm_text: confirmText.value,
        });
        toast(result.mode === "paper" ? "Paper order created" : "Live order sent", "success");
        review.value = null; confirmText.value = "";
        await Promise.all([hydrateOrders(), hydratePortfolio()]);
        emit("close");
      } catch (exc) {
        error.value = exc.payload?.detail || exc.message;
        if (exc.status === 409) review.value = null; // nonce expired: force new dry-run
      } finally { busy.value = false; }
    }

    return {
      props, emit, store, search, results, orderBookId, side, volume, price, condition,
      validUntil, review, confirmText, busy, error, quote, orderValue, pick, dryRun, place,
    };
  },
  template: `
    <aside class="drawer" :class="{ open: props.open }" role="dialog" aria-label="Order ticket">
      <div class="drawer-head">
        <h2>Order Ticket</h2>
        <span class="badge" :class="store.meta.paper_mode ? 'paper' : 'live'">{{ store.meta.paper_mode ? "PAPER" : "LIVE" }}</span>
        <button class="ghost" @click="emit('close')" aria-label="Close">✕</button>
      </div>
      <div class="drawer-body">
        <div class="field search-field">
          <label>Search stock</label>
          <input v-model="search" placeholder="Name or ticker (min 2 chars)" autocomplete="off">
          <ul v-if="results.length" class="search-results">
            <li v-for="r in results" :key="r.order_book_id">
              <button type="button" @click="pick(r)">{{ r.label }}</button>
            </li>
          </ul>
        </div>
        <div class="field-row">
          <div class="field">
            <label>Side</label>
            <select v-model="side"><option value="buy">Buy</option><option value="sell">Sell</option></select>
          </div>
          <div class="field"><label>Volume</label><input v-model="volume" inputmode="numeric"></div>
        </div>
        <div class="field-row">
          <div class="field"><label>Limit price (SEK)</label><input v-model="price" inputmode="decimal"></div>
          <div class="field">
            <label>Condition</label>
            <select v-model="condition">
              <option value="normal">Normal</option>
              <option value="fill_or_kill">Fill or Kill</option>
              <option value="fill_and_kill">Fill and Kill</option>
            </select>
          </div>
        </div>
        <div class="field-row">
          <div class="field"><label>Valid until</label><input v-model="validUntil" type="date"></div>
          <div class="field"><label>Order value</label><div class="static-value num">{{ orderValue || "-" }}</div></div>
        </div>

        <div class="ticket-actions">
          <button @click="dryRun" :disabled="busy || !orderBookId">Review (dry-run)</button>
        </div>

        <div v-if="review" class="review-card fade-in">
          <h3>Review</h3>
          <dl class="review-grid num">
            <dt>Stock</dt><dd>{{ search }}</dd>
            <dt>Side</dt><dd :class="review.preview.order_type === 'BUY' ? 'up' : 'down'">{{ review.preview.order_type }}</dd>
            <dt>Volume</dt><dd>{{ review.preview.volume }}</dd>
            <dt>Price</dt><dd>{{ review.preview.price }} SEK</dd>
            <dt>Valid until</dt><dd>{{ review.preview.valid_until }}</dd>
            <dt>Condition</dt><dd>{{ review.preview.condition }}</dd>
          </dl>
          <ConfirmTyped v-if="review.confirm_required" :word="review.confirm_required" @armed="confirmText = $event" />
          <button :class="review.confirm_required ? 'danger' : 'warn'" style="width: 100%"
                  :disabled="busy || (review.confirm_required && confirmText !== review.confirm_required)"
                  @click="place">
            {{ busy ? "Submitting..." : (review.confirm_required ? "Submit Live Order" : "Create Paper Order") }}
          </button>
        </div>
        <div class="error" role="alert">{{ error }}</div>
      </div>
    </aside>
  `,
});

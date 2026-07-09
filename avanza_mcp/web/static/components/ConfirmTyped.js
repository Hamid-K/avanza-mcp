// Typed-confirmation input: the user must type the exact word to arm the action.
import { defineComponent, ref, computed, watch } from "vue";

export default defineComponent({
  name: "ConfirmTyped",
  props: {
    word: { type: String, required: true },
    label: { type: String, default: "" },
  },
  emits: ["armed"],
  setup(props, { emit }) {
    const typed = ref("");
    const armed = computed(() => typed.value === props.word);
    watch(armed, (value) => emit("armed", value ? typed.value : ""));
    function reset() { typed.value = ""; }
    return { props, typed, armed, reset };
  },
  template: `
    <div class="field confirm-typed" :class="{ armed }">
      <label>{{ props.label || 'Type "' + props.word + '" to confirm' }}</label>
      <input v-model="typed" autocomplete="off" spellcheck="false"
             :placeholder="props.word" :aria-label="'Type ' + props.word + ' to confirm'">
    </div>
  `,
});

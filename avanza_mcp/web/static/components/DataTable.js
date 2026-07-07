// Generic dense trading table: sortable columns, sticky header, empty state.
// Column spec: { key, label, numeric?, format?, cellClass?, component? }
import { defineComponent, ref, computed } from "vue";

function defaultFormat(value) {
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

export default defineComponent({
  name: "DataTable",
  props: {
    columns: { type: Array, required: true },
    rows: { type: Array, required: true },
    rowKey: { type: String, default: "" },
    emptyText: { type: String, default: "No data" },
  },
  setup(props) {
    const sortKey = ref("");
    const sortAsc = ref(true);

    function sortBy(key) {
      if (sortKey.value === key) {
        sortAsc.value = !sortAsc.value;
      } else {
        sortKey.value = key;
        sortAsc.value = true;
      }
    }

    const sortedRows = computed(() => {
      if (!sortKey.value) return props.rows;
      const key = sortKey.value;
      const direction = sortAsc.value ? 1 : -1;
      return [...props.rows].sort((a, b) => {
        const av = a[key];
        const bv = b[key];
        const an = typeof av === "number" ? av : parseFloat(String(av).replace(/[^\d.-]/g, ""));
        const bn = typeof bv === "number" ? bv : parseFloat(String(bv).replace(/[^\d.-]/g, ""));
        if (!Number.isNaN(an) && !Number.isNaN(bn)) return (an - bn) * direction;
        return String(av ?? "").localeCompare(String(bv ?? "")) * direction;
      });
    });

    return { sortKey, sortAsc, sortBy, sortedRows, defaultFormat };
  },
  template: `
    <div class="data-table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="col in columns" :key="col.key" :class="{ num: col.numeric }" @click="sortBy(col.key)">
              {{ col.label }}<span v-if="sortKey === col.key" class="sort-arrow">{{ sortAsc ? "▲" : "▼" }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!sortedRows.length">
            <td class="empty" :colspan="columns.length">{{ emptyText }}</td>
          </tr>
          <tr v-for="(row, i) in sortedRows" :key="rowKey ? row[rowKey] : i">
            <td v-for="col in columns" :key="col.key"
                :class="[{ num: col.numeric }, col.cellClass ? col.cellClass(row) : '']">
              <slot :name="'cell-' + col.key" :row="row">
                {{ col.format ? col.format(row[col.key], row) : defaultFormat(row[col.key]) }}
              </slot>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  `,
});

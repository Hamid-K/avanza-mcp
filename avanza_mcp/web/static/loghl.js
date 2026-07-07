// Log-line syntax highlighting: escape, then single-pass tokenize so rules
// never match inside an earlier rule's output.
const RULES = [
  { cls: "lg-ok", re: /✓|\bsuccess(?:ful)?\b|\bauthorized\b|\benabled\b/iy },
  { cls: "lg-err", re: /✗|\bfail(?:ed|ure)?\b|\berror\b|\bdenied\b|\brejected\b|\bexpired\b|\brevoked\b|\bunauthorized\b/iy },
  { cls: "lg-buy", re: /BUY|KÖP/y },
  { cls: "lg-sell", re: /SELL|SÄLJ/y },
  { cls: "lg-tool", re: /(?:avanza|tv|zacks|fmp|polygon|sec|fred|data_source|signal_context|paper)_[a-z0-9_]+/y },
  { cls: "lg-url", re: /https?:\/\/\S+/y },
  { cls: "lg-num", re: /-?\d[\d\u00a0]*(?:[.,]\d+)?(?:\s?(?:%|SEK|USD|kr))?/y },
];

function escapeHtml(value) {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function highlightLog(message) {
  const text = String(message ?? "");
  let out = "";
  let i = 0;
  while (i < text.length) {
    let matched = false;
    // word-ish boundary: only start a token at a non-word boundary
    const prev = i === 0 ? "" : text[i - 1];
    const boundary = i === 0 || !/[\w.]/.test(prev);
    if (boundary) {
      for (const { cls, re } of RULES) {
        re.lastIndex = i;
        const m = re.exec(text);
        if (m && m.index === i && m[0]) {
          out += `<span class="${cls}">${escapeHtml(m[0])}</span>`;
          i += m[0].length;
          matched = true;
          break;
        }
      }
    }
    if (!matched) {
      out += escapeHtml(text[i]);
      i += 1;
    }
  }
  return out;
}

// Dark/light theme: persisted, applied before first paint, broadcast on change.
const STORAGE_KEY = "avanza-theme";

export function currentTheme() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function applyStoredTheme() {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light") document.documentElement.dataset.theme = "light";
}

export function toggleTheme() {
  const next = currentTheme() === "light" ? "dark" : "light";
  if (next === "light") {
    document.documentElement.dataset.theme = "light";
  } else {
    delete document.documentElement.dataset.theme;
  }
  localStorage.setItem(STORAGE_KEY, next);
  window.dispatchEvent(new Event("themechange"));
  return next;
}

// Fetch wrapper: JSON in/out, CSRF header on mutations, structured errors.
let csrfToken = "";

export function setCsrfToken(value) {
  csrfToken = value || "";
}

export class ApiError extends Error {
  constructor(status, payload) {
    super((payload && (payload.detail || payload.error)) || `HTTP ${status}`);
    this.status = status;
    this.payload = payload || {};
  }
}

async function request(method, path, body) {
  const options = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  if (method !== "GET") {
    options.headers["X-Avanza-Web-Token"] = csrfToken;
  }
  const response = await fetch(path, options);
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new ApiError(response.status, payload);
  }
  return payload;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body),
  del: (path, body) => request("DELETE", path, body),
};

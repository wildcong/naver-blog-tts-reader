// Only the signed access credential enters this bridge. Never add a password,
// server secret, blog content, or audio to its data or browser storage.
const STORAGE_KEY = "blog_daily_access_v1";
const MAX_TOKEN_LENGTH = 512;

export default function ({ data, setStateValue }) {
  if (!data || typeof data.id !== "string" || data.id.length > 64 ||
      !["read", "set", "clear"].includes(data.action)) return;

  // Rebinding is safe across Streamlit rerenders and removes old listeners.
  const state = window.__blogDailyAccessBridge || { commandId: "", cleanup: null };
  window.__blogDailyAccessBridge = state;
  state.cleanup?.();
  let timer;

  function read() {
    try {
      const token = window.localStorage.getItem(STORAGE_KEY) || "";
      if (token.length > MAX_TOKEN_LENGTH) {
        window.localStorage.removeItem(STORAGE_KEY);
        return { available: true, token: "" };
      }
      return { available: true, token };
    } catch (_) {
      return { available: false, token: "" };
    }
  }

  function publishEvent() {
    const current = read();
    if (state.lastToken === current.token && state.lastAvailable === current.available) {
      return armExpiry(current.token);
    }
    state.lastToken = current.token;
    state.lastAvailable = current.available;
    setStateValue("reply", {
      id: data.id, action: "event", eventId: crypto.randomUUID(), ...current,
    });
    armExpiry(current.token);
  }

  function armExpiry(token) {
    clearTimeout(timer);
    const expires = Number(token.split(".")[2]);
    if (!token || !Number.isFinite(expires) || expires <= 0) return;
    const remaining = expires * 1000 - Date.now();
    timer = setTimeout(() => {
      const current = read();
      if (current.token !== token) return publishEvent();
      if (expires * 1000 > Date.now()) return armExpiry(token);
      try { window.localStorage.removeItem(STORAGE_KEY); } catch (_) {}
      publishEvent();
    }, Math.min(Math.max(remaining, 1), 2147483647));
  }

  if (state.commandId !== data.id) {
    state.commandId = data.id;
    let available = true;
    try {
      if (data.action === "set") {
        if (typeof data.token !== "string" || !data.token || data.token.length > MAX_TOKEN_LENGTH) {
          throw new Error("Invalid access credential");
        }
        window.localStorage.setItem(STORAGE_KEY, data.token);
        available = window.localStorage.getItem(STORAGE_KEY) === data.token;
      } else if (data.action === "clear") {
        window.localStorage.removeItem(STORAGE_KEY);
      }
    } catch (_) {
      available = false;
    }
    const current = read();
    state.lastToken = current.token;
    state.lastAvailable = available && current.available;
    setStateValue("reply", {
      id: data.id, action: data.action,
      available: available && current.available,
      token: data.action === "read" ? current.token : "",
    });
  }

  const onStorage = (event) => {
    if (event.key === STORAGE_KEY || event.key === null) publishEvent();
  };
  const onVisible = () => { if (document.visibilityState === "visible") publishEvent(); };
  window.addEventListener("storage", onStorage);
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("focus", publishEvent);
  armExpiry(read().token);
  state.cleanup = () => {
    clearTimeout(timer);
    window.removeEventListener("storage", onStorage);
    document.removeEventListener("visibilitychange", onVisible);
    window.removeEventListener("focus", publishEvent);
  };
}

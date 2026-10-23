import type { Config, Session, Snapshot } from "./types";

/* One device is one person. The session lives in localStorage so a phone stays signed in.
   In rehearsal mode (presenter toggle) it lives in sessionStorage instead, so each tab on one laptop can be a different person. */
let rehearsal = false;
export const setRehearsal = (v: boolean) => { rehearsal = v; };
export const isRehearsal = () => rehearsal;
const store = () => (rehearsal ? sessionStorage : localStorage);
const rand = () => Array.from({ length: 16 }, () => Math.floor(Math.random() * 16).toString(16)).join("");

export const deviceId = () => {
  try {
    let id = store().getItem("trustseal.device");
    if (!id) { id = rand(); store().setItem("trustseal.device", id); }
    return id;
  } catch { return "volatile-" + rand(); }
};
export const loadSession = (): Session | null => {
  try { return JSON.parse(store().getItem("trustseal.session") || "null"); } catch { return null; }
};
export const saveSession = (s: Session | null) => {
  try { s ? store().setItem("trustseal.session", JSON.stringify(s)) : store().removeItem("trustseal.session"); } catch { /* private mode */ }
};

export class ApiError extends Error { constructor(public status: number, msg: string) { super(msg); } }

export async function api<T = Snapshot>(path: string, opts: { method?: string; body?: unknown; form?: FormData } = {}): Promise<T> {
  const token = loadSession()?.token ?? "";
  const res = await fetch("/api" + path, {
    method: opts.method ?? (opts.body || opts.form ? "POST" : "GET"),
    headers: { "x-token": token, ...(opts.body ? { "content-type": "application/json" } : {}) },
    body: opts.form ?? (opts.body ? JSON.stringify(opts.body) : undefined),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && token) { saveSession(null); location.href = "/"; }
  if (!res.ok) {
    const d = data.detail;
    throw new ApiError(res.status, typeof d === "string" ? d : "Something went wrong");
  }
  return data as T;
}

export const loadConfig = () => api<Config>("/config");

export async function signOut() {
  try { await api("/auth/leave", { method: "POST" }); } catch { /* already gone */ }
  saveSession(null);
}

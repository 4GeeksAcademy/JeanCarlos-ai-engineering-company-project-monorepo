"use client";

import { authenticatedFetch, getToken } from "@healthcore/auth/client";
import catalog from "./telemetry-contract.json";

type Value = string | number | boolean | null | Value[] | { [key: string]: Value };
type Properties = Record<string, Value>;
type Descriptor = {
  type: string | string[];
  required?: boolean;
  enum?: unknown[];
  const?: unknown;
  minimum?: number;
  maximum?: number;
  pattern?: string;
  items?: Descriptor;
  properties?: Record<string, Descriptor>;
};
type Event = {
  eventId: string;
  timestamp: string;
  sessionId: string;
  userId: string | null;
  event_type: string;
  schemaVersion: string;
  requestId: string;
  properties: Properties;
};

const SCHEMA_VERSION = catalog.schemaVersion;
const MAX_QUEUE = 200;
const BATCH_SIZE = 20;
const MAX_EVENT_BYTES = 2500;
const routeNames = [
  "/login", "/suppliers", "/inventory/products", "/inventory/orders",
  "/inventory/orders/inbound", "/inventory/orders/outbound",
  "/account/change-password", "/forgot-password", "/reset-password",
];
let queue: Event[] = [];
let running = false;
let sending = false;
let sessionId: string | null = null;
let userId: string | null = null;
let role: "admin" | "manager" | "user" | null = null;
let correlationId: string | null = null;
let sessionLookup: Promise<void> | null = null;
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const recent = new Map<string, number>();
const pending = new Map<string, { event: Event; timer: ReturnType<typeof setTimeout> }>();
const latency = new Map<string, { route: string; method: string; status: string; values: number[] }>();

function valid(value: Value, descriptor: Descriptor): boolean {
  const types = Array.isArray(descriptor.type) ? descriptor.type : [descriptor.type];
  const type = value === null ? "null" : Array.isArray(value) ? "array" : typeof value;
  if (!types.includes(type) && !(types.includes("integer") && typeof value === "number" && Number.isInteger(value))) return false;
  if (descriptor.enum && !descriptor.enum.includes(value)) return false;
  if ("const" in descriptor && descriptor.const !== value) return false;
  if (typeof value === "number" && (!Number.isFinite(value) ||
    (descriptor.minimum !== undefined && value < descriptor.minimum) ||
    (descriptor.maximum !== undefined && value > descriptor.maximum))) return false;
  if (typeof value === "string" && descriptor.pattern && !new RegExp(descriptor.pattern).test(value)) return false;
  if (Array.isArray(value) && descriptor.items && !value.every((item) => valid(item, descriptor.items!))) return false;
  if (type === "object" && descriptor.properties && value && !Array.isArray(value) && typeof value === "object") {
    return Object.keys(value).every((key) => key in descriptor.properties!) &&
      Object.entries(descriptor.properties).every(([key, field]) =>
        key in value ? valid(value[key], field) : !field.required);
  }
  return true;
}

function ensureSessionId() {
  if (sessionId) return sessionId;
  try {
    const stored = sessionStorage.getItem("healthcore_telemetry_session");
    sessionId = stored && uuidPattern.test(stored) ? stored : crypto.randomUUID();
    sessionStorage.setItem("healthcore_telemetry_session", sessionId);
  } catch {
    sessionId = crypto.randomUUID();
    console.warn("Telemetry session storage unavailable; using in-memory session.");
  }
  return sessionId;
}

async function refreshSession() {
  if (sessionLookup) return await sessionLookup;
  sessionLookup = (async () => {
    try {
      // The session lives in the browser (Bearer token); without one, events stay anonymous.
      if (!getToken()) {
        userId = null;
        role = null;
        return;
      }
      const response = await authenticatedFetch("/api/telemetry/session", { signal: AbortSignal.timeout(5000) });
      if (response.status === 401) {
        userId = null;
        role = null;
        return;
      }
      if (!response.ok) throw new Error("Session lookup failed");
      const data: unknown = await response.json();
      if (typeof data !== "object" || data === null || !("userId" in data) ||
        typeof data.userId !== "string" || !/^[a-f0-9]{64}$/.test(data.userId)) {
        throw new Error("Invalid telemetry identity");
      }
      userId = data.userId;
      role = "role" in data && (data.role === "admin" || data.role === "manager" || data.role === "user")
        ? data.role : null;
    } catch {
      userId = null;
      role = null;
      console.warn("Telemetry identity unavailable; events remain anonymous.");
    }
  })();
  try {
    await sessionLookup;
  } finally {
    sessionLookup = null;
  }
}

function endpoint(): string | null {
  const configured = process.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT;
  if (!configured) return null;
  try {
    const url = new URL(configured, window.location.origin);
    if (url.origin !== window.location.origin || !["http:", "https:"].includes(url.protocol)) return null;
    return url.href;
  } catch {
    return null;
  }
}

function remove(events: Event[]) {
  const ids = new Set(events.map((event) => event.eventId));
  queue = queue.filter((event) => !ids.has(event.eventId));
}

async function flush() {
  if (sending || queue.length === 0) return;
  const url = endpoint();
  if (!url) return;
  sending = true;
  const events = queue.slice(0, BATCH_SIZE);
  try {
    for (let attempt = 0; attempt <= 3; attempt += 1) {
      try {
        const response = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ events }),
          signal: AbortSignal.timeout(10000),
        });
        if (response.ok) {
          const receipt: unknown = await response.json();
          if (typeof receipt !== "object" || receipt === null || !("received" in receipt) ||
            receipt.received !== events.length) throw new Error("Invalid telemetry receipt");
          remove(events);
          return;
        }
        if (response.status >= 400 && response.status < 500 && response.status !== 429) {
          console.warn("Telemetry batch rejected and discarded:", response.status);
          remove(events);
          return;
        }
      } catch {
        // Telemetry is best effort; failures must not interrupt business operations.
      }
      if (attempt < 3) await new Promise((resolve) => setTimeout(resolve, 1000 * 2 ** attempt));
    }
    console.warn("Telemetry batch discarded after three retries.");
    remove(events);
  } finally {
    sending = false;
    if (queue.length >= BATCH_SIZE) void flush();
  }
}

function beaconFlush() {
  if (document.visibilityState !== "hidden") return;
  for (const [key, item] of pending) {
    clearTimeout(item.timer);
    pending.delete(key);
    enqueue(item.event);
  }
  if (queue.length === 0) return;
  const url = endpoint();
  if (!url) return;
  while (queue.length) {
    const events = queue.slice(0, BATCH_SIZE);
    if (!navigator.sendBeacon(url, new Blob([JSON.stringify({ events })], { type: "application/json" }))) {
      console.warn("Telemetry beacon rejected; keeping batch for the next flush.");
      return;
    }
    remove(events);
  }
}

function initialize() {
  if (running) return;
  running = true;
  ensureSessionId();
  void refreshSession();
  setInterval(() => {
    for (const sample of latency.values()) {
      const sorted = sample.values.toSorted((a, b) => a - b);
      track("api_latency_aggregated", {
        route_template: sample.route,
        method: sample.method,
        status_class: sample.status,
        request_count: sorted.length,
        p50_ms: sorted[Math.max(0, Math.ceil(sorted.length * 0.5) - 1)],
        p95_ms: sorted[Math.max(0, Math.ceil(sorted.length * 0.95) - 1)],
      });
    }
    latency.clear();
  }, 60000);
  setInterval(() => void flush(), 10000);
  document.addEventListener("visibilitychange", beaconFlush);
  window.addEventListener("pagehide", beaconFlush);
  window.addEventListener("error", () => track("frontend_unhandled_error", {
    route_template: canonicalRoute(),
    error_code: "unknown",
    release: "telemetry-v1",
  }));
  window.addEventListener("unhandledrejection", () => track("frontend_unhandled_error", {
    route_template: canonicalRoute(),
    error_code: "unknown",
    release: "telemetry-v1",
  }));
}

export function canonicalRoute(): string {
  return routeNames.includes(window.location.pathname) ? window.location.pathname : "unmatched";
}

function apiRoute(input: string): string {
  const path = input.split("?")[0].replace(/^\/api/, "");
  if (/^\/inventory\/products\/\d+$/.test(path)) return "/inventory/products/{supply_id}";
  if (/^\/suppliers\/\d+\/rate$/.test(path)) return "/suppliers/{supplier_id}/rate";
  if (/^\/suppliers\/\d+\/status$/.test(path)) return "/suppliers/{supplier_id}/status";
  if (/^\/suppliers\/\d+$/.test(path)) return "/suppliers/{supplier_id}";
  if (["/suppliers", "/inventory/products", "/inventory/orders", "/inventory/orders/inbound", "/inventory/orders/outbound"].includes(path)) return path;
  return "unmatched";
}

export function track(eventType: string, properties: Properties, schemaVersion = SCHEMA_VERSION): void {
  if (typeof window === "undefined") return;
  initialize();
  const schema = catalog.events.find((event) => event.event_type === eventType);
  if (!schema || schemaVersion !== SCHEMA_VERSION) {
    console.warn("Telemetry event rejected: unknown contract.");
    return;
  }
  const fields = Object.entries<Descriptor | undefined>(schema.properties);
  if (Object.keys(properties).some((key) => !schema.allowlist.includes(key)) ||
    fields.some(([key, field]) => field !== undefined &&
      (key in properties ? !valid(properties[key], field) : field.required))) {
    console.warn("Telemetry event rejected: properties do not match allowlist.");
    return;
  }
  if (typeof properties.route_template === "string" &&
    ![...routeNames, "unmatched", "/inventory/products/{supply_id}", "/suppliers/{supplier_id}",
      "/suppliers/{supplier_id}/rate", "/suppliers/{supplier_id}/status"].includes(properties.route_template)) {
    console.warn("Telemetry event rejected: route is not canonical.");
    return;
  }
  const now = Date.now();
  for (const [key, timestamp] of recent) if (now - timestamp >= 30000) recent.delete(key);
  if (["frontend_route_viewed", "inventory_orders_viewed", "frontend_unhandled_error", "auth_session_rejected"].includes(eventType)) {
    const key = `${ensureSessionId()}:${eventType}:${JSON.stringify(properties)}`;
    if (recent.has(key)) return;
    recent.set(key, now);
  }
  const event: Event = {
    eventId: crypto.randomUUID(),
    timestamp: new Date().toISOString(),
    sessionId: ensureSessionId(),
    userId,
    event_type: eventType,
    schemaVersion,
    requestId: correlationId ?? crypto.randomUUID(),
    properties: structuredClone(properties),
  };
  if (new TextEncoder().encode(JSON.stringify(event)).length > MAX_EVENT_BYTES) {
    console.warn("Telemetry event discarded: size limit.");
    return;
  }
  if (!endpoint()) {
    console.warn("Telemetry disabled: configure a same-origin NEXT_PUBLIC_TELEMETRY_ENDPOINT.");
    return;
  }
  if (eventType === "supplier_filter_applied" || eventType === "inventory_order_validation_failed") {
    const key = eventType === "supplier_filter_applied" ? eventType : `${eventType}:${JSON.stringify({ ...properties, occurrence_count: 0 })}`;
    const previous = pending.get(key);
    if (previous) {
      clearTimeout(previous.timer);
      if (eventType === "inventory_order_validation_failed") {
        event.properties.occurrence_count = Number(previous.event.properties.occurrence_count) + 1;
        event.timestamp = previous.event.timestamp;
      }
    }
    if (pending.size >= MAX_QUEUE && !previous) {
      console.warn("Telemetry pending queue full; event discarded.");
      return;
    }
    pending.set(key, {
      event,
      timer: setTimeout(() => {
        pending.delete(key);
        enqueue(event);
      }, eventType === "supplier_filter_applied" ? 500 : 5000),
    });
    if (document.visibilityState === "hidden") queueMicrotask(beaconFlush);
    return;
  }
  enqueue(event);
}

function enqueue(event: Event) {
  if (queue.length >= MAX_QUEUE) {
    queue.shift();
    console.warn("Telemetry queue full; oldest event discarded.");
  }
  queue.push(event);
  if (document.visibilityState === "hidden") queueMicrotask(beaconFlush);
  else if (queue.length >= BATCH_SIZE) void flush();
}

export function withResponseTelemetry(response: Response, capture: () => void): void {
  const previous = correlationId;
  const header = response.headers.get("x-request-id");
  correlationId = header && uuidPattern.test(header) ? header : null;
  try {
    capture();
  } finally {
    correlationId = previous;
  }
}

export async function telemetryFetch(
  input: string,
  init?: RequestInit,
  transport: (input: string, init?: RequestInit) => Promise<Response> = fetch,
): Promise<Response> {
  const requestId = crypto.randomUUID();
  const headers = new Headers(init?.headers);
  headers.set("X-Request-ID", requestId);
  const started = performance.now();
  const response = await transport(input, { ...init, headers });
  const route = apiRoute(input);
  const method = init?.method ?? "GET";
  const status = `${Math.floor(response.status / 100)}xx`;
  const key = `${method}:${route}:${status}`;
  if (!latency.has(key)) latency.set(key, { route, method, status, values: [] });
  const sample = latency.get(key)!;
  if (sample.values.length < 1000) sample.values.push(performance.now() - started);
  if (response.status === 401) {
    userId = null;
    withResponseTelemetry(response, () => track("auth_session_rejected", {
      reason_code: "invalid",
      route_template: route,
    }));
  }
  return response;
}

export async function trackNavigation(pathname: string): Promise<void> {
  if (!routeNames.includes(pathname)) return;
  initialize();
  await refreshSession();
  track("frontend_route_viewed", {
    route_template: pathname,
    section: pathname.startsWith("/inventory") ? "inventory" :
      pathname === "/suppliers" ? "suppliers" :
        pathname.startsWith("/account") ? "account" : "authentication",
  });
}

export async function captureAuthentication(action: string, response: Response): Promise<void> {
  if (action === "login") {
    if (response.ok) {
      userId = null;
      role = null;
      sessionId = crypto.randomUUID();
      try {
        sessionStorage.setItem("healthcore_telemetry_session", sessionId);
      } catch {
        console.warn("Telemetry login session is memory-only.");
      }
      if (sessionLookup) await sessionLookup;
      await refreshSession();
      if (role) withResponseTelemetry(response, () => track("auth_login_succeeded", { role }));
    } else {
      withResponseTelemetry(response, () => track("auth_login_failed", {
        failure_reason: response.status === 401 ? "invalid_credentials" : response.status === 429 ? "rate_limited" : "service_unavailable",
      }));
    }
  } else if (action === "forgot-password" && response.ok) {
    withResponseTelemetry(response, () => track("auth_password_recovery_requested", { outcome: "accepted_neutral" }));
  } else if (action === "change-password" && response.ok) {
    userId = null;
    role = null;
  }
}

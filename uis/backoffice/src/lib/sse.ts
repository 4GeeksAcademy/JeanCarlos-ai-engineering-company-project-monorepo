// Server-Sent Events over fetch + ReadableStream (Sistemas en Tiempo Real, Parte 1).
//
// Why not EventSource: it cannot send an Authorization header and has no control over the
// reconnection. Here the stream is a plain authenticated fetch: in the backoffice the browser calls
// the same-origin route /api/rfp/events with its session cookie and the Next server forwards the
// backoffice JWT as `Authorization: Bearer …` to the API (the token never reaches page JavaScript).
//
// Reconnection: exponential backoff with jitter (1 s, 2 s, 4 s … up to 30 s), reset once the server
// confirms the stream (`stream_ready`). A watchdog aborts a connection that has been silent for
// longer than three heartbeats (a half-open TCP connection after a network cut never errors on its
// own). Every reconnection sends Last-Event-ID — taken from the `id:` of every frame, including
// stream_ready / resync_required, so there is a cursor from the first frame on — and the server
// replays what was missed. A 401 stops
// the loop: the session has expired and retrying would not help.
//
// No framework imports: the same module runs in the browser and in tests/realtime (node --test).

export type SseFrame = { event: string; data: string; id?: string };

/** Incremental parser of the text/event-stream format (WHATWG HTML §9.2.6). */
export class SseParser {
  private buffer = "";
  private event = "";
  private data: string[] = [];
  private id: string | undefined = undefined;
  retry: number | undefined = undefined;

  feed(chunk: string): SseFrame[] {
    this.buffer += chunk;
    const frames: SseFrame[] = [];
    let newline: number;
    while ((newline = this.buffer.search(/\r\n|\r|\n/)) >= 0) {
      const line = this.buffer.slice(0, newline);
      const length = this.buffer.startsWith("\r\n", newline) ? 2 : 1;
      if (this.buffer[newline] === "\r" && newline + 1 === this.buffer.length) break; // wait: maybe "\r\n"
      this.buffer = this.buffer.slice(newline + length);
      if (line === "") {
        if (this.data.length > 0) {
          frames.push({ event: this.event || "message", data: this.data.join("\n"), ...(this.id !== undefined ? { id: this.id } : {}) });
        }
        this.event = "";
        this.data = [];
        continue;
      }
      if (line.startsWith(":")) continue; // comment: keep-alive
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") this.event = value;
      else if (field === "data") this.data.push(value);
      else if (field === "id" && !value.includes("\0")) this.id = value;
      else if (field === "retry" && /^\d+$/.test(value)) this.retry = Number(value);
    }
    return frames;
  }

  /** The cursor the server gave last (sent back as Last-Event-ID). */
  get lastEventId(): string | undefined {
    return this.id;
  }
}

/** `last_event_id` of a stream_ready / resync_required payload, as a Last-Event-ID value. */
export function cursorOf(frame: SseFrame): string | undefined {
  try {
    const value = (JSON.parse(frame.data) as { last_event_id?: unknown }).last_event_id;
    return typeof value === "number" && Number.isInteger(value) && value >= 0 ? String(value) : undefined;
  } catch {
    return undefined;
  }
}

export type Backoff = { baseMs: number; maxMs: number; jitter: number };
export const DEFAULT_BACKOFF: Backoff = { baseMs: 1000, maxMs: 30000, jitter: 0.2 };

/** attempt 1 → ~1 s, 2 → ~2 s, 3 → ~4 s … capped at maxMs; ± jitter so clients do not reconnect in lockstep. */
export function backoffDelay(attempt: number, backoff: Backoff = DEFAULT_BACKOFF, random: () => number = Math.random): number {
  const exponential = Math.min(backoff.maxMs, backoff.baseMs * 2 ** Math.max(0, attempt - 1));
  return Math.round(exponential * (1 - backoff.jitter + 2 * backoff.jitter * random()));
}

export type StreamState = "connecting" | "open" | "reconnecting" | "expired" | "closed";
export type StreamStatus = { state: StreamState; attempt: number; retryInMs?: number; lastEventId?: string; reason?: string };

export type StreamOptions = {
  url: string;
  onEvent: (frame: SseFrame) => void;
  onStatus?: (status: StreamStatus) => void;
  fetchImpl?: typeof fetch;
  backoff?: Backoff;
  stallMs?: number; // watchdog: no byte (not even a keep-alive) for this long → reconnect
  random?: () => number;
  lastEventId?: string;
};

export type EventStream = { close: () => void; reconnectNow: () => void; lastEventId: () => string | undefined };

export function openEventStream(options: StreamOptions): EventStream {
  const fetchImpl = options.fetchImpl ?? fetch;
  const backoff = options.backoff ?? DEFAULT_BACKOFF;
  const stallMs = options.stallMs ?? 45000;
  let lastEventId = options.lastEventId;
  let attempt = 0;
  let closed = false;
  let controller: AbortController | null = null;
  let wakeUp: (() => void) | null = null;

  const status = (s: Omit<StreamStatus, "lastEventId">) => options.onStatus?.({ ...s, lastEventId });

  const wait = (ms: number) =>
    new Promise<void>((resolve) => {
      const timer = setTimeout(done, ms);
      function done() {
        clearTimeout(timer);
        wakeUp = null;
        resolve();
      }
      wakeUp = done;
    });

  async function connectOnce(): Promise<"retry" | "stop"> {
    controller = new AbortController();
    const current = controller;
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    const arm = () => {
      clearTimeout(watchdog);
      watchdog = setTimeout(() => current.abort(new Error("stalled")), stallMs);
    };
    status({ state: attempt === 0 ? "connecting" : "reconnecting", attempt });
    try {
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      if (lastEventId !== undefined) headers["Last-Event-ID"] = lastEventId;
      arm();
      const response = await fetchImpl(options.url, { headers, signal: current.signal, cache: "no-store", credentials: "same-origin" });
      if (response.status === 401) {
        status({ state: "expired", attempt, reason: "Sesión expirada" });
        return "stop";
      }
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
      const parser = new SseParser();
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        arm();
        for (const frame of parser.feed(value)) {
          if (frame.id !== undefined) lastEventId = frame.id;
          else if (frame.event === "stream_ready" || frame.event === "resync_required") {
            // the cursor the server resumes from: kept even if no ticket event ever arrives on this
            // connection, so a cut right after connecting still reconnects with Last-Event-ID
            const cursor = cursorOf(frame);
            if (cursor !== undefined) lastEventId = cursor;
          }
          if (frame.event === "stream_ready") {
            attempt = 0; // the server confirmed the stream: the next failure starts the backoff again
            status({ state: "open", attempt });
          }
          options.onEvent(frame);
        }
      }
      return "retry"; // the server closed the stream (restart, session_expired, slow client)
    } catch (error) {
      if (closed) return "stop";
      status({ state: "reconnecting", attempt, reason: error instanceof Error ? error.message : "network error" });
      return "retry";
    } finally {
      clearTimeout(watchdog);
      current.abort();
    }
  }

  (async () => {
    while (!closed) {
      if ((await connectOnce()) === "stop" || closed) break;
      attempt += 1;
      const delay = backoffDelay(attempt, backoff, options.random);
      status({ state: "reconnecting", attempt, retryInMs: delay });
      await wait(delay);
    }
    if (closed) status({ state: "closed", attempt });
  })();

  return {
    close() {
      closed = true;
      controller?.abort();
      wakeUp?.();
    },
    reconnectNow() {
      // the browser says the network is back: skip the rest of the backoff
      if (wakeUp) wakeUp();
      else controller?.abort(new Error("reconnect"));
    },
    lastEventId: () => lastEventId,
  };
}

// ---------------------------------------------------------------- rfp_ticket_created

export const RFP_TICKET_CREATED = "rfp_ticket_created";

export type RfpTicketCreated = {
  ticket_id: string; rfp_id: string; client_name: string | null; client_country: string | null;
  program_type: string | null; status: string; created_at: string;
};

export function parseTicketCreated(frame: SseFrame): RfpTicketCreated | null {
  if (frame.event !== RFP_TICKET_CREATED) return null;
  try {
    const data = JSON.parse(frame.data) as Partial<RfpTicketCreated>;
    if (typeof data.ticket_id !== "string" || typeof data.rfp_id !== "string" || typeof data.status !== "string") return null;
    return data as RfpTicketCreated;
  } catch {
    return null;
  }
}

/** Deduplication by ticket_id: a replayed or repeated event never shows the same ticket twice. */
export class TicketDeduper {
  private seen = new Set<string>();

  markSeen(ticketIds: Iterable<string>): void {
    for (const id of ticketIds) this.seen.add(id);
  }

  /** true the first time a ticket is offered, false afterwards. */
  accept(ticketId: string): boolean {
    if (this.seen.has(ticketId)) return false;
    this.seen.add(ticketId);
    return true;
  }
}

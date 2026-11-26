// WebSocket chat of the Compliance assistant (Sistemas en Tiempo Real, Parte 2): the client side.
//
// - ChatSocket: one persistent connection bound to a session_id. Authenticates with the backoffice
//   JWT in the first frame, reconnects with progressive backoff (same schedule as the SSE stream of
//   Part 1) and asks the server again for the session: the `session_snapshot` rehydrates the thread
//   BEFORE any new token is accepted.
// - applyChatEvent: a pure reducer of the event contract (token_chunk, generation_interrupted,
//   generation_completed, session_snapshot…). Tokens are ordered and deduplicated by `sequence`:
//   one already shown (or already in the snapshot) is ignored, a gap forces a resync.
//
// No framework imports: the same module runs in the browser and in tests/realtime (node --test).

import { backoffDelay, DEFAULT_BACKOFF, type Backoff } from "./sse.ts";

export type ChatRole = "user" | "assistant";
export type MessageStatus = "complete" | "streaming" | "interrupted" | "failed";
export type ChatMessage = { message_id: string; role: ChatRole; text: string; status: MessageStatus; sequence?: number; created_at?: string };
export type ChatEvent = { event: string; data: Record<string, unknown> };

export type ChatState = {
  sessionId: string;
  ready: boolean; // a snapshot has been applied: tokens can be accepted
  role: "participant" | "observer" | null;
  status: string | null; // ChatSession.status
  messages: ChatMessage[];
  generating: string | null; // message_id being generated
  needsResync: boolean; // a token sequence gap: reconnect to get a fresh snapshot
  error: string | null;
};

export function initialChatState(sessionId: string): ChatState {
  return { sessionId, ready: false, role: null, status: null, messages: [], generating: null, needsResync: false, error: null };
}

function upsert(messages: ChatMessage[], message: ChatMessage): ChatMessage[] {
  const index = messages.findIndex((m) => m.message_id === message.message_id);
  if (index < 0) return [...messages, message];
  const next = messages.slice();
  next[index] = { ...messages[index], ...message };
  return next;
}

export function applyChatEvent(state: ChatState, event: ChatEvent): ChatState {
  const d = event.data ?? {};
  if (typeof d.session_id === "string" && d.session_id !== state.sessionId) return state; // another session: ignore
  switch (event.event) {
    case "session_snapshot": {
      const messages = (d.messages as ChatMessage[]).map((m) => ({ ...m, sequence: m.sequence ?? (m.status === "streaming" ? 0 : undefined) }));
      const streaming = d.streaming as { message_id: string; sequence: number } | null;
      return { ...state, ready: true, needsResync: false, role: d.role as ChatState["role"], status: d.status as string,
        messages, generating: streaming ? streaming.message_id : null, error: null };
    }
    case "user_message":
      return { ...state, messages: upsert(state.messages, { message_id: d.message_id as string, role: "user", text: d.text as string, status: "complete" }) };
    case "generation_started":
      return { ...state, status: "active", generating: d.message_id as string,
        messages: upsert(state.messages, { message_id: d.message_id as string, role: "assistant", text: "", status: "streaming", sequence: 0 }) };
    case "token_chunk": {
      if (!state.ready) return state; // rehydrate first, then tokens
      const current = state.messages.find((m) => m.message_id === d.message_id);
      const sequence = d.sequence as number;
      const known = current?.sequence ?? 0;
      if (current && current.status !== "streaming") return state; // finished or interrupted: no more tokens
      if (current && sequence <= known) return state; // already shown (or in the snapshot)
      if (sequence !== known + 1) return { ...state, needsResync: true }; // a gap: never show a hole
      const base: ChatMessage = current ?? { message_id: d.message_id as string, role: "assistant", text: "", status: "streaming" };
      return { ...state, generating: base.message_id,
        messages: upsert(state.messages, { ...base, text: base.text + (d.token as string), sequence, status: "streaming" }) };
    }
    case "generation_interrupted":
      return { ...state, status: "interrupted", generating: state.generating === d.message_id ? null : state.generating,
        messages: upsert(state.messages, { ...(state.messages.find((m) => m.message_id === d.message_id) ?? {
          message_id: d.message_id as string, role: "assistant", text: "" }), status: "interrupted" } as ChatMessage) };
    case "generation_completed":
      return { ...state, generating: state.generating === d.message_id ? null : state.generating,
        messages: upsert(state.messages, { message_id: d.message_id as string, role: "assistant", text: d.text as string,
          status: "complete", sequence: d.sequence as number }) };
    case "generation_failed":
      return { ...state, generating: state.generating === d.message_id ? null : state.generating,
        messages: upsert(state.messages, { ...(state.messages.find((m) => m.message_id === d.message_id) ?? {
          message_id: d.message_id as string, role: "assistant", text: d.detail as string }), status: "failed" } as ChatMessage) };
    case "session_closed":
      return { ...state, status: "closed", generating: null };
    case "chat_error":
      return { ...state, error: d.detail as string };
    default:
      return state; // interrupt_requested and unknown events change nothing on screen
  }
}

// ---------------------------------------------------------------- connection

export type SocketState = "connecting" | "open" | "reconnecting" | "expired" | "forbidden" | "closed";
export type SocketStatus = { state: SocketState; attempt: number; retryInMs?: number; reason?: string };

type WebSocketLike = {
  readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number; reason?: string }) => void) | null;
  onerror: ((event: unknown) => void) | null;
};

export type ChatSocketOptions = {
  sessionId: string;
  credentials: () => Promise<{ token: string; ws_url: string }>; // fetched again on every (re)connection
  onEvent: (event: ChatEvent) => void;
  onStatus?: (status: SocketStatus) => void;
  createSocket?: (url: string) => WebSocketLike;
  backoff?: Backoff;
  random?: () => number;
};

const FINAL_CODES: Record<number, SocketState> = { 4400: "forbidden", 4401: "expired", 4403: "forbidden", 4404: "forbidden" };

export class ChatSocket {
  private socket: WebSocketLike | null = null;
  private attempt = 0;
  private closed = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private options: ChatSocketOptions;

  constructor(options: ChatSocketOptions) {
    this.options = options;
  }

  start(): void {
    void this.connect();
  }

  private status(status: SocketStatus) {
    this.options.onStatus?.(status);
  }

  private async connect(): Promise<void> {
    if (this.closed) return;
    this.status({ state: this.attempt === 0 ? "connecting" : "reconnecting", attempt: this.attempt });
    let credentials: { token: string; ws_url: string };
    try {
      credentials = await this.options.credentials();
    } catch (error) {
      if (error instanceof Error && error.message === "expired") return this.status({ state: "expired", attempt: this.attempt });
      return this.retry("credenciales no disponibles");
    }
    if (this.closed) return;
    const url = `${credentials.ws_url}?session_id=${encodeURIComponent(this.options.sessionId)}`;
    const create = this.options.createSocket ?? ((u: string) => new WebSocket(u) as unknown as WebSocketLike);
    const socket = create(url);
    this.socket = socket;
    socket.onopen = () => socket.send(JSON.stringify({ event: "auth", data: { token: credentials.token } }));
    socket.onmessage = (message) => {
      let event: ChatEvent;
      try {
        event = JSON.parse(String(message.data)) as ChatEvent;
      } catch {
        return;
      }
      if (event.event === "session_snapshot") {
        this.attempt = 0; // the server confirmed the session: the backoff starts again on the next failure
        this.status({ state: "open", attempt: 0 });
      }
      this.options.onEvent(event);
    };
    socket.onerror = () => undefined; // onclose follows
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.socket = null;
      if (this.closed) return this.status({ state: "closed", attempt: this.attempt });
      const final = FINAL_CODES[event.code];
      if (final) return this.status({ state: final, attempt: this.attempt, reason: event.reason });
      this.retry(event.reason || `código ${event.code}`);
    };
  }

  private retry(reason: string) {
    this.attempt += 1;
    const delay = backoffDelay(this.attempt, this.options.backoff ?? DEFAULT_BACKOFF, this.options.random);
    this.status({ state: "reconnecting", attempt: this.attempt, retryInMs: delay, reason });
    clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.connect(), delay);
  }

  /** Drop the connection and reconnect (e.g. a token sequence gap: the snapshot resyncs). */
  resync(): void {
    this.socket?.close(4000, "resync");
  }

  reconnectNow(): void {
    if (this.socket || this.closed) return;
    clearTimeout(this.timer);
    void this.connect();
  }

  send(event: "user_message" | "interrupt_requested", data: Record<string, unknown>): boolean {
    if (!this.socket || this.socket.readyState !== 1) return false;
    this.socket.send(JSON.stringify({ event, data: { session_id: this.options.sessionId, ...data } }));
    return true;
  }

  close(): void {
    this.closed = true;
    clearTimeout(this.timer);
    this.socket?.close(1000, "bye");
  }
}

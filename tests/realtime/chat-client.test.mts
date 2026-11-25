// Client side of the Compliance assistant chat (uis/backoffice/src/lib/chat-socket.ts): the event
// reducer and the WebSocket connection with a fake socket — auth in the first frame, rehydration
// from session_snapshot before any token, dedup/order by sequence, interrupt keeps the partial and
// the redirected answer is a new message, backoff reconnection to the SAME session_id.
//
//     node --test tests/realtime/chat-client.test.mts

import assert from "node:assert/strict";
import { test } from "node:test";

import { applyChatEvent, ChatSocket, initialChatState, type ChatEvent, type SocketStatus } from "../../uis/backoffice/src/lib/chat-socket.ts";

const S = "chat_0076aa00bb11";
const ev = (event: string, data: Record<string, unknown>): ChatEvent => ({ event, data: { session_id: S, ...data } });
const run = (events: ChatEvent[]) => events.reduce(applyChatEvent, initialChatState(S));
const token = (message_id: string, sequence: number, t: string) => ev("token_chunk", { message_id, sequence, token: t });

test("tokens are only accepted after the snapshot, in order, without duplicates", () => {
  const before = run([token("msg_1", 1, "Hola")]);
  assert.deepEqual(before.messages, []); // not rehydrated yet: the token waits for the snapshot
  const state = run([
    ev("session_snapshot", { status: "active", role: "participant", streaming: { message_id: "msg_1", sequence: 2 },
      messages: [{ message_id: "msg_0", role: "user", text: "¿HIPAA?", status: "complete" },
        { message_id: "msg_1", role: "assistant", text: "Según HI", status: "streaming", sequence: 2 }] }),
    token("msg_1", 2, "HI"), // already in the snapshot
    token("msg_1", 3, "PAA, "),
    token("msg_1", 3, "PAA, "), // duplicated delivery
    token("msg_1", 4, "el personal"),
  ]);
  assert.equal(state.messages[1].text, "Según HIPAA, el personal");
  assert.equal(state.generating, "msg_1");
  assert.equal(run([ev("session_snapshot", { status: "active", role: "participant", streaming: null, messages: [] }),
    ev("generation_started", { message_id: "msg_2" }), token("msg_2", 2, "x")]).needsResync, true); // a gap
});

test("an interrupt keeps the partial message and the redirected answer is a new message", () => {
  const state = run([
    ev("session_snapshot", { status: "active", role: "participant", streaming: null, messages: [] }),
    ev("user_message", { message_id: "msg_1", text: "¿Qué permite HIPAA?" }),
    ev("generation_started", { message_id: "msg_2" }),
    token("msg_2", 1, "Bajo HIPAA, "), token("msg_2", 2, "el "),
    ev("interrupt_requested", { new_input: "espera, mi pregunta era sobre UK GDPR, no HIPAA" }),
    ev("generation_interrupted", { message_id: "msg_2", status: "interrupted" }),
    token("msg_2", 3, "personal"), // a late token of the aborted generation is never shown
    ev("user_message", { message_id: "msg_3", text: "espera, mi pregunta era sobre UK GDPR, no HIPAA" }),
    ev("generation_started", { message_id: "msg_4" }),
    token("msg_4", 1, "Según UK GDPR"),
    ev("generation_completed", { message_id: "msg_4", status: "complete", text: "Según UK GDPR…", sequence: 1 }),
  ]);
  assert.deepEqual(state.messages.map((m) => [m.message_id, m.role, m.status, m.text]), [
    ["msg_1", "user", "complete", "¿Qué permite HIPAA?"],
    ["msg_2", "assistant", "interrupted", "Bajo HIPAA, el "],
    ["msg_3", "user", "complete", "espera, mi pregunta era sobre UK GDPR, no HIPAA"],
    ["msg_4", "assistant", "complete", "Según UK GDPR…"],
  ]);
  assert.equal(state.generating, null);
  assert.equal(run([ev("token_chunk", { session_id: "chat_other", message_id: "m", sequence: 1, token: "x" })]).messages.length, 0);
});

class FakeSocket {
  static all: FakeSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: ((e: unknown) => void) | null = null;
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onclose: ((e: { code: number; reason?: string }) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeSocket.all.push(this);
  }
  open() {
    this.readyState = 1;
    this.onopen?.({});
  }
  push(event: ChatEvent) {
    this.onmessage?.({ data: JSON.stringify(event) });
  }
  send(data: string) {
    this.sent.push(data);
  }
  close(code = 1000, reason = "") {
    this.readyState = 3;
    this.onclose?.({ code, reason });
  }
}

const until = async (condition: () => boolean, ms = 2000) => {
  const deadline = Date.now() + ms;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error("timeout");
    await new Promise((r) => setTimeout(r, 5));
  }
};

test("the socket authenticates in the first frame, reconnects with backoff to the same session and rehydrates", async () => {
  FakeSocket.all = [];
  const statuses: SocketStatus[] = [];
  const events: string[] = [];
  let fetched = 0;
  const chat = new ChatSocket({
    sessionId: S,
    credentials: async () => ({ token: `jwt-${++fetched}`, ws_url: "ws://api/agent/chat/ws" }),
    onEvent: (e) => events.push(e.event),
    onStatus: (s) => statuses.push(s),
    createSocket: (url) => new FakeSocket(url),
    backoff: { baseMs: 10, maxMs: 40, jitter: 0 },
  });
  chat.start();
  await until(() => FakeSocket.all.length === 1);
  const first = FakeSocket.all[0];
  assert.equal(first.url, `ws://api/agent/chat/ws?session_id=${S}`); // bound to the session; no token in the URL
  first.open();
  assert.deepEqual(JSON.parse(first.sent[0]), { event: "auth", data: { token: "jwt-1" } });
  first.push(ev("session_snapshot", { status: "active", role: "participant", streaming: null, messages: [] }));
  assert.ok(chat.send("user_message", { text: "¿HIPAA?" }));
  assert.deepEqual(JSON.parse(first.sent[1]), { event: "user_message", data: { session_id: S, text: "¿HIPAA?" } });

  first.close(1006, "network"); // the connection drops
  assert.equal(chat.send("user_message", { text: "x" }), false); // nothing is sent into the void
  await until(() => FakeSocket.all.length === 2);
  FakeSocket.all[1].close(1006); // still down
  await until(() => FakeSocket.all.length === 3);
  const third = FakeSocket.all[2];
  assert.equal(third.url, first.url); // the same session_id
  third.open();
  assert.deepEqual(JSON.parse(third.sent[0]), { event: "auth", data: { token: "jwt-3" } }); // fresh credentials
  third.push(ev("session_snapshot", { status: "active", role: "participant", streaming: null, messages: [] }));
  const waits = statuses.filter((s) => s.state === "reconnecting" && s.retryInMs !== undefined).map((s) => s.retryInMs);
  assert.deepEqual(waits, [10, 20]); // progressive backoff
  assert.deepEqual(statuses.at(-1), { state: "open", attempt: 0 }); // reset once the session is rehydrated
  assert.deepEqual(events, ["session_snapshot", "session_snapshot"]);
  chat.close();
});

test("an expired token or a forbidden session stops the reconnection", async () => {
  FakeSocket.all = [];
  const statuses: SocketStatus[] = [];
  const chat = new ChatSocket({
    sessionId: S, credentials: async () => ({ token: "t", ws_url: "ws://api/agent/chat/ws" }), onEvent: () => {},
    onStatus: (s) => statuses.push(s), createSocket: (url) => new FakeSocket(url), backoff: { baseMs: 5, maxMs: 5, jitter: 0 },
  });
  chat.start();
  await until(() => FakeSocket.all.length === 1);
  FakeSocket.all[0].open();
  FakeSocket.all[0].close(4401, "authentication required");
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(FakeSocket.all.length, 1);
  assert.equal(statuses.at(-1)?.state, "expired");
});

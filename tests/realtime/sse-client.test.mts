// Client side of the RFP real-time stream (uis/backoffice/src/lib/sse.ts), with a fake fetch that
// behaves like the API: the stream drops, the client backs off, reconnects with Last-Event-ID,
// the replay is applied and a ticket is never shown twice; a silent connection is aborted by the
// watchdog; a 401 stops the loop.
//
//     node --test tests/realtime/sse-client.test.mts      (Node ≥ 22.18: TypeScript types are stripped)

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  backoffDelay,
  openEventStream,
  parseTicketCreated,
  SseParser,
  TicketDeduper,
  type SseFrame,
  type StreamStatus,
} from "../../uis/backoffice/src/lib/sse.ts";

const ticket = (n: number) => ({
  ticket_id: `00000000-0000-0000-0000-00000000000${n}`, rfp_id: `RFP-20261125-00000${n}`,
  client_name: "Meridian Manufacturing, Inc.", client_country: "US", program_type: "occupational_health",
  status: "analizando", created_at: "2026-11-25T10:00:00Z",
});
const created = (id: number, n: number) => `id: ${id}\nevent: rfp_ticket_created\ndata: ${JSON.stringify(ticket(n))}\n\n`;
const ready = (last: number, replayed: number) =>
  `retry: 3000\n\nevent: stream_ready\ndata: {"last_event_id":${last},"replayed":${replayed},"heartbeat_seconds":15}\n\n`;

/** A response whose body emits `chunks` and then either stays open, ends, or fails like a network cut. */
function sseResponse(chunks: string[], end: "open" | "close" | "error" = "open", signal?: AbortSignal): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      if (end === "close") controller.close();
      if (end === "error") setTimeout(() => controller.error(new TypeError("network error")), 5);
      signal?.addEventListener("abort", () => {
        try {
          controller.error(signal.reason ?? new Error("aborted"));
        } catch {
          /* already closed */
        }
      });
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

const until = async (condition: () => boolean, ms = 2000) => {
  const deadline = Date.now() + ms;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error("timeout");
    await new Promise((r) => setTimeout(r, 5));
  }
};

test("the parser handles split chunks, CRLF, comments, multi-line data and ids", () => {
  const parser = new SseParser();
  const frames: SseFrame[] = [];
  for (const chunk of ["id: 7\r\nevent: rfp_ticket_cre", "ated\r\ndata: {\"a\":", "1}\r\n\r\n", ": keep-alive\n\n",
    "event: x\ndata: one\ndata: two\n\n"]) {
    frames.push(...parser.feed(chunk));
  }
  assert.deepEqual(frames, [
    { event: "rfp_ticket_created", data: "{\"a\":1}", id: "7" },
    { event: "x", data: "one\ntwo", id: "7" },
  ]);
  assert.equal(parser.lastEventId, "7");
  assert.deepEqual(parser.feed("retry: 3000\n\n"), []);
  assert.equal(parser.retry, 3000);
});

test("the backoff grows exponentially up to 30 s, with jitter", () => {
  assert.deepEqual([1, 2, 3, 4, 5, 6, 7, 8].map((a) => backoffDelay(a, undefined, () => 0.5)),
    [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000]);
  assert.equal(backoffDelay(1, undefined, () => 0), 800);
  assert.equal(backoffDelay(1, undefined, () => 1), 1200);
});

test("after a network cut the client backs off, reconnects with Last-Event-ID and never shows a ticket twice", async () => {
  const requests: Record<string, string>[] = [];
  const shown: string[] = [];
  const statuses: StreamStatus[] = [];
  const deduper = new TicketDeduper();
  const fetchImpl = (async (_url: string, init: RequestInit) => {
    requests.push({ ...(init.headers as Record<string, string>) });
    if (requests.length === 1) return sseResponse([ready(0, 0), created(1, 1)], "error", init.signal!); // drops
    if (requests.length === 2) throw new TypeError("Failed to fetch"); // still offline
    // back online: the server replays after Last-Event-ID 1; an at-least-once duplicate of ticket 1 is included
    return sseResponse([ready(1, 2), created(1, 1), created(2, 2), created(3, 3)], "open", init.signal!);
  }) as typeof fetch;

  const stream = openEventStream({
    url: "/api/rfp/events", fetchImpl, backoff: { baseMs: 10, maxMs: 40, jitter: 0 },
    onStatus: (s) => statuses.push(s),
    onEvent: (frame) => {
      const event = parseTicketCreated(frame);
      if (event && deduper.accept(event.ticket_id)) shown.push(event.rfp_id);
    },
  });
  await until(() => shown.length === 3);
  stream.close();

  assert.deepEqual(shown, ["RFP-20261125-000001", "RFP-20261125-000002", "RFP-20261125-000003"]);
  assert.equal(requests[0]["Last-Event-ID"], undefined);
  assert.equal(requests[1]["Last-Event-ID"], "1"); // the cursor survives the failed attempt
  assert.equal(requests[2]["Last-Event-ID"], "1");
  assert.equal(stream.lastEventId(), "3");
  const waits = statuses.filter((s) => s.state === "reconnecting" && s.retryInMs !== undefined).map((s) => s.retryInMs);
  assert.deepEqual(waits, [10, 20]); // backoff grows while the network is down
  assert.ok(statuses.some((s) => s.state === "open" && s.attempt === 0)); // reset once the stream is confirmed
});

test("a silent connection (half-open after a cut) is aborted by the watchdog and reconnected", async () => {
  let calls = 0;
  const fetchImpl = (async (_url: string, init: RequestInit) => {
    calls += 1;
    return sseResponse(calls === 1 ? [ready(0, 0)] : [ready(0, 0), created(1, 1)], "open", init.signal!);
  }) as typeof fetch;
  const events: string[] = [];
  const stream = openEventStream({
    url: "/api/rfp/events", fetchImpl, stallMs: 50, backoff: { baseMs: 5, maxMs: 5, jitter: 0 },
    onEvent: (f) => events.push(f.event),
  });
  await until(() => events.includes("rfp_ticket_created"));
  stream.close();
  assert.ok(calls >= 2);
});

test("a 401 stops the loop: the session expired", async () => {
  let calls = 0;
  const statuses: StreamStatus[] = [];
  const fetchImpl = (async () => {
    calls += 1;
    return new Response("{}", { status: 401 });
  }) as unknown as typeof fetch;
  openEventStream({ url: "/api/rfp/events", fetchImpl, onEvent: () => {}, onStatus: (s) => statuses.push(s) });
  await until(() => statuses.some((s) => s.state === "expired"));
  await new Promise((r) => setTimeout(r, 50));
  assert.equal(calls, 1);
});

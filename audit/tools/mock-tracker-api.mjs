// Deterministic mock of the Talent Pipeline Tracker API used by the audit.
// Same response shape as https://playground.4geeks.com/tracker/api/v1 but with
// synthetic data (no real people) and a fixed latency, so every Lighthouse run
// sees exactly the same payload.
//
//   node audit/tools/mock-tracker-api.mjs            # 1000 records on :4010
//   RECORDS=100 LATENCY_MS=150 PORT=4010 node audit/tools/mock-tracker-api.mjs
import { createServer } from "node:http";

const PORT = Number(process.env.PORT ?? 4010);
const COUNT = Number(process.env.RECORDS ?? 1000);
const LATENCY_MS = Number(process.env.LATENCY_MS ?? 150);

// Small deterministic PRNG (mulberry32) so the dataset never changes.
let seed = 20261118;
function random() {
  seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
const pick = (items) => items[Math.floor(random() * items.length)];

const FIRST = ["Alex", "Sam", "Jordan", "Taylor", "Robin", "Casey", "Morgan", "Jamie", "Avery", "Riley", "Noa", "Ariel"];
const LAST = ["Synthetic", "Example", "Sample", "Placeholder", "Demo", "Fixture", "Mock", "Testcase"];
const POSITIONS = ["Enfermera/o", "Recepcionista", "Coordinador/a Administrativa/o", "Técnica/o de laboratorio",
  "Jefa/e de Gabinete", "Analista de facturación", "Auxiliar clínico", "Especialista en cumplimiento"];
const STATUSES = ["received", "received", "in_progress", "in_progress", "selected", "discarded"];
const STAGES = ["pending", "review", "personal_interview", "technical_interview", "offer_presented"];
const NOTES = ["Buena comunicación en la llamada inicial.", "Revisar referencias antes de avanzar.",
  "Experiencia relevante en clínicas ambulatorias.", "Disponibilidad inmediata."];

const base = Date.UTC(2026, 0, 1);
const records = Array.from({ length: COUNT }, (_, index) => {
  const id = `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`;
  const name = `${pick(FIRST)} ${pick(LAST)} ${index + 1}`;
  const applied = new Date(base + Math.floor(random() * 300) * 86400000).toISOString();
  const notes = Array.from({ length: Math.floor(random() * 3) }, (_, n) => ({
    id: `${id.slice(0, -4)}${String(n).padStart(4, "0")}`, record_id: id, content: pick(NOTES), created_at: applied,
  }));
  return {
    id, full_name: name, email: `candidate${index + 1}@example.org`, phone: `+1 555 01${String(index % 100).padStart(2, "0")}`,
    position: pick(POSITIONS), linkedin_url: null, cv_url: null, status: pick(STATUSES), stage: pick(STAGES),
    experience_years: Math.floor(random() * 15), applied_at: applied, updated_at: applied,
    notes, notes_count: notes.length,
  };
});

function send(response, status, body) {
  setTimeout(() => {
    response.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    response.end(JSON.stringify(body));
  }, LATENCY_MS);
}

createServer((request, response) => {
  const url = new URL(request.url, `http://localhost:${PORT}`);
  const match = url.pathname.match(/^\/records(?:\/([^/]+))?$/);
  if (request.method === "POST" && match && !match[1]) {
    // Create (kept in memory) so the "Nueva candidatura" flow can be exercised.
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      const input = JSON.parse(body || "{}");
      const now = new Date().toISOString();
      const record = { id: `00000000-0000-4000-9000-${String(records.length + 1).padStart(12, "0")}`, linkedin_url: null, cv_url: null,
        status: "received", stage: "pending", ...input, experience_years: Number(input.experience_years ?? 0),
        notes: [], notes_count: 0, applied_at: now, updated_at: now };  // same defaults as the real API
      records.unshift(record);
      send(response, 201, record);
    });
    return;
  }
  if (request.method !== "GET" || !match) return send(response, 404, { detail: "Not found" });
  if (match[1]) {
    const record = records.find((item) => item.id === match[1]);
    return record ? send(response, 200, record) : send(response, 404, { detail: "Not found" });
  }
  const search = (url.searchParams.get("search") ?? "").toLowerCase();
  const status = url.searchParams.get("status");
  const stage = url.searchParams.get("stage");
  const filtered = records.filter((item) =>
    (!search || item.full_name.toLowerCase().includes(search) || item.email.includes(search))
    && (!status || item.status === status) && (!stage || item.stage === stage));
  const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
  const limit = Math.min(1000, Math.max(1, Number(url.searchParams.get("limit") ?? 20)));
  send(response, 200, { total: filtered.length, page, limit, data: filtered.slice((page - 1) * limit, page * limit) });
}).listen(PORT, "127.0.0.1", () => console.log(`mock tracker API: ${COUNT} records, ${LATENCY_MS} ms, http://127.0.0.1:${PORT}`));

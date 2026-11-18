// Runs Lighthouse (performance only) RUNS times per route and form factor,
// writes every JSON to a scratch folder, and copies the median run's HTML/JSON
// report plus a summary into audit/<label>/.
//
//   CHROME_PATH=/path/to/chromium SESSION_COOKIE=healthcore_session=... \
//   node audit/tools/run-lighthouse.mjs <label> [runs]
//
// Requires the apps running in production mode (next build && next start):
// tracker on :3000 and backoffice on :3001.
// Optional: THROTTLING=devtools applies real network/CPU throttling during the
// load (Lighthouse default is "simulate"); ROUTES=tracker-home,... limits routes;
// FORM_FACTORS=mobile limits form factors.
import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const [label = "run", runsArg = "5"] = process.argv.slice(2);
const RUNS = Number(runsArg);
const LIGHTHOUSE = process.env.LIGHTHOUSE_BIN ?? "lighthouse"; // lighthouse@12.8.2
const OUT = join("audit", label);
const SCRATCH = process.env.SCRATCH_DIR ?? join(tmpdir(), `lighthouse-${label}`);
const cookie = process.env.SESSION_COOKIE;

const ALL_ROUTES = [
  { id: "tracker-home", url: "http://localhost:3000/" },
  { id: "backoffice-login", url: "http://localhost:3001/login" },
  { id: "backoffice-inventory", url: "http://localhost:3001/inventory/products", auth: true },
];
const ROUTES = process.env.ROUTES
  ? ALL_ROUTES.filter((route) => process.env.ROUTES.split(",").includes(route.id))
  : ALL_ROUTES;
const FORM_FACTORS = process.env.FORM_FACTORS ? process.env.FORM_FACTORS.split(",") : ["mobile", "desktop"];
const THROTTLING = process.env.THROTTLING ?? "simulate";

const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
};

function metrics(report) {
  const audit = (id) => report.audits[id];
  return {
    score: Math.round(report.categories.performance.score * 100),
    fcp: audit("first-contentful-paint").numericValue,
    lcp: audit("largest-contentful-paint").numericValue,
    cls: audit("cumulative-layout-shift").numericValue,
    tbt: audit("total-blocking-time").numericValue,
    si: audit("speed-index").numericValue,
    observedLcp: audit("metrics").details.items[0].observedLargestContentfulPaint,
    ttfb: audit("server-response-time").numericValue,
    finalUrl: report.finalDisplayedUrl ?? report.finalUrl,
    lcpElement: audit("largest-contentful-paint-element")?.details?.items?.[0]?.items?.[0]?.node?.snippet ?? null,
  };
}

mkdirSync(OUT, { recursive: true });
mkdirSync(SCRATCH, { recursive: true });
const summary = [];

for (const route of ROUTES) {
  for (const formFactor of FORM_FACTORS) {
    const runs = [];
    for (let run = 1; run <= RUNS; run += 1) {
      const base = join(SCRATCH, `${route.id}-${formFactor}-${run}`);
      const args = [route.url, "--quiet", "--only-categories=performance", "--output=json", "--output=html",
        `--output-path=${base}`, "--chrome-flags=--headless=new --no-first-run --disable-extensions"];
      if (formFactor === "desktop") args.push("--preset=desktop");
      args.push(`--throttling-method=${THROTTLING}`);
      if (route.auth) {
        if (!cookie) throw new Error("SESSION_COOKIE is required for authenticated routes");
        args.push(`--extra-headers=${JSON.stringify({ Cookie: cookie })}`);
      }
      execFileSync(LIGHTHOUSE, args, { stdio: "inherit" });
      const report = JSON.parse(readFileSync(`${base}.report.json`, "utf8"));
      const result = { run, base, ...metrics(report) };
      if (route.auth && new URL(result.finalUrl).pathname === "/login") {
        throw new Error(`${route.id}: redirected to /login, the session cookie is not valid`);
      }
      runs.push(result);
      console.log(`${label} ${route.id} ${formFactor} #${run}: score ${result.score} LCP ${Math.round(result.lcp)} ms CLS ${result.cls.toFixed(3)} TBT ${Math.round(result.tbt)} ms`);
    }
    // The median run (by score, then LCP) is the representative report.
    const representative = [...runs].sort((a, b) => a.score - b.score || b.lcp - a.lcp)[Math.floor(runs.length / 2)];
    const name = `${route.id}-${formFactor}`;
    copyFileSync(`${representative.base}.report.html`, join(OUT, `${name}.report.html`));
    copyFileSync(`${representative.base}.report.json`, join(OUT, `${name}.report.json`));
    const pick = (key) => median(runs.map((item) => item[key]));
    summary.push({
      route: route.id, url: route.url, formFactor, runs: RUNS,
      score: pick("score"), fcp: pick("fcp"), lcp: pick("lcp"), cls: pick("cls"), tbt: pick("tbt"),
      si: pick("si"), ttfb: pick("ttfb"), observedLcp: pick("observedLcp"), lcpElement: representative.lcpElement,
      all: runs.map(({ score, lcp, cls, tbt }) => ({ score, lcp: Math.round(lcp), cls: Number(cls.toFixed(3)), tbt: Math.round(tbt) })),
    });
  }
}

writeFileSync(join(OUT, "summary.json"), `${JSON.stringify({ label, date: new Date().toISOString(), runs: RUNS, throttling: THROTTLING, summary }, null, 2)}\n`);
console.log(`\nSummary written to ${join(OUT, "summary.json")}`);

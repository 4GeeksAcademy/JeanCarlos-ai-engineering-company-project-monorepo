// Interaction latency (Event Timing API, the basis of INP) for the tracker list:
// changing the status filter and typing in the search box, with 4x CPU
// throttling. Reports the worst interaction per run and the median of RUNS.
//   CHROME_PATH=... node audit/tools/measure-inp.mjs http://localhost:3000/ [runs]
import { chromium } from "playwright-core";

const [url = "http://localhost:3000/", runsArg = "5"] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH, headless: true });
const results = [];
for (let run = 1; run <= Number(runsArg); run += 1) {
  const page = await browser.newPage({ viewport: { width: 1350, height: 940 } });
  const cdp = await page.context().newCDPSession(page);
  await page.addInitScript(() => {
    window.__events = [];
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) if (entry.interactionId) window.__events.push({ name: entry.name, duration: entry.duration });
    }).observe({ type: "event", durationThreshold: 16, buffered: true });
  });
  await page.goto(url, { waitUntil: "networkidle" });
  await page.locator("table tbody tr").first().waitFor();
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  await page.locator("select").first().selectOption("in_progress");   // status filter
  await page.locator("table tbody tr").first().waitFor();
  await page.waitForTimeout(800);
  await page.locator("input[type=search]").pressSequentially("Sample", { delay: 120 });
  await page.waitForTimeout(1500);
  const events = await page.evaluate(() => window.__events);
  const worst = events.reduce((max, event) => Math.max(max, event.duration), 0);
  results.push({ run, worst: Math.round(worst), interactions: events.length });
  console.log(`run ${run}: worst interaction ${Math.round(worst)} ms over ${events.length} events`);
  await page.close();
}
const sorted = results.map((r) => r.worst).sort((a, b) => a - b);
console.log(JSON.stringify({ url, cpuThrottling: 4, median_worst_ms: sorted[Math.floor(sorted.length / 2)], runs: results }));
await browser.close();

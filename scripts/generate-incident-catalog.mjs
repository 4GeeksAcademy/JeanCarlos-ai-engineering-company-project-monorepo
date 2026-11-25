// Copies the shared incident catalog (single source of truth, also read by the
// Python API and seed script) into the backoffice. Legacy CSV maps are omitted.
// Usage: node scripts/generate-incident-catalog.mjs [--check]
import { readFile, writeFile } from "node:fs/promises";

const source = new URL("../packages/shared/incidents/catalog.json", import.meta.url);
const target = new URL("../uis/backoffice/src/services/incident-catalog.json", import.meta.url);
const { legacy_csv: _legacy, ...catalog } = JSON.parse(await readFile(source, "utf8"));
const output = `${JSON.stringify(catalog, null, 2)}\n`;
if (process.argv.includes("--check")) {
  const current = await readFile(target, "utf8").catch(() => "");
  if (current !== output) {
    throw new Error("Incident catalog is stale. Run node scripts/generate-incident-catalog.mjs");
  }
} else {
  await writeFile(target, output);
}

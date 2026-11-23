import { readFile, writeFile } from "node:fs/promises";

const source = new URL("../docs/telemetry/event-schemas.json", import.meta.url);
// The UI and the API are built from separate Docker contexts, so each gets its own copy.
const targets = [
  new URL("../uis/backoffice/src/services/telemetry-contract.json", import.meta.url),
  new URL("../services/api/telemetry_contract.json", import.meta.url),
];
const catalog = JSON.parse(await readFile(source, "utf8"));
const contract = {
  schemaVersion: catalog.schemaVersion,
  events: catalog.events.map(({ event_type, allowlist, properties }) => ({
    event_type,
    allowlist,
    properties: Object.fromEntries(Object.entries(properties).map(([key, field]) => {
      const { description, sensitive, ...validation } = field;
      return [key, validation];
    })),
  })),
};
const output = `${JSON.stringify(contract, null, 2)}\n`;
for (const target of targets) {
  if (process.argv.includes("--check")) {
    const current = await readFile(target, "utf8").catch(() => "");
    if (current !== output) {
      throw new Error(`Telemetry contract is stale: ${target.pathname}. Run node scripts/generate-telemetry-contract.mjs`);
    }
  } else {
    await writeFile(target, output);
  }
}

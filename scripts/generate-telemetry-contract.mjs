import { readFile, writeFile } from "node:fs/promises";

const source = new URL("../docs/telemetry/event-schemas.json", import.meta.url);
const target = new URL("../uis/backoffice/src/services/telemetry-contract.json", import.meta.url);
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
if (process.argv.includes("--check")) {
  if (await readFile(target, "utf8") !== output) {
    throw new Error("Telemetry contract is stale. Run node scripts/generate-telemetry-contract.mjs");
  }
} else {
  await writeFile(target, output);
}

// Generates src/protocol.gen.ts from src/schema.json, which the backend exports:
//   cd backend && uv run python -m app.web.schema > ../frontend/src/schema.json
import { readFileSync, writeFileSync } from "node:fs";
import { compile } from "json-schema-to-typescript";

const exported = JSON.parse(readFileSync(new URL("../src/schema.json", import.meta.url), "utf8"));

// Merge the three exports into one schema so shared definitions are declared once.
const defs = {};
const roots = {};
for (const [key, schema] of Object.entries(exported)) {
  const { $defs = {}, ...root } = schema;
  Object.assign(defs, $defs);
  roots[key] = root;
}

// Pydantic titles every property ("Text", "Seq"...), which would become one alias type each.
// Only definitions keep their titles.
function dropPropertyTitles(node) {
  if (Array.isArray(node)) return node.forEach(dropPropertyTitles);
  if (!node || typeof node !== "object") return;
  for (const prop of Object.values(node.properties ?? {})) delete prop.title;
  Object.values(node).forEach(dropPropertyTitles);
}
dropPropertyTitles(defs);
dropPropertyTitles(roots);

const schema = {
  title: "Protocol",
  type: "object",
  properties: {
    ClientMessage: { ...roots.client_message, title: "ClientMessage" },
    ServerMessage: { ...roots.server_message, title: "ServerMessage" },
    Payload: { ...roots.payload, title: "Payload" },
    Preview: { ...roots.preview, title: "Preview" },
  },
  $defs: defs,
};

const ts = await compile(schema, "Protocol", {
  bannerComment: "// Generated from src/schema.json by scripts/gen-types.mjs. Do not edit.",
  additionalProperties: false,
  unreachableDefinitions: true,
});
// Drop the generator's "referenced by" boilerplate, the wrapper type, and a duplicate alias.
const clean = ts
  .replace(/\n \*\n \* This interface was referenced by[^\n]*\n \* via the `definition` "\w+"\./g, "")
  .replace(/\/\*\*\n \* This interface was referenced by[^\n]*\n \* via the `definition` "\w+"\.\n \*\/\n/g, "")
  .replace(/export type CallPhase1 = [^;]+;\n/, "")
  .replace(/\nexport interface Protocol \{[^}]+\}\n/, "\n");
writeFileSync(new URL("../src/protocol.gen.ts", import.meta.url), clean);
console.log("wrote src/protocol.gen.ts");

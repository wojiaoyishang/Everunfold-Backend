/* Test-only child HTTP process; never deploy these control endpoints. */
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { ApiKeyClient } from "../../examples/api-key-client.mjs";

const config = JSON.parse(readFileSync(process.argv[2], "utf8"));
let queries = 0, callbacks = 0, delay = 0, mode = "normal";
const client = new ApiKeyClient({ ...config, fetch: async (...args) => {
  queries += 1;
  const response = await fetch(...args);
  // Buffer before the delay so the server result really predates a later revocation.
  const raw = await response.arrayBuffer();
  if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
  return new Response(raw, { status: response.status, headers: response.headers });
} });
createServer(async (req, res) => {
  try {
    const chunks = [];
    let size = 0;
    for await (const chunk of req) { size += chunk.length; if (size > 16384) throw Error("too_large"); chunks.push(chunk); }
    const raw = Buffer.concat(chunks);
    if (req.url === "/health") { res.end("ready"); return; }
    if (req.url === "/stats") { res.end(JSON.stringify({ queries, callbacks })); return; }
    if (req.url === "/control") { const value = JSON.parse(raw); mode = value.mode || "normal"; delay = value.delay || 0; res.end("ok"); return; }
    if (req.url === "/callback") {
      if (mode === "unavailable") { res.writeHead(503).end(); return; }
      const ack = client.callback(raw, req.headers); callbacks += 1;
      if (mode === "badack") { res.writeHead(200).end("{}"); return; }
      res.writeHead(200, ack.headers).end(ack.body); return;
    }
    if (req.url === "/check") {
      const value = JSON.parse(raw);
      const result = await client.check(value.token, { write: value.write });
      res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(result)); return;
    }
    res.writeHead(404).end();
  } catch (error) { res.writeHead(503).end(JSON.stringify({ error: error.message })); }
}).listen(config.port, "127.0.0.1");

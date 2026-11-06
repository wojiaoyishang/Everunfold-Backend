import assert from "node:assert/strict";
import { ApiKeyClient, allows, signedHeaders } from "../../examples/api-key-client.mjs";

const wall = Date.now(), realNow = Date.now;
let elapsed = 0, delay = 0, count = 0;
Date.now = () => wall + elapsed;
Object.defineProperty(performance, "now", { configurable: true, value: () => elapsed });
const config = { appId: "clock-test", audience: "clock-test", querySecret: "dummy-query-secret",
  queryKeyId: "clock-test:query:1", queryUrl: "http://example.invalid/introspect",
  fetch: async (_url, request) => {
    count += 1;
    const checked = wall + elapsed + 110000;
    const value = { active: true, app_id: "clock-test", audience: "clock-test", key_id: "test-key",
      subject: { id: "user-id", display_name: "Test" }, permissions: { "data:read": "own" },
      app_version: 1, user_version: 1, key_version: 1, checked_at: new Date(checked).toISOString(),
      expires_at: new Date(checked + 10000).toISOString(), cache_until: new Date(checked + 10000).toISOString() };
    const body = Buffer.from(JSON.stringify({ success: true, data: value }));
    const proof = signedHeaders(config.querySecret, config.appId, config.queryKeyId, "RESPONSE", config.queryUrl,
      body, request.headers["X-Everunfold-Nonce"]);
    elapsed += delay;
    return new Response(body, { headers: proof });
  } };
try {
  const client = new ApiKeyClient(config);
  const result = await client.check("dummy-token");
  assert.equal(allows(result, "data:read", "user-id"), true);
  assert.equal(allows(result, "data:read", undefined), false);
  result.permissions["data:write"] = "all";
  assert.equal(allows(await client.check("dummy-token"), "data:write", "other"), false);
  assert.equal(count, 1);
  elapsed += 11000;
  await client.check("dummy-token");
  assert.equal(count, 2, "server lease expires despite a slow local wall clock");
  delay = 11000;
  assert.deepEqual(await new ApiKeyClient(config).check("late-expiring-token"), { active: false });
} finally { Date.now = realNow; }

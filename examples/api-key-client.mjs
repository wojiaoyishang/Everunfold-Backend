/* Node 22 reference integration. Configuration/secrets stay on the child server. */
import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

const protocol = "EF-AUTH-HMAC-SHA256-V1";
const digest = (body) => createHash("sha256").update(body).digest("hex");
const sign = (secret, app, kid, method, url, body, timestamp, nonce) =>
  createHmac("sha256", secret).update(
    [protocol, method.toUpperCase(), url, app, kid, timestamp, nonce, digest(body)].join("\n"),
  ).digest("hex");

export function signedHeaders(secret, app, kid, method, url, body, nonce = randomBytes(24).toString("base64url")) {
  const timestamp = String(Math.floor(Date.now() / 1000));
  return {
    "Content-Type": "application/json", "X-Everunfold-App": app,
    "X-Everunfold-Key-Id": kid, "X-Everunfold-Timestamp": timestamp,
    "X-Everunfold-Nonce": nonce,
    "X-Everunfold-Signature": sign(secret, app, kid, method, url, body, timestamp, nonce),
  };
}

export function verifySignature(secret, app, kid, method, url, body, input) {
  const headers = new Headers(input);
  const timestamp = headers.get("x-everunfold-timestamp") || "";
  const nonce = headers.get("x-everunfold-nonce") || "";
  const signature = headers.get("x-everunfold-signature") || "";
  if (headers.get("x-everunfold-app") !== app || headers.get("x-everunfold-key-id") !== kid ||
      !/^\d{1,12}$/.test(timestamp) || Math.abs(Date.now() / 1000 - Number(timestamp)) > 120 ||
      !/^[\w-]{24,80}$/.test(nonce) || !/^[a-f0-9]{64}$/.test(signature) ||
      !timingSafeEqual(Buffer.from(signature, "hex"),
        Buffer.from(sign(secret, app, kid, method, url, body, timestamp, nonce), "hex"))) {
    throw new Error("invalid_signature");
  }
  return nonce;
}

export class ApiKeyClient {
  #cache = new Map();
  #floor = 0;
  #generation = 0;
  #nonces = new Map();
  constructor(config) {
    this.config = { cacheSeconds: 30, ...config };
    if (!config.appId || !config.audience || !config.queryUrl || !config.querySecret) {
      throw new Error("invalid_application_config");
    }
  }
  async check(token, { write = false } = {}) {
    if (typeof token !== "string" || token.length > 160) return { active: false };
    const key = digest(token), start = performance.now();
    const cached = this.#cache.get(key);
    if (!write && cached && start < cached.deadline && cached.value.app_version >= this.#floor) return structuredClone(cached.value);
    this.#cache.delete(key);
    const generation = this.#generation, c = this.config;
    const body = Buffer.from(JSON.stringify({ token, mode: write ? "write" : "read" }));
    const headers = signedHeaders(c.querySecret, c.appId, c.queryKeyId, "POST", c.queryUrl, body);
    const response = await (c.fetch || fetch)(c.queryUrl, {
      method: "POST", headers, body, redirect: "error", signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error("authorization_unavailable");
    const chunks = [];
    let size = 0;
    for await (const chunk of response.body) {
      size += chunk.length;
      if (size > 65536) throw new Error("response_too_large");
      chunks.push(chunk);
    }
    const raw = Buffer.concat(chunks);
    if (verifySignature(c.querySecret, c.appId, c.queryKeyId, "RESPONSE", c.queryUrl, raw, response.headers) !==
        headers["X-Everunfold-Nonce"]) throw new Error("invalid_response_nonce");
    const envelope = JSON.parse(raw.toString()), value = envelope.data;
    if (envelope.success !== true || !value || typeof value.active !== "boolean") throw new Error("invalid_response");
    if (!value.active) return { active: false };
    if (value.app_id !== c.appId || value.audience !== c.audience || !value.subject?.id ||
        !value.permissions || !Number.isSafeInteger(value.app_version) ||
        !Number.isSafeInteger(value.user_version) || !Number.isSafeInteger(value.key_version)) throw new Error("invalid_response");
    // A response started before a callback cannot resurrect the invalidated authorization.
    if (generation !== this.#generation || value.app_version < this.#floor) throw new Error("authorization_changed_retry");
    if (value.app_version > this.#floor) { this.#floor = value.app_version; this.#cache.clear(); }
    const checked = Date.parse(value.checked_at), expiry = Date.parse(value.expires_at);
    const serverDeadline = Math.min(Date.parse(value.cache_until), expiry);
    // A clock offset must not extend either the server lease or the key lifetime.
    const lease = Math.min(Math.min(30, Math.max(0, c.cacheSeconds)) * 1000, serverDeadline - checked);
    const deadline = Math.min(start + lease, performance.now() + serverDeadline - Date.now());
    if (!Number.isFinite(deadline)) throw new Error("invalid_response");
    if (expiry <= Date.now() || start + expiry - checked <= performance.now()) return { active: false };
    if (!write && deadline <= performance.now()) throw new Error("authorization_expired_retry");
    if (!write && deadline > performance.now()) {
      if (this.#cache.size >= 1000) this.#cache.delete(this.#cache.keys().next().value);
      this.#cache.set(key, { value: structuredClone(value), deadline });
    }
    return structuredClone(value);
  }
  callback(raw, headers) {
    const c = this.config;
    if (!Buffer.isBuffer(raw) || raw.length > 16384) throw new Error("invalid_callback");
    const nonce = verifySignature(c.callbackSecret, c.appId, c.callbackKeyId, "POST", c.callbackUrl, raw, headers);
    const now = Date.now();
    for (const [key, expiry] of this.#nonces) if (expiry <= now) this.#nonces.delete(key);
    if (this.#nonces.has(nonce)) throw new Error("replayed_callback");
    if (this.#nonces.size >= 10000) throw new Error("callback_busy");
    this.#nonces.set(nonce, now + 240000);
    const event = JSON.parse(raw.toString());
    if (event.protocol !== "everunfold.api-key.v1" || event.app_id !== c.appId || event.audience !== c.audience ||
        !/^[a-f0-9-]{36}$/.test(event.event_id) || !Number.isSafeInteger(event.app_version) || event.app_version < 1) throw new Error("invalid_callback");
    if (event.type !== "application.test") {
      this.#floor = Math.max(this.#floor, event.app_version);
      this.#generation += 1;
      this.#cache.clear();
    }
    const body = Buffer.from(JSON.stringify({ event_id: event.event_id, app_id: c.appId,
      app_version: Math.max(this.#floor, event.app_version), received: true, applied: true }));
    return { body, headers: signedHeaders(c.callbackSecret, c.appId, c.callbackKeyId, "RESPONSE", c.callbackUrl, body, nonce) };
  }
}

export function allows(identity, capability, ownerId) {
  const scope = identity.active && identity.permissions?.[capability];
  return scope === "all" || (scope === "own" && typeof ownerId === "string" && ownerId === identity.subject?.id);
}

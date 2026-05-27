/**
 * Origin API — k6 Smoke Test
 *
 * Usage:
 *   k6 run tests/load/k6_smoke.js
 *   k6 run --vus 10 --duration 30s tests/load/k6_smoke.js
 *
 * Install k6: https://k6.io/docs/get-started/installation/
 *
 * Thresholds (smoke):
 *   - 95% of requests complete under 500ms
 *   - Error rate below 1%
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const errorRate = new Rate("errors");
const thinkLatency = new Trend("think_latency_ms", true);

export const options = {
  stages: [
    { duration: "10s", target: 3 },   // ramp up
    { duration: "20s", target: 3 },   // steady
    { duration: "5s",  target: 0 },   // ramp down
  ],
  thresholds: {
    http_req_duration: ["p(95)<500"],
    errors: ["rate<0.01"],
  },
};

const BASE = __ENV.ORIGIN_URL || "http://localhost:9001";

export function setup() {
  // Register + login to get an auth token
  const reg = http.post(
    `${BASE}/auth/register`,
    JSON.stringify({ username: `k6_load_${Date.now()}`, password: "K6LoadTest_secure!" }),
    { headers: { "Content-Type": "application/json" } }
  );
  const username = reg.json("username");

  const login = http.post(
    `${BASE}/auth/login`,
    JSON.stringify({ username, password: "K6LoadTest_secure!" }),
    { headers: { "Content-Type": "application/json" } }
  );
  return { token: login.json("access_token") };
}

export default function (data) {
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${data.token}`,
  };

  // ── Health check ──────────────────────────────────────────────
  const health = http.get(`${BASE}/health`);
  check(health, { "health 200": (r) => r.status === 200 });
  errorRate.add(health.status !== 200);

  // ── Memory search ─────────────────────────────────────────────
  const mem = http.get(`${BASE}/mind/memory/search?query=test&top_k=5`, { headers });
  check(mem, { "memory search 200": (r) => r.status === 200 });
  errorRate.add(mem.status !== 200);

  // ── Think (lightweight chitchat) ──────────────────────────────
  const t0 = Date.now();
  const think = http.post(
    `${BASE}/mind/think`,
    JSON.stringify({ message: "¿Qué hora es?" }),
    { headers, timeout: "15s" }
  );
  thinkLatency.add(Date.now() - t0);
  check(think, {
    "think 200": (r) => r.status === 200,
    "think has answer": (r) => r.json("final_answer") !== undefined,
  });
  errorRate.add(think.status !== 200);

  sleep(1);
}

export function teardown(data) {
  // Clean up test account
  if (data.token) {
    http.del(`${BASE}/auth/account`, null, {
      headers: { Authorization: `Bearer ${data.token}` },
    });
  }
}

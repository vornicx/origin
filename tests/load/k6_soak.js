/**
 * Origin API — k6 Soak Test (sustained load, memory leak detection)
 *
 * Usage:
 *   k6 run tests/load/k6_soak.js
 *
 * Runs at low VU count for a longer duration to detect:
 *   - Memory leaks (response times drifting up over time)
 *   - Connection pool exhaustion
 *   - Background task accumulation
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate } from "k6/metrics";

const errorRate = new Rate("errors");

export const options = {
  stages: [
    { duration: "1m",  target: 2 },
    { duration: "10m", target: 2 },
    { duration: "1m",  target: 0 },
  ],
  thresholds: {
    http_req_duration: ["p(95)<1000", "p(99)<3000"],
    errors: ["rate<0.02"],
  },
};

const BASE = __ENV.ORIGIN_URL || "http://localhost:9001";

export function setup() {
  const reg = http.post(
    `${BASE}/auth/register`,
    JSON.stringify({ username: `k6_soak_${Date.now()}`, password: "K6SoakTest_secure!" }),
    { headers: { "Content-Type": "application/json" } }
  );
  const login = http.post(
    `${BASE}/auth/login`,
    JSON.stringify({ username: reg.json("username"), password: "K6SoakTest_secure!" }),
    { headers: { "Content-Type": "application/json" } }
  );
  return { token: login.json("access_token") };
}

export default function (data) {
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${data.token}`,
  };

  const health = http.get(`${BASE}/health`);
  check(health, { "health 200": (r) => r.status === 200 });
  errorRate.add(health.status !== 200);

  const dashboard = http.get(`${BASE}/dashboard/stats`, { headers });
  check(dashboard, { "dashboard 200": (r) => r.status === 200 });
  errorRate.add(dashboard.status !== 200);

  sleep(2);
}

export function teardown(data) {
  if (data.token) {
    http.del(`${BASE}/auth/account`, null, {
      headers: { Authorization: `Bearer ${data.token}` },
    });
  }
}

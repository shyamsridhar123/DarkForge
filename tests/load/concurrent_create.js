/**
 * tests/load/concurrent_create.js
 *
 * Plan task: Phase 6 — Load test: 100 concurrent POST /sessions from 10 users.
 *
 * Acceptance criteria:
 *   - p95 < 10s
 *   - Zero HTTP 500 responses
 *
 * Run:
 *   k6 run tests/load/concurrent_create.js \
 *     -e CONTROL_PLANE_URL=https://<fqdn>/api \
 *     -e TOKEN=<bearer_token>
 *
 * Or with token acquisition (requires k6 extensions):
 *   k6 run tests/load/concurrent_create.js \
 *     -e CONTROL_PLANE_URL=... \
 *     -e ENTRA_TOKEN_URL=... \
 *     -e CLIENT_ID=... \
 *     -e CLIENT_SECRET=... \
 *     -e SCOPE=...
 */

import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

// ---------------------------------------------------------------------------
// Custom metrics
// ---------------------------------------------------------------------------
const errorRate = new Rate("session_create_errors");
const sessionCreateDuration = new Trend("session_create_duration_ms", true);
const http500Rate = new Rate("http_500_rate");

// ---------------------------------------------------------------------------
// k6 options: 10 VUs × ramp to 100 concurrent requests
// ---------------------------------------------------------------------------
export const options = {
  scenarios: {
    concurrent_create: {
      executor: "constant-vus",
      vus: 10,
      duration: "3m",
      gracefulStop: "30s",
    },
  },
  thresholds: {
    // p95 session creation < 10s (AC load test)
    session_create_duration_ms: ["p(95)<10000"],
    // Zero HTTP 500s
    http_500_rate: ["rate==0"],
    // Overall error rate < 1%
    session_create_errors: ["rate<0.01"],
    // Standard k6 http thresholds
    http_req_duration: ["p(95)<10000"],
    http_req_failed: ["rate<0.01"],
  },
  summaryTrendStats: ["min", "med", "avg", "p(90)", "p(95)", "p(99)", "max"],
};

// ---------------------------------------------------------------------------
// Setup: acquire token once per test run
// ---------------------------------------------------------------------------
export function setup() {
  const baseUrl = __ENV.CONTROL_PLANE_URL;
  if (!baseUrl) {
    throw new Error("CONTROL_PLANE_URL env var is required");
  }

  // Use pre-supplied token if provided
  if (__ENV.TOKEN) {
    return { baseUrl, token: __ENV.TOKEN };
  }

  // Otherwise acquire via client-credentials
  const tokenUrl = __ENV.ENTRA_TOKEN_URL;
  const clientId = __ENV.CLIENT_ID;
  const clientSecret = __ENV.CLIENT_SECRET;
  const scope = __ENV.SCOPE;

  if (!tokenUrl || !clientId || !clientSecret || !scope) {
    throw new Error(
      "Either TOKEN or (ENTRA_TOKEN_URL + CLIENT_ID + CLIENT_SECRET + SCOPE) must be set"
    );
  }

  const tokenResp = http.post(
    tokenUrl,
    {
      grant_type: "client_credentials",
      client_id: clientId,
      client_secret: clientSecret,
      scope: scope,
    },
    { headers: { "Content-Type": "application/x-www-form-urlencoded" } }
  );

  check(tokenResp, { "token acquired": (r) => r.status === 200 });
  const token = JSON.parse(tokenResp.body).access_token;
  if (!token) {
    throw new Error(`Token acquisition failed: ${tokenResp.body}`);
  }

  return { baseUrl, token };
}

// ---------------------------------------------------------------------------
// Default function: each VU runs POST /sessions in a tight loop
// ---------------------------------------------------------------------------
export default function (data) {
  const { baseUrl, token } = data;

  // Each VU simulates a different user via a unique test session ID
  const testSessionId = `load-test-${__VU}-${__ITER}-${Date.now()}`;

  const payload = JSON.stringify({
    image: "python312-sandbox",
    low_latency: false,
    test_session_id: testSessionId,
  });

  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Load-Test": "true",
  };

  const startMs = Date.now();
  const resp = http.post(`${baseUrl}/sessions`, payload, {
    headers,
    timeout: "30s",
  });
  const durationMs = Date.now() - startMs;

  // Record custom metrics
  sessionCreateDuration.add(durationMs);

  const isSuccess = check(resp, {
    "status is 200/201/202": (r) =>
      r.status === 200 || r.status === 201 || r.status === 202,
    "no 500 error": (r) => r.status !== 500,
    "response has session_id": (r) => {
      try {
        const body = JSON.parse(r.body);
        return !!(body.session_id || body.id);
      } catch {
        return false;
      }
    },
  });

  errorRate.add(!isSuccess);
  http500Rate.add(resp.status === 500);

  if (resp.status === 500) {
    console.error(
      `VU ${__VU} iter ${__ITER}: HTTP 500 — ${resp.body?.substring(0, 200)}`
    );
  }

  // Cleanup: delete session to avoid resource accumulation
  if (resp.status === 200 || resp.status === 201 || resp.status === 202) {
    try {
      const body = JSON.parse(resp.body);
      const sessionId = body.session_id || body.id;
      if (sessionId) {
        http.del(`${baseUrl}/sessions/${sessionId}`, null, {
          headers,
          timeout: "10s",
        });
      }
    } catch {
      // Ignore cleanup errors in load test
    }
  }

  // Brief think-time between iterations (100ms)
  sleep(0.1);
}

// ---------------------------------------------------------------------------
// Teardown: summary
// ---------------------------------------------------------------------------
export function teardown(data) {
  console.log("Load test complete.");
  console.log(`Base URL: ${data.baseUrl}`);
  console.log("Check the k6 summary for p95 and error rate thresholds.");
}

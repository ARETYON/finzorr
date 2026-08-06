// finzorr load harness (k6) — non-LLM paths only.
// Usage: APP_ENV=dev DEV_FAKE_AUTH=true backend running, then:
//   k6 run load/k6-chat.js
// Thresholds: p95 < 250ms on non-LLM REST, zero errors.
// NOTE: k6's HTTP cookie jar does NOT apply to ws.connect — the session
// cookie is passed explicitly in the WS headers below.

import http from 'k6/http';
import ws from 'k6/ws';
import { check, sleep } from 'k6';

export const options = {
  scenarios: {
    rest: { executor: 'constant-vus', vus: 10, duration: '3m', exec: 'rest' },
    wsPing: { executor: 'constant-vus', vus: 5, duration: '3m', exec: 'wsPing' },
  },
  thresholds: {
    http_req_failed: ['rate==0'],
    'http_req_duration{scenario:rest}': ['p(95)<250'],
  },
};

const BASE = __ENV.BASE_URL || 'http://localhost:8000';

function login() {
  const res = http.post(`${BASE}/api/v1/auth/dev-login`);
  check(res, { 'login 200': (r) => r.status === 200 });
  return res.cookies['finzorr_session'][0].value;
}

export function rest() {
  const cookie = login();
  const params = { headers: { Cookie: `finzorr_session=${cookie}` } };
  check(http.get(`${BASE}/healthz`), { healthz: (r) => r.status === 200 });
  const created = http.post(`${BASE}/api/v1/chat/sessions`, null, params);
  check(created, { 'create 201': (r) => r.status === 201 });
  const sid = created.json('id');
  check(http.get(`${BASE}/api/v1/chat/sessions?limit=50`, params), {
    'list 200': (r) => r.status === 200,
  });
  check(http.get(`${BASE}/api/v1/chat/search?q=test`, params), {
    'search 200': (r) => r.status === 200,
  });
  check(http.del(`${BASE}/api/v1/chat/sessions/${sid}`, null, params), {
    'delete 204': (r) => r.status === 204,
  });
  sleep(0.2);
}

export function wsPing() {
  const cookie = login();
  ws.connect(
    `${BASE.replace('http', 'ws')}/ws/chat`,
    { headers: { Cookie: `finzorr_session=${cookie}` } },
    (socket) => {
      socket.on('open', () => socket.send(JSON.stringify({ type: 'ping' })));
      socket.on('message', (msg) => {
        check(JSON.parse(msg), { pong: (f) => f.type === 'pong' });
        socket.close();
      });
      socket.setTimeout(() => socket.close(), 5000);
    },
  );
  sleep(0.5);
}

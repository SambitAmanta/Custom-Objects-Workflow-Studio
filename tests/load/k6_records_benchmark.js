import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';

// Custom performance metrics
const createRecordLatency = new Trend('create_record_latency_ms');
const listRecordsLatency = new Trend('list_records_latency_ms');
const updateRecordLatency = new Trend('update_record_latency_ms');
const conflictRate = new Rate('concurrency_conflict_rate');
const errorRate = new Rate('http_error_rate');
const recordsCreated = new Counter('total_records_created');

export const options = {
  scenarios: {
    benchmark_load: {
      executor: 'shared-iterations',
      vus: 50,
      iterations: 3334,
      maxDuration: '1m',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<150', 'p(50)<50'], // 95% of requests under 150ms, 50% under 50ms
    http_error_rate: ['rate<0.01'],               // Less than 1% errors
  },
};

const BASE_URL = __ENV.API_BASE_URL || 'http://localhost:8000/api/v1';
const TENANT_ID = __ENV.BENCH_TENANT_ID || '11111111-1111-1111-1111-111111111111';
const AUTH_TOKEN = __ENV.BENCH_JWT_TOKEN || 'test-bench-token';

export default function () {
  const headers = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${AUTH_TOKEN}`,
    'X-Tenant-Id': TENANT_ID,
  };

  // 1. Create a dynamic record (with single-transaction idempotency key)
  const randomAmount = Math.floor(Math.random() * 5000) + 100;
  const createPayload = JSON.stringify({
    title: `Benchmark Invoice #${__VU}-${__ITER}`,
    amount: randomAmount,
    department: 'Engineering',
  });

  const createRes = http.post(`${BASE_URL}/o/invoice/`, createPayload, {
    headers: Object.assign({}, headers, {
      'Idempotency-Key': `bench-key-${__VU}-${__ITER}`,
    }),
  });

  createRecordLatency.add(createRes.timings.duration);
  const createSuccess = check(createRes, {
    'record created status is 201 or 200': (r) => r.status === 201 || r.status === 200,
    'record has valid id': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body && body.id !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  if (!createSuccess) {
    errorRate.add(1);
    return;
  }

  errorRate.add(0);
  recordsCreated.add(1);

  const createdBody = JSON.parse(createRes.body);
  const recordId = createdBody.id;
  const recordVersion = createdBody.version || 1;

  sleep(0.1);

  // 2. Query/List records with filter and cursor
  const listRes = http.get(`${BASE_URL}/o/invoice/?data.amount[gte]=100&limit=10`, { headers });
  listRecordsLatency.add(listRes.timings.duration);
  check(listRes, {
    'list records status is 200': (r) => r.status === 200,
  });

  // 3. Update record with ETag optimistic concurrency control
  const patchPayload = JSON.stringify({
    title: `Updated Benchmark Invoice #${__VU}-${__ITER}`,
  });

  const patchRes = http.patch(
    `${BASE_URL}/o/invoice/${recordId}/`,
    patchPayload,
    {
      headers: Object.assign({}, headers, {
        'If-Match': `W/"${recordVersion}"`,
      }),
    }
  );

  updateRecordLatency.add(patchRes.timings.duration);
  if (patchRes.status === 412) {
    conflictRate.add(1);
  } else {
    check(patchRes, {
      'patch record status is 200': (r) => r.status === 200,
    });
    conflictRate.add(0);
  }

  sleep(0.2);
}

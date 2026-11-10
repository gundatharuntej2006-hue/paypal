import { normalizeGatewayEvent, normalizeGatewayEvents } from './events';
import { loadBenchmarkReport } from '../benchmark/report';
import type { MandateDraft } from '../lib/mandate';
import type { Mandate } from '../contracts/types';
import type { ApprovalPayment, DemoScenario, Payment } from './models';
import { stub } from './stub';

export const useStub = import.meta.env.VITE_USE_STUB !== 'false';
const gateway = (import.meta.env.VITE_GATEWAY_URL || 'http://localhost:8080').replace(/\/+$/, '');

export class GatewayError extends Error {
  constructor(readonly status: number) {
    super('Gateway returned HTTP ' + status + '.');
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(gateway + '/v1' + path, options);
  if (!response.ok) throw new GatewayError(response.status);
  return response.json() as Promise<T>;
}

async function review(id: string, action: 'approve' | 'reject', detail?: string) {
  const body = detail ? JSON.stringify(action === 'approve' ? { paypal_order_id: detail } : { reason: detail }) : undefined;
  const response = await fetch(gateway + '/v1/payments/' + encodeURIComponent(id) + '/' + action, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body,
  });
  if (!response.ok) throw new GatewayError(response.status);
}

export async function checkGatewayHealth(signal: AbortSignal) {
  const response = await fetch(gateway + '/health', {
    cache: 'no-store', signal: AbortSignal.any([signal, AbortSignal.timeout(8000)]),
  });
  if (!response.ok || response.headers.get('content-type')?.includes('text/html')) {
    throw new GatewayError(response.status);
  }
}

const demoRunUrl = import.meta.env.VITE_DEMO_RUN_URL?.trim() || gateway + '/v1/demo/run';

async function runDemoScenario(scenario: DemoScenario, mandateId?: string) {
  const response = await fetch(demoRunUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scenario, mandate_id: mandateId, idempotency_key: crypto.randomUUID() }),
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new GatewayError(response.status);
}

const namedEvents = [
  'intent.received',
  'mandate.verified',
  'mandate.failed',
  'scan.completed',
  'judge.completed',
  'risk.scored',
  'decision.made',
  'human.approved',
  'human.rejected',
  'paypal.order_created',
  'paypal.authorized',
  'paypal.captured',
  'paypal.voided',
  'paypal.refunded',
  'webhook.received',
  'reconciliation.mismatch',
  'reconciliation.completed',
  'payout.created',
  'payout.updated',
  'invoice.paid',
  'payment.step_up_token_issued',
  'payment.step_up_token_consumed',
  'paypal.capture_failed',
];

const live: typeof stub = {
  runDemoScenario,
  listMandates: () => request<Mandate[]>('/mandates'),
  createMandate: (mandate) => request<Mandate>('/mandates', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(mandate),
  }),
  createMandateOrder: (draft: MandateDraft) => request<{ id: string }>(
    '/mandates/' + encodeURIComponent(draft.mandate_id) + '/order',
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) },
  ),
  listPayments: () => request<Payment[]>('/payments'),
  async getPaymentEvents(id) {
    const result = await request<{ timeline: unknown }>('/payments/' + encodeURIComponent(id));
    return normalizeGatewayEvents(result.timeline);
  },
  async listApprovals() {
    try {
      return await request<ApprovalPayment[]>('/approvals');
    } catch (error) {
      if (error instanceof GatewayError && error.status === 404) return [];
      throw error;
    }
  },
  approve: (id, orderId) => review(id, 'approve', orderId),
  reject: (id, reason) => review(id, 'reject', reason),
  subscribeEvents(onEvent, paymentId, onConnection) {
    const query = paymentId ? '?' + new URLSearchParams({ payment_id: paymentId }) : '';
    // Keep the same EventSource across network failures so the browser
    // reconnects and sends Last-Event-ID when the gateway supplies SSE IDs.
    const source = new EventSource(gateway + '/v1/events' + query);
    source.onopen = () => onConnection?.(true);
    source.onerror = () => onConnection?.(false);
    const receive = (message: MessageEvent<string>) => {
      let event: ReturnType<typeof normalizeGatewayEvent>;
      try {
        event = normalizeGatewayEvent(JSON.parse(message.data));
      } catch {
        return;
      }
      if (event?.id && (!paymentId || event.payment_id === paymentId)) onEvent(event);
    };
    source.onmessage = receive;
    for (const name of namedEvents) source.addEventListener(name, receive);
    return () => source.close();
  },
};

const benchmarkMode = import.meta.env.VITE_BENCHMARK_SOURCE;
const benchmarkSource = benchmarkMode === 'static' || benchmarkMode === 'gateway' ? benchmarkMode : 'auto';

export const client = {
  ...(useStub ? stub : live),
  getLedgerEvents: async () => useStub ? [] : normalizeGatewayEvents(await request<unknown>('/ledger/events')),
  getBenchmarkReport: () => loadBenchmarkReport({
    source: benchmarkSource,
    staticUrl: import.meta.env.VITE_BENCHMARK_REPORT_URL || '/benchmark-report.json',
    gatewayUrl: benchmarkSource === 'gateway' || import.meta.env.VITE_GATEWAY_URL || !useStub ?
      gateway + '/v1/benchmark/report' : undefined,
  }),
};

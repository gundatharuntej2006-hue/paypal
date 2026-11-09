import type { MandateDraft } from '../lib/mandate';
import type { EventEnvelope, Mandate } from '../contracts/types';
import type { ApprovalPayment, BenchmarkReport, Payment } from './models';
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

const live: typeof stub = {
  listMandates: () => request<Mandate[]>('/mandates'),
  createMandate: (mandate) => request<Mandate>('/mandates', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(mandate),
  }),
  createMandateOrder: (draft: MandateDraft) => request<{ id: string }>(
    '/mandates/' + encodeURIComponent(draft.mandate_id) + '/order',
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) },
  ),
  listPayments: () => request<Payment[]>('/payments'),
  getPaymentEvents: (id) => request<EventEnvelope[]>('/payments/' + encodeURIComponent(id) + '/events'),
  listApprovals: () => request<ApprovalPayment[]>('/approvals'),
  approve: (id, orderId) => review(id, 'approve', orderId),
  reject: (id, reason) => review(id, 'reject', reason),
  getBenchmarkReport: () => request<BenchmarkReport>('/benchmark'),
  subscribeEvents(onEvent, paymentId, onConnection) {
    const query = paymentId ? '?' + new URLSearchParams({ payment_id: paymentId }) : '';
    // Keep the same EventSource across network failures so the browser
    // reconnects and sends Last-Event-ID when the gateway supplies SSE IDs.
    const source = new EventSource(gateway + '/v1/events' + query);
    source.onopen = () => onConnection?.(true);
    source.onerror = () => onConnection?.(false);
    source.onmessage = (message) => {
      let event: EventEnvelope;
      try {
        event = JSON.parse(message.data);
      } catch {
        return;
      }
      if (event?.id && (!paymentId || event.payment_id === paymentId)) onEvent(event);
    };
    return () => source.close();
  },
};

export const client = useStub ? stub : live;

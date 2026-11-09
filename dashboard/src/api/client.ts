import type { MandateDraft } from '../lib/mandate';
import type { EventEnvelope, Mandate } from '../contracts/types';
import type { BenchmarkReport, Payment } from './models';
import { stub } from './stub';

export const useStub = import.meta.env.VITE_USE_STUB !== 'false';
const gateway = (import.meta.env.VITE_GATEWAY_URL || 'http://localhost:8080').replace(/\/+$/, '');

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(gateway + '/v1' + path, options);
  if (!response.ok) throw new Error('Gateway returned HTTP ' + response.status + '.');
  return response.json() as Promise<T>;
}

async function review(id: string, action: 'approve' | 'reject') {
  const response = await fetch(gateway + '/v1/approvals/' + encodeURIComponent(id) + '/' + action, { method: 'POST' });
  if (!response.ok) throw new Error('Gateway returned HTTP ' + response.status + '.');
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
  listApprovals: () => request<Payment[]>('/approvals'),
  approve: (id) => review(id, 'approve'),
  reject: (id) => review(id, 'reject'),
  getBenchmarkReport: () => request<BenchmarkReport>('/benchmark'),
  subscribeEvents(onEvent, paymentId, onConnection) {
    const query = paymentId ? '?' + new URLSearchParams({ payment_id: paymentId }) : '';
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

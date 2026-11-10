import type { EventEnvelope } from '../contracts/types';

export function normalizeGatewayEvent(value: unknown): EventEnvelope | undefined {
  if (!value || typeof value !== 'object') return;
  const event = value as Record<string, unknown>;
  const ts = event.created_at ?? event.ts;
  if (typeof event.id !== 'string' || !event.id || typeof event.type !== 'string' ||
      typeof ts !== 'string' || !Number.isFinite(Date.parse(ts))) return;
  const data = event.data ?? event.payload;
  const payload = data && typeof data === 'object' && !Array.isArray(data) ? { ...data } as Record<string, unknown> : {};
  delete payload.step_up_token;
  if (typeof payload.id === 'string') {
    if (event.type === 'paypal.order_created') payload.order_id ??= payload.id;
    if (event.type === 'paypal.captured') payload.capture_id ??= payload.id;
    if (event.type === 'paypal.refunded') payload.refund_id ??= payload.id;
  }
  return {
    id: event.id, type: event.type as EventEnvelope['type'], ts, payload,
    payment_id: typeof event.payment_id === 'string' ? event.payment_id : '',
    mandate_id: typeof event.mandate_id === 'string' ? event.mandate_id : '',
  };
}

export function normalizeGatewayEvents(value: unknown): EventEnvelope[] {
  if (value == null) return [];
  if (!Array.isArray(value)) throw new Error('Gateway returned an invalid event list.');
  return value.flatMap((entry) => {
    const event = normalizeGatewayEvent(entry);
    return event ? [event] : [];
  });
}

import { expect, it } from 'vitest';
import { normalizeGatewayEvents } from './events';

it('normalizes Go ledger events and drops step-up tokens from displayed payloads', () => {
  const events = normalizeGatewayEvents([
    { id: 'evt_1', type: 'paypal.captured', payment_id: 'pay_1', created_at: '2026-11-10T00:00:00Z', data: { id: 'CAP-1' } },
    { id: 'evt_2', type: 'payment.step_up_token_issued', created_at: '2026-11-10T00:00:01Z', data: { step_up_token: 'fixture-only' } },
  ]);
  expect(events[0]).toMatchObject({ ts: '2026-11-10T00:00:00Z', payment_id: 'pay_1', payload: { capture_id: 'CAP-1' } });
  expect(events[1]).toMatchObject({ payment_id: '', payload: {} });
  expect(events[1].payload).not.toHaveProperty('step_up_token');
  expect(normalizeGatewayEvents(null)).toEqual([]);
});

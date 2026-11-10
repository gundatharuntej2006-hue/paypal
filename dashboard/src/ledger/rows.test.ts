import { describe, expect, it } from 'vitest';
import type { EventEnvelope } from '../contracts/types';
import { appendEvent, MAX_EVENTS, projectLedger } from './rows';

function event(id: string, time: number, type: EventEnvelope['type'], payload: EventEnvelope['payload'] = {}, payment_id = 'pay_1'): EventEnvelope {
  return { id, ts: new Date(time).toISOString(), payment_id, mandate_id: 'mdt_1', type, payload };
}

describe('Live Ledger event reducer', () => {
  it('deduplicates reconnect replays, keeps newest first and bounds retention', () => {
    const latest = event('latest', MAX_EVENTS + 1, 'decision.made');
    const history = Array.from({ length: MAX_EVENTS }, (_, index) => event(String(index), MAX_EVENTS - index, 'intent.received'));
    const retained = appendEvent(history, latest);
    expect(retained).toHaveLength(MAX_EVENTS);
    expect(retained[0]).toBe(latest);
    expect(retained.at(-1)?.id).toBe(String(MAX_EVENTS - 2));
    expect(appendEvent(retained, latest)).toBe(retained);
    expect(appendEvent(retained, event('too-old', 0, 'intent.received'))).toBe(retained);
    const middle = event('middle', 100, 'risk.scored');
    expect(appendEvent(retained, middle).findIndex((item) => item.id === 'middle')).toBe(MAX_EVENTS - 99);
  });

  it('joins out-of-order checks, order IDs and the latest decision into one row per payment', () => {
    const events = [
      event('capture', 60, 'paypal.captured', { capture_id: 'CAP-1' }),
      event('intent', 10, 'intent.received', { merchant_id: 'merchant_a', amount: '24.50', currency: 'USD' }),
      event('decision', 50, 'decision.made', { decision: 'allow', checks: { intent: 'pass', risk: 'pass' }, paypal: { order_id: 'ORDER-1' } }),
      event('review', 40, 'decision.made', { decision: 'step_up' }),
      event('judge', 30, 'judge.completed', { aligned: true, rationale: 'Matches the mandate.' }),
      event('risk', 35, 'risk.scored', { score: 0.17, factors: ['First purchase'] }),
      event('other', 80, 'decision.made', { decision: 'block' }, 'pay_2'),
      event('audit', 90, 'webhook.received', {}, ''),
    ].reduce<EventEnvelope[]>(appendEvent, []);
    const projected = projectLedger(events);
    expect(projected.rows[0].id).toBe('audit');
    expect(projected.summaries.map((row) => row.id)).toEqual(['pay_2', 'pay_1']);
    expect(projected.counts).toEqual({ total: 2, allow: 1, step_up: 0, block: 1 });
    expect(projected.payments.get('pay_1')).toMatchObject({
      decision: 'allow', merchant: 'merchant_a', amount: '24.50', risk: 0.17, eventId: 'capture',
      checks: { judge: { status: 'pass', rationale: 'Matches the mandate.' }, risk: { status: 'pass', rationale: 'First purchase' } },
      paypal: { order_id: 'ORDER-1', capture_id: 'CAP-1' },
    });
    expect(projected.rows.find((row) => row.id === 'review')?.decision).toBe('step_up');
  });
});

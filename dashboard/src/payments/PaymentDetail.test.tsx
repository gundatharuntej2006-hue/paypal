import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import type { EventEnvelope } from '../contracts/types';
import { mergePaymentEvents, PaymentTimeline } from './PaymentDetail';

function event(id: string, type: EventEnvelope['type'], ts: string): EventEnvelope {
  return { id, type, ts, mandate_id: 'mdt_1', payment_id: 'pay_1', payload: {} };
}

it('renders stored events in their recorded order, then appends unique live events', () => {
  const history = [
    event('first', 'intent.received', '2026-11-10T10:00:00Z'),
    event('second', 'decision.made', '2026-11-10T10:00:03Z'),
    event('third', 'human.approved', '2026-11-10T10:00:02Z'),
    event('fourth', 'paypal.captured', '2026-11-10T10:00:03Z'),
  ];
  const live = [history[2], event('fifth', 'webhook.received', '2026-11-10T10:00:04Z')];
  const html = renderToStaticMarkup(<PaymentTimeline events={mergePaymentEvents(history, live)} />);
  expect([...html.matchAll(/data-event-id="([^"]+)"/g)].map((match) => match[1]))
    .toEqual(['first', 'second', 'third', 'fourth', 'fifth']);
  expect(html).toContain('Raw JSON payload');
});

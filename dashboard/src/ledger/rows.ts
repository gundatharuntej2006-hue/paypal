import type { Decision, EventEnvelope } from '../contracts/types';

export const MAX_EVENTS = 5000;
export const decisions = ['allow', 'step_up', 'block'] as const;
export const decisionLabels: Record<Decision, string> = { allow: 'Allow', step_up: 'Step-up', block: 'Block' };

export type Layer = 'mandate' | 'injection' | 'judge' | 'risk';

interface Verdict {
  status?: string;
  rationale?: string;
}

interface PaymentFacts {
  paymentId: string;
  merchant?: string;
  amount?: string;
  currency?: string;
  decision?: Decision;
  risk?: number;
  reasons?: string[];
  humanReview?: string;
  checks: Partial<Record<Layer, Verdict>>;
  paypal: { order_id?: string; authorization_id?: string; capture_id?: string };
}

export interface LedgerRow extends PaymentFacts {
  id: string;
  eventId: string;
  time: number;
  type: string;
}

export function appendEvent(events: EventEnvelope[], event: EventEnvelope): EventEnvelope[] {
  if (events.some((item) => item.id === event.id)) return events;
  const time = Date.parse(event.ts);
  if (!Number.isFinite(time)) return events;
  const index = events.findIndex((item) => Date.parse(item.ts) <= time);
  if (index === -1) return events.length >= MAX_EVENTS ? events : [...events, event];
  return [...events.slice(0, index), event, ...events.slice(index, MAX_EVENTS - 1)];
}

const asText = (value: unknown) => typeof value === 'string' && value.length > 0 ? value : undefined;
const asNumber = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : undefined;
const asObject = (value: unknown) => value && typeof value === 'object' ? value as Record<string, unknown> : {};
const asTexts = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : undefined;

function updateFacts(previous: PaymentFacts | undefined, event: EventEnvelope): PaymentFacts {
  const payload = asObject(event.payload);
  const result = asObject(payload.checks);
  const paypal = asObject(payload.paypal);
  const decision = decisions.find((value) => value === payload.decision);
  const checks = { ...previous?.checks };
  const layers: [Layer, string][] = [['mandate', 'mandate'], ['injection', 'injection'], ['judge', 'intent'], ['risk', 'risk']];
  for (const [layer, key] of layers) {
    const status = asText(result[key]);
    if (status) checks[layer] = { ...checks[layer], status };
  }

  function updateCheck(layer: Layer, status?: string, rationale?: string) {
    const current = checks[layer];
    checks[layer] = { status: status ?? current?.status, rationale: rationale ?? current?.rationale };
  }

  const rationale = asText(payload.rationale) ?? asText(payload.reason);
  switch (event.type) {
    case 'mandate.verified':
    case 'mandate.failed':
      updateCheck('mandate', event.type === 'mandate.verified' ? 'pass' : 'fail', rationale);
      break;
    case 'scan.completed':
      updateCheck('injection', typeof payload.injection_suspected === 'boolean' ? (payload.injection_suspected ? 'fail' : 'pass') : undefined,
        rationale ?? asTexts(payload.snippets)?.join(' · '));
      break;
    case 'judge.completed':
      updateCheck('judge', typeof payload.aligned === 'boolean' ? (payload.aligned ? 'pass' : 'fail') : undefined, rationale);
      break;
    case 'risk.scored':
      updateCheck('risk', asText(payload.result), rationale ?? asTexts(payload.factors)?.join(' · '));
      break;
  }

  return {
    paymentId: event.payment_id,
    merchant: asText(payload.merchant_id) ?? previous?.merchant,
    amount: asText(payload.amount) ?? previous?.amount,
    currency: asText(payload.currency) ?? previous?.currency,
    decision: decision ?? previous?.decision,
    risk: asNumber(payload.risk_score) ?? (event.type === 'risk.scored' ? asNumber(payload.score) : undefined) ?? previous?.risk,
    reasons: asTexts(payload.reasons) ?? previous?.reasons,
    humanReview: event.type === 'human.approved' ? 'Human approved' :
      event.type === 'human.rejected' ? ['Human rejected', rationale].filter(Boolean).join(': ') :
      event.type === 'decision.made' ? undefined : previous?.humanReview,
    checks,
    paypal: {
      order_id: asText(paypal.order_id) ?? asText(payload.order_id) ?? previous?.paypal.order_id,
      authorization_id: asText(paypal.authorization_id) ?? asText(payload.authorization_id) ?? previous?.paypal.authorization_id,
      capture_id: asText(paypal.capture_id) ?? asText(payload.capture_id) ?? previous?.paypal.capture_id,
    },
  };
}

export function projectLedger(events: EventEnvelope[]) {
  const payments = new Map<string, LedgerRow>();
  const rows = events.slice().reverse().map((event): LedgerRow => {
    const previous = payments.get(event.payment_id);
    const row = {
      ...updateFacts(previous, event),
      id: event.id,
      eventId: event.id,
      type: event.type,
      time: Date.parse(event.ts),
    };
    if (event.payment_id) payments.set(event.payment_id, { ...row, id: event.payment_id });
    return row;
  }).reverse();
  const summaries = [...payments.values()].sort((a, b) => b.time - a.time);
  const counts = { total: summaries.length, allow: 0, step_up: 0, block: 0 };
  for (const row of summaries) if (row.decision) counts[row.decision]++;
  return { rows, summaries, payments, counts };
}

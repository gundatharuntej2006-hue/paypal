import type { MandateDraft } from '../lib/mandate';
import type { Decision, EventEnvelope, EventType, Mandate } from '../contracts/types';
import type { BenchmarkReport, Payment } from './models';

const merchants = ['Northstar Office', 'Harbor Electronics', 'Pixel Supply'];
const now = Date.now();
const mandates: Mandate[] = merchants.map((merchant, i) => ({
  mandate_id: 'mdt_00' + (i + 1),
  principal_id: 'demo_operator',
  agent_id: 'agent_shopper',
  task: ['Restock office supplies', 'Buy USB-C accessories', 'Replace a conference room adapter'][i],
  currency: 'USD',
  max_total: '120.00',
  max_per_txn: '60.00',
  step_up_over: '30.00',
  allowed_merchants: [merchant],
  allowed_categories: ['office_supplies', 'electronics_accessories'],
  valid_from: new Date(now - 86400000).toISOString(),
  expires_at: new Date(now + 86400000).toISOString(),
  nonce: 'demo-nonce-' + i,
  paypal_order_id: 'DEMO-MANDATE-ORDER-' + i,
  signature: 'demo-signature-' + i,
}));

const outcomes: Decision[] = ['allow', 'step_up', 'block', 'allow', 'block', 'step_up'];
const payments: Payment[] = outcomes.map((decision, i) => ({
  payment_id: 'pay_' + (481 + i),
  mandate_id: mandates[i % 3].mandate_id,
  merchant_id: merchants[i % 3],
  amount: ['18.49', '42.90', '24.99', '12.80', '49.00', '35.50'][i],
  currency: 'USD',
  decision,
  risk_score: decision === 'block' ? 0.91 : decision === 'step_up' ? 0.58 : 0.08,
  reasons: [decision === 'block' ? 'Merchant content contained an injection attempt' :
    decision === 'step_up' ? 'Amount exceeds the automatic approval threshold' : 'All checks passed'],
  checks: { mandate: 'pass', injection: decision === 'block' ? 'fail' : 'pass', intent: 'pass',
    risk: decision === 'step_up' ? 'review' : decision === 'block' ? 'fail' : 'pass' },
  paypal: {
    order_id: 'DEMO-ORDER-' + i,
    authorization_id: 'DEMO-AUTH-' + i,
    ...(decision === 'allow' ? { capture_id: 'DEMO-CAPTURE-' + i } : {}),
  },
  audit_event_id: 'evt_decision_' + i,
  ts: new Date(now - (6 - i) * 60000).toISOString(),
}));

let sequence = 0;
function makeEvent(payment: Payment, type: EventType, payload: Record<string, unknown>, ts = new Date().toISOString()): EventEnvelope {
  return { id: type === 'decision.made' ? payment.audit_event_id : 'evt_demo_' + ++sequence,
    type, payment_id: payment.payment_id, mandate_id: payment.mandate_id, ts, payload };
}

const events = payments.flatMap((payment) => {
  const steps: [EventType, Record<string, unknown>][] = [
    ['intent.received', { merchant_id: payment.merchant_id, amount: payment.amount, currency: payment.currency }],
    ['mandate.verified', { result: 'pass' }],
    ['scan.completed', { injection_suspected: payment.decision === 'block' }],
    ['judge.completed', { aligned: true, rationale: 'The item matches the mandate task.' }],
    ['risk.scored', { score: payment.risk_score }],
    ['decision.made', { ...payment }],
  ];
  if (payment.decision === 'allow') steps.push(['paypal.captured', { ...payment.paypal }]);
  if (payment.decision === 'block') steps.push(['paypal.voided', { ...payment.paypal }]);
  return steps.map(([type, payload], i) => makeEvent(payment, type, payload,
    new Date(Date.parse(payment.ts) + (i - 5) * 1000).toISOString()));
});

const reviewed = new Set<string>();
const listeners = new Set<(event: EventEnvelope) => void>();
let timer: number | undefined;
let nextPayment = 0;

function publish(event: EventEnvelope) {
  events.push(event);
  listeners.forEach((listener) => listener(event));
}

function review(id: string, type: 'human.approved' | 'human.rejected') {
  const payment = payments.find((item) => item.payment_id === id && item.decision === 'step_up');
  if (!payment || reviewed.has(id)) throw new Error('This approval is no longer pending.');
  reviewed.add(id);
  publish(makeEvent(payment, type, { reviewer: 'demo_operator' }));
}

const benchmark: BenchmarkReport = {
  classes: [
    { name: 'Prompt injection', blocked: 15, total: 16 },
    { name: 'Authority', blocked: 12, total: 12 },
    { name: 'Budget evasion', blocked: 10, total: 11 },
    { name: 'Merchant spoofing', blocked: 8, total: 8 },
    { name: 'Intent drift', blocked: 7, total: 8 },
    { name: 'Replay and retries', blocked: 7, total: 7 },
  ],
  false_positive_rate: 0.04,
  median_latency_ms: 840,
  full_stack_block_rate: 59 / 62,
  llm_only_block_rate: 0.68,
};

export const stub = {
  async listMandates() { return [...mandates]; },
  async createMandate(mandate: Mandate) {
    if (mandates.some((item) => item.mandate_id === mandate.mandate_id)) throw new Error('Mandate already exists.');
    mandates.push(mandate);
    return mandate;
  },
  async createMandateOrder(draft: MandateDraft) {
    return { id: 'DEMO-ORDER-' + draft.mandate_id };
  },
  async listPayments() { return [...payments].reverse(); },
  async getPaymentEvents(id: string) { return events.filter((event) => event.payment_id === id); },
  async listApprovals() { return payments.filter((item) => item.decision === 'step_up' && !reviewed.has(item.payment_id)); },
  async approve(id: string) { review(id, 'human.approved'); },
  async reject(id: string) { review(id, 'human.rejected'); },
  async getBenchmarkReport() { return benchmark; },
  subscribeEvents(onEvent: (event: EventEnvelope) => void, paymentId?: string, onConnection?: (connected: boolean) => void) {
    const listener = (event: EventEnvelope) => {
      if (!paymentId || event.payment_id === paymentId) onEvent(event);
    };
    listeners.add(listener);
    onConnection?.(true);
    timer ??= window.setInterval(() => {
      const payment = payments[nextPayment++ % payments.length];
      publish(makeEvent(payment, 'webhook.received', { order_id: payment.paypal?.order_id, source: 'sandbox-demo' }));
    }, 2500);
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0) {
        window.clearInterval(timer);
        timer = undefined;
      }
    };
  },
};

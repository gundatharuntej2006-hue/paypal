import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { client, GatewayError } from '../api/client';
import type { EventEnvelope } from '../contracts/types';
import { decisionLabels, projectLedger } from '../ledger/rows';
import './payment-detail.css';

const timestamp = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
const formatTime = (ts: string) => Number.isFinite(Date.parse(ts)) ? timestamp.format(new Date(ts)) : 'Time unavailable';
const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' ? value as Record<string, unknown> : {};
const text = (value: unknown) => typeof value === 'string' && value.length ? value : undefined;
const strings = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : undefined;
const flag = (value: unknown) => value === true ? 'Yes' : value === false ? 'No' : '—';
const money = (amount?: string, currency?: string) => amount ? [currency, amount].filter(Boolean).join(' ') : '—';

type Tone = 'allow' | 'step_up' | 'block' | 'neutral';

const eventStyles: Record<string, [string, string, Tone]> = {
  'intent.received': ['Payment intent received', '↳', 'neutral'],
  'mandate.verified': ['Mandate verified', '✓', 'allow'],
  'mandate.failed': ['Mandate rejected', '×', 'block'],
  'scan.completed': ['Injection scan completed', '⌕', 'neutral'],
  'judge.completed': ['Intent judge completed', '◇', 'neutral'],
  'risk.scored': ['Risk scored', '▥', 'neutral'],
  'decision.made': ['Decision recorded', '◆', 'neutral'],
  'paypal.order_created': ['PayPal order created', 'P', 'neutral'],
  'paypal.authorized': ['PayPal authorized', 'P', 'allow'],
  'paypal.captured': ['PayPal captured', 'P', 'allow'],
  'paypal.voided': ['PayPal authorization voided', 'P', 'step_up'],
  'paypal.refunded': ['PayPal refunded', 'P', 'step_up'],
  'webhook.received': ['Webhook received', '↻', 'neutral'],
  'human.approved': ['Human approved', '✓', 'allow'],
  'human.rejected': ['Human rejected', '×', 'block'],
  'reconciliation.mismatch': ['Reconciliation mismatch', '!', 'block'],
};

function eventStyle(type: string, payload: Record<string, unknown>): [string, string, Tone] {
  const fallback: [string, string, Tone] = type.startsWith('paypal.') ? ['PayPal activity', 'P', 'neutral'] : ['Audit event', '•', 'neutral'];
  const [title, icon, base] = Object.hasOwn(eventStyles, type) ? eventStyles[type] : fallback;
  let tone = base;
  if (type === 'decision.made') {
    const decision = payload.decision;
    if (decision === 'allow' || decision === 'step_up' || decision === 'block') tone = decision;
  }
  if (type === 'scan.completed' && typeof payload.injection_suspected === 'boolean') tone = payload.injection_suspected ? 'block' : 'allow';
  if (type === 'judge.completed' && typeof payload.aligned === 'boolean') tone = payload.aligned ? 'allow' : 'block';
  return [title, icon, tone];
}

function Facts({ items }: { items: [string, string | undefined][] }) {
  return <dl className="payment-facts">{items.map(([label, value]) =>
    <div key={label}><dt>{label}</dt><dd>{value ?? '—'}</dd></div>)}</dl>;
}

function EventContent({ event }: { event: EventEnvelope }) {
  const payload = record(event.payload);
  const rationale = text(payload.rationale) ?? text(payload.reason);
  switch (event.type) {
    case 'intent.received':
      return <Facts items={[['Merchant', text(payload.merchant_id)], ['Amount', money(text(payload.amount), text(payload.currency))]]} />;
    case 'scan.completed': {
      const snippets = strings(payload.snippets);
      const snippet = text(payload.snippet);
      if (snippet && !snippets.includes(snippet)) snippets.push(snippet);
      return <>
        <Facts items={[[ 'Injection suspected', flag(payload.injection_suspected) ]]} />
        {snippets.length > 0 && <ul className="flagged-snippets" aria-label="Flagged snippets">
          {snippets.map((value, index) => <li key={index}><mark>{value}</mark></li>)}
        </ul>}
      </>;
    }
    case 'judge.completed': {
      const confidence = number(payload.confidence);
      return <>
        <Facts items={[
          ['Aligned', flag(payload.aligned)],
          ['Confidence', confidence === undefined ? undefined : (confidence * 100).toFixed(1) + '%'],
          ['Externally influenced', flag(payload.influenced_by_external)],
        ]} />
        {rationale && <p>{rationale}</p>}
      </>;
    }
    case 'risk.scored': {
      const score = number(payload.score) ?? number(payload.risk_score);
      const factors = strings(payload.factors);
      return <div className="risk-breakdown">
        <Facts items={[[ 'Risk score', score?.toFixed(2) ]]} />
        {score !== undefined && <meter min={0} max={1} value={Math.max(0, Math.min(1, score))} aria-label="Risk score" />}
        {factors.length > 0 && <ul className="risk-factors" aria-label="Top risk factors">
          {factors.map((factor, index) => <li key={index}>{factor}</li>)}
        </ul>}
      </div>;
    }
    case 'decision.made': {
      const decision = text(payload.decision);
      const reasons = strings(payload.reasons);
      return <><Facts items={[[ 'Decision', decision && (Object.hasOwn(decisionLabels, decision) ? decisionLabels[decision as keyof typeof decisionLabels] : decision) ]]} />
        {reasons.length > 0 && <p>{reasons.join(' · ')}</p>}</>;
    }
  }
  if (event.type.startsWith('paypal.')) {
    const ids = record(payload.paypal);
    const amount = record(payload.amount);
    return <Facts items={[
      ['Order', text(payload.order_id) ?? text(ids.order_id)],
      ['Authorization', text(payload.authorization_id) ?? text(ids.authorization_id)],
      ['Capture', text(payload.capture_id) ?? text(ids.capture_id)],
      ['Refund', text(payload.refund_id) ?? text(ids.refund_id)],
      ['Amount', money(text(payload.amount) ?? text(amount.value), text(payload.currency) ?? text(amount.currency_code))],
    ]} />;
  }
  return rationale ? <p>{rationale}</p> : null;
}

export function mergePaymentEvents(history: EventEnvelope[], live: EventEnvelope[]): EventEnvelope[] {
  const seen = new Set<string>();
  return [...history, ...live].filter(({ id }) => {
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

export function PaymentTimeline({ events }: { events: EventEnvelope[] }) {
  return <ol className="timeline payment-timeline" aria-label="Payment audit events">
    {events.map((event) => {
      const [title, icon, tone] = eventStyle(event.type, record(event.payload));
      return <li key={event.id} data-event-id={event.id} className={'payment-event ' + tone}>
        <span className="payment-event-icon" aria-hidden="true">{icon}</span>
        <article className="panel card">
          <div className="event-heading"><h2>{title}</h2><time dateTime={event.ts}>{formatTime(event.ts)}</time></div>
          <p className="payment-event-type">{event.type} · {event.id}</p>
          <EventContent event={event} />
          <details><summary>Raw JSON payload</summary><pre>{JSON.stringify(event.payload ?? null, null, 2)}</pre></details>
        </article>
      </li>;
    })}
  </ol>;
}

const paymentStates: Record<string, string> = {
  'intent.received': 'Received', 'mandate.failed': 'Mandate rejected', 'mandate.verified': 'Verified',
  'scan.completed': 'Reviewing', 'judge.completed': 'Reviewing', 'risk.scored': 'Reviewing',
  'paypal.order_created': 'Order created', 'paypal.authorized': 'Authorized', 'paypal.captured': 'Captured',
  'paypal.voided': 'Voided', 'paypal.refunded': 'Refunded', 'human.approved': 'Approved', 'human.rejected': 'Rejected',
};

function PaymentSummary({ id, events }: { id: string; events: EventEnvelope[] }) {
  const summary = projectLedger([...events].reverse()).payments.get(id);
  let state: string | undefined;
  let decisionTime: number | undefined;
  for (const event of events) {
    const payload = record(event.payload);
    if (event.type === 'decision.made') {
      const decision = payload.decision;
      state = decision === 'step_up' ? 'Awaiting approval' : decision === 'block' ? 'Blocked' : 'Decided';
      decisionTime = Date.parse(event.ts);
    }
    state = text(payload.state) ?? (Object.hasOwn(paymentStates, event.type) ? paymentStates[event.type] : state);
  }
  const elapsed = decisionTime === undefined ? NaN : decisionTime - Date.parse(events[0].ts);
  const decision = summary?.decision;
  return <section className="panel card payment-summary" aria-label="Payment summary">
    <div className="event-heading">
      <div><p className="eyebrow">{id}</p><h2>{summary?.merchant ?? 'Merchant unavailable'}</h2></div>
      <span className={'pill ' + (decision ?? '')}>{decision ? decisionLabels[decision] : 'Pending'}</span>
    </div>
    <Facts items={[
      ['Amount', money(summary?.amount, summary?.currency)], ['State', state],
      ['Decision latency', Number.isFinite(elapsed) && elapsed >= 0 ? elapsed.toLocaleString() + ' ms' : undefined],
      ['PayPal order', summary?.paypal.order_id], ['Authorization', summary?.paypal.authorization_id], ['Capture', summary?.paypal.capture_id],
    ]} />
  </section>;
}

function PaymentDetail({ id }: { id: string }) {
  const [live, setLive] = useState<EventEnvelope[]>([]);
  const [history, setHistory] = useState<EventEnvelope[]>();
  const [error, setError] = useState<unknown>();
  const [attempt, setAttempt] = useState(0);

  useEffect(() => client.subscribeEvents((event) => setLive((current) =>
    current.some((item) => item.id === event.id) ? current : [...current, event]), id), [id]);

  useEffect(() => {
    let active = true;
    setError(undefined);
    void client.getPaymentEvents(id).then(
      (result) => { if (active) setHistory(result); },
      (failure: unknown) => { if (active) setError(failure); },
    );
    return () => { active = false; };
  }, [id, attempt]);

  const events = mergePaymentEvents(history ?? [], live);
  const notFound = error instanceof GatewayError && error.status === 404;
  return <section className="payment-detail">
    <header className="page-heading"><p className="eyebrow">Audit trail</p><h1>Payment Detail</h1>
      <p>Inspect the recorded checks, approval decisions, and PayPal activity for this payment.</p></header>
    <Link className="back" to="/ledger">← Back to Live Ledger</Link>
    {error ? <div className="panel notice" role="alert">
      <p>{notFound ? 'Payment ' + id + ' was not found.' : error instanceof Error ? error.message : 'Unable to load this payment.'}</p>
      {!notFound && <button type="button" onClick={() => setAttempt((value) => value + 1)}>Try again</button>}
    </div> : history === undefined ? <div className="panel notice" role="status">Loading payment events…</div> :
      events.length === 0 ? <div className="panel notice">No events recorded for {id}.</div> : <>
        <PaymentSummary id={id} events={events} />
        <PaymentTimeline events={events} />
      </>}
  </section>;
}

export function PaymentRoute() {
  const { id = '' } = useParams();
  return <PaymentDetail key={id} id={id} />;
}

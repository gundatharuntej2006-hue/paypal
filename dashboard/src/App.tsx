import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Link, NavLink, Navigate, Route, Routes, useParams } from 'react-router-dom';
import { client, useStub } from './api/client';
import type { Payment } from './api/models';
import type { Decision, EventEnvelope } from './contracts/types';

const pages = [
  { path: '/mandates', label: 'Mandates', icon: '◇' },
  { path: '/ledger', label: 'Live Ledger', icon: '▤' },
  { path: '/approvals', label: 'Approvals', icon: '✓' },
  { path: '/benchmark', label: 'Benchmark', icon: '▥' },
];

const decisionLabels: Record<Decision, string> = { allow: 'Allow', step_up: 'Step-up', block: 'Block' };
const percent = (value: number) => new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 }).format(value);
const amount = (value: string, currency: string) =>
  new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(Number(value));
const date = (value: string) => new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
const detailPath = (id: string) => '/payments/' + encodeURIComponent(id);
const errorText = (error: unknown) => error instanceof Error ? error.message : 'An unexpected error occurred.';

function useData<T>(load: () => Promise<T>) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ data?: T; error?: string }>({});
  useEffect(() => {
    let current = true;
    setResult({});
    load().then(
      (data) => { if (current) setResult({ data }); },
      (error: unknown) => { if (current) setResult({ error: errorText(error) }); },
    );
    return () => { current = false; };
  }, [load, attempt]);
  return { ...result, retry: () => setAttempt((value) => value + 1) };
}

function DataView<T>({ state, empty, children }: {
  state: ReturnType<typeof useData<T>>;
  empty: string;
  children: (data: T) => ReactNode;
}) {
  if (state.error) return <div className="panel notice" role="alert"><p>{state.error}</p><button onClick={state.retry}>Try again</button></div>;
  if (state.data === undefined) return <div className="panel notice" role="status">Loading…</div>;
  return <>{children(state.data) || <div className="panel notice">{empty}</div>}</>;
}

function Page({ label, title, description, children }: {
  label: string; title: string; description: string; children: ReactNode;
}) {
  return <>
    <header className="page-heading">
      <p className="eyebrow">{label}</p><h1>{title}</h1><p>{description}</p>
    </header>
    {children}
  </>;
}

function DecisionBadge({ decision }: { decision: Decision }) {
  return <span className={'pill ' + decision}>{decisionLabels[decision]}</span>;
}

function ConnectionStatus({ connected }: { connected: boolean }) {
  return <div className="connection" aria-label={useStub ? 'Stub event stream' : 'Live SSE'}>
    <span className="pill">{useStub ? 'Stub mode' : 'Live mode'}</span>
    <span className="connection-state" role="status">
      <span className={connected ? 'status-dot connected' : 'status-dot'} />
      {connected ? 'Stream connected' : 'Stream disconnected'}
    </span>
  </div>;
}

function Mandates() {
  const state = useData(client.listMandates);
  return <Page label="Policy" title="Mandates" description="Review spending authority, limits, and expiry for each agent.">
    <DataView state={state} empty="No mandates have been created.">{(mandates) => mandates.length > 0 &&
      <div className="cards">{mandates.map((mandate) => <article className="panel card" key={mandate.mandate_id}>
        <p className="eyebrow">{mandate.mandate_id}</p><h2>{mandate.task}</h2>
        <dl>
          <dt>Agent</dt><dd>{mandate.agent_id}</dd>
          <dt>Total cap</dt><dd>{amount(mandate.max_total, mandate.currency)}</dd>
          <dt>Per purchase</dt><dd>{amount(mandate.max_per_txn, mandate.currency)}</dd>
          <dt>Expires</dt><dd>{date(mandate.expires_at)}</dd>
        </dl>
      </article>)}</div>}
    </DataView>
  </Page>;
}

function Ledger({ recent }: { recent: EventEnvelope[] }) {
  const state = useData(client.listPayments);
  return <Page label="Activity" title="Live Ledger" description="Review payment decisions and follow the latest events from AgentGuard.">
    <DataView state={state} empty="No payments have been recorded.">{(payments) => payments.length > 0 &&
      <div className="panel table-wrap"><table>
        <caption>Recent payments</caption>
        <thead><tr><th scope="col">Payment</th><th scope="col">Merchant</th><th scope="col">Amount</th><th scope="col">Decision</th><th scope="col">Recorded</th></tr></thead>
        <tbody>{payments.map((payment) => <tr key={payment.payment_id}>
          <td><Link to={detailPath(payment.payment_id)}>{payment.payment_id}</Link></td>
          <td>{payment.merchant_id}</td><td>{amount(payment.amount, payment.currency)}</td>
          <td><DecisionBadge decision={payment.decision} /></td><td>{date(payment.ts)}</td>
        </tr>)}</tbody>
      </table></div>}
    </DataView>
    <section className="panel card activity" aria-label="Incoming events">
      <h2>Incoming events</h2>
      {recent.length === 0 ? <p>Waiting for the first event…</p> :
        <ul className="feed">{recent.map((event) => <li key={event.id}>
          <span>{event.type}</span> <Link to={detailPath(event.payment_id)}>{event.payment_id}</Link>
          <time dateTime={event.ts}>{new Date(event.ts).toLocaleTimeString()}</time>
        </li>)}</ul>}
    </section>
  </Page>;
}

function Approvals() {
  const state = useData(client.listApprovals);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState('');
  async function review(payment: Payment, action: 'approve' | 'reject') {
    setPending(payment.payment_id);
    setError('');
    try {
      await client[action](payment.payment_id);
      state.retry();
    } catch (error) {
      setError(errorText(error));
    } finally {
      setPending(null);
    }
  }

  return <Page label="Review" title="Approvals" description="Review payments that require a human decision before they can proceed.">
    {error && <p className="panel notice" role="alert">{error}</p>}
    <DataView state={state} empty="There are no payments awaiting approval.">{(payments) => payments.length > 0 &&
      <div className="cards">{payments.map((payment) => <article className="panel card" key={payment.payment_id}>
        <DecisionBadge decision="step_up" /><h2>{payment.merchant_id}</h2>
        <p>{amount(payment.amount, payment.currency)} · <Link to={detailPath(payment.payment_id)}>{payment.payment_id}</Link></p>
        <p>{payment.reasons.join(' · ')}</p>
        <div className="actions">
          <button disabled={pending !== null} onClick={() => void review(payment, 'approve')}>Approve</button>
          <button className="secondary" disabled={pending !== null} onClick={() => void review(payment, 'reject')}>Reject</button>
        </div>
      </article>)}</div>}
    </DataView>
  </Page>;
}

function PaymentDetail({ id }: { id: string }) {
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [load] = useState(() => () => client.getPaymentEvents(id));
  const state = useData(load);
  useEffect(() => client.subscribeEvents((event) => {
    setEvents((current) => current.some((item) => item.id === event.id) ? current : [...current, event]);
  }, id), [id]);
  return <Page label="Audit trail" title="Payment Detail" description={'Recorded checks and PayPal activity for ' + id + '.'}>
    <Link className="back" to="/ledger">← Back to Live Ledger</Link>
    <DataView state={state} empty="No events found for this payment.">{(history) => {
      const timeline = [...new Map([...history, ...events].map((event) => [event.id, event])).values()]
        .sort((first, second) => Date.parse(first.ts) - Date.parse(second.ts));
      return timeline.length > 0 && <ol className="timeline">{timeline.map((event) => <li className="panel card" key={event.id}>
        <div className="event-heading"><h2>{event.type}</h2><time dateTime={event.ts}>{date(event.ts)}</time></div>
        <p>{event.id} · {event.mandate_id}</p>
        <details><summary>Event payload</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>
      </li>)}</ol>;
    }}</DataView>
  </Page>;
}

function PaymentRoute() {
  const { id = '' } = useParams();
  return <PaymentDetail key={id} id={id} />;
}

function Benchmark() {
  const state = useData(client.getBenchmarkReport);
  return <Page label="Evaluation" title="Benchmark" description={useStub ? 'Illustrative results from the demo dataset.' : 'Attack coverage, false positives, and decision latency.'}>
    <DataView state={state} empty="A benchmark report has not been published.">{(report) => report?.classes.length > 0 &&
      <>
        <div className="cards metrics">
          <div className="panel card"><p>Full stack block rate</p><strong>{percent(report.full_stack_block_rate)}</strong></div>
          <div className="panel card"><p>LLM-only block rate</p><strong>{percent(report.llm_only_block_rate)}</strong></div>
          <div className="panel card"><p>False-positive rate</p><strong>{percent(report.false_positive_rate)}</strong></div>
          <div className="panel card"><p>Median latency</p><strong>{report.median_latency_ms} ms</strong></div>
        </div>
        <section className="panel card activity"><h2>Blocked attacks by class</h2>
          <ul className="benchmark">{report.classes.map((entry) => <li key={entry.name}>
            <span>{entry.name}</span><progress aria-label={entry.name} max={entry.total || 1} value={entry.blocked} />
            <span>{entry.blocked} / {entry.total}</span>
          </li>)}</ul>
        </section>
      </>}
    </DataView>
  </Page>;
}

export function App() {
  const [connected, setConnected] = useState(false);
  const [recent, setRecent] = useState<EventEnvelope[]>([]);
  useEffect(() => client.subscribeEvents((event) => {
    setRecent((current) => [event, ...current.filter((item) => item.id !== event.id)].slice(0, 6));
  }, undefined, setConnected), []);

  return <div className="shell">
    <aside className="sidebar">
      <Link className="brand" to="/ledger"><span className="mark" aria-hidden="true">A</span> AgentGuard</Link>
      <p className="eyebrow nav-label">Workspace</p>
      <nav aria-label="Main navigation">{pages.map(({ path, label, icon }) =>
        <NavLink key={path} to={path}><span aria-hidden="true">{icon}</span>{label}</NavLink>)}</nav>
    </aside>
    <div className="workspace">
      <div className="topbar"><span>Payment firewall</span><ConnectionStatus connected={connected} /></div>
      <main>
        <Routes>
          <Route index element={<Navigate replace to="/ledger" />} />
          <Route path="/mandates" element={<Mandates />} />
          <Route path="/ledger" element={<Ledger recent={recent} />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/payments/:id" element={<PaymentRoute />} />
          <Route path="/benchmark" element={<Benchmark />} />
          <Route path="*" element={<Page label="AgentGuard" title="Page not found" description="This address does not match a dashboard page."><Link to="/ledger">Open Live Ledger</Link></Page>} />
        </Routes>
      </main>
    </div>
  </div>;
}

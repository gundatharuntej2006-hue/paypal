import { Approvals } from './approvals/Approvals';
import { useApprovalInbox } from './approvals/inbox';
import { PaymentRoute } from './payments/PaymentDetail';
import { Ledger } from './ledger/Ledger';
import { appendEvent } from './ledger/rows';
import { CopyButton, MandateBuilder } from './mandates/MandateBuilder';
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Link, NavLink, Navigate, Route, Routes } from 'react-router-dom';
import { client, useStub } from './api/client';

import type { EventEnvelope } from './contracts/types';

const pages = [
  { path: '/mandates', label: 'Mandates', icon: '◇' },
  { path: '/ledger', label: 'Live Ledger', icon: '▤' },
  { path: '/approvals', label: 'Approvals', icon: '✓' },
  { path: '/benchmark', label: 'Benchmark', icon: '▥' },
];

const percent = (value: number) => new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 }).format(value);
const amount = (value: string, currency: string) =>
  new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(Number(value));
const date = (value: string) => new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
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
  const now = Date.now();
  return <Page label="Policy" title="Mandates" description="Define spending authority and review signed mandates.">
    <MandateBuilder onSaved={state.retry} />
    <h2 className="saved-mandates">Saved mandates</h2>
    <DataView state={state} empty="No mandates have been created.">{(mandates) => mandates.length > 0 &&
      <div className="cards">{mandates.map((mandate) => {
        const status = Date.parse(mandate.expires_at) <= now ? 'Expired' :
          Date.parse(mandate.valid_from) > now ? 'Scheduled' : 'Active';
        const json = JSON.stringify(mandate, null, 2);
        return <article className="panel card" key={mandate.mandate_id}>
          <div className="actions"><p className="eyebrow">{mandate.mandate_id}</p>
            <span className={'pill ' + (status === 'Active' ? 'allow' : status === 'Expired' ? 'block' : 'step_up')}>{status}</span>
          </div>
          <h2>{mandate.task}</h2>
          <dl>
            <dt>Agent</dt><dd>{mandate.agent_id}</dd>
            <dt>Total cap</dt><dd>{amount(mandate.max_total, mandate.currency)}</dd>
            <dt>Per purchase</dt><dd>{amount(mandate.max_per_txn, mandate.currency)}</dd>
            <dt>Expires</dt><dd>{date(mandate.expires_at)}</dd>
          </dl>
          <details><summary>Mandate JSON</summary><pre>{json}</pre></details>
          <CopyButton text={json} label="Copy mandate JSON" />
        </article>;
      })}</div>}
    </DataView>
  </Page>;
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
  const [ledgerEvents, setLedgerEvents] = useState<EventEnvelope[]>([]);
  const inbox = useApprovalInbox(ledgerEvents, connected);
  useEffect(() => client.subscribeEvents((event) => {
    setLedgerEvents((current) => appendEvent(current, event));
  }, undefined, setConnected), []);

  return <div className="shell">
    <aside className="sidebar">
      <Link className="brand" to="/ledger"><span className="mark" aria-hidden="true">A</span> AgentGuard</Link>
      <p className="eyebrow nav-label">Workspace</p>
      <nav aria-label="Main navigation">{pages.map(({ path, label, icon }) =>
        <NavLink key={path} to={path}><span aria-hidden="true">{icon}</span>{label}
          {path === '/approvals' && <span className="pill approval-count" aria-label={inbox.pending.length + ' pending approvals'}>{inbox.pending.length}</span>}
        </NavLink>)}</nav>
    </aside>
    <div className="workspace">
      <div className="topbar"><span>Payment firewall</span><ConnectionStatus connected={connected} /></div>
      <main>
        <Routes>
          <Route index element={<Navigate replace to="/ledger" />} />
          <Route path="/mandates" element={<Mandates />} />
          <Route path="/ledger" element={<Ledger events={ledgerEvents} connected={connected} />} />
          <Route path="/approvals" element={<Approvals inbox={inbox} />} />
          <Route path="/payments/:id" element={<PaymentRoute />} />
          <Route path="/benchmark" element={<Benchmark />} />
          <Route path="*" element={<Page label="AgentGuard" title="Page not found" description="This address does not match a dashboard page."><Link to="/ledger">Open Live Ledger</Link></Page>} />
        </Routes>
      </main>
    </div>
  </div>;
}

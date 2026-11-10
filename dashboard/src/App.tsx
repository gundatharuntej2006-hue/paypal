import { useRecordingMode } from './demo/useRecordingMode';
import { DemoBanner } from './demo/DemoPanel';
import { HostedStartup } from './demo/HostedStartup';
import { Approvals } from './approvals/Approvals';
import { useApprovalInbox } from './approvals/inbox';
import { PaymentRoute } from './payments/PaymentDetail';
import { Ledger } from './ledger/Ledger';
import { appendEvent } from './ledger/rows';
import { CopyButton, MandateBuilder } from './mandates/MandateBuilder';
import { lazy, Suspense, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Link, NavLink, Navigate, Route, Routes } from 'react-router-dom';
import { client, useStub } from './api/client';

import type { EventEnvelope } from './contracts/types';

const Benchmark = lazy(() => import('./benchmark/Benchmark'));

const pages = [
  { path: '/mandates', label: 'Mandates', icon: '◇' },
  { path: '/ledger', label: 'Live Ledger', icon: '▤' },
  { path: '/approvals', label: 'Approvals', icon: '✓' },
  { path: '/benchmark', label: 'Benchmark', icon: '▥' },
];

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

function Workspace() {
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
        <DemoBanner />
        <Routes>
          <Route index element={<Navigate replace to="/ledger" />} />
          <Route path="/mandates" element={<Mandates />} />
          <Route path="/ledger" element={<Ledger events={ledgerEvents} connected={connected} />} />
          <Route path="/approvals" element={<Approvals inbox={inbox} />} />
          <Route path="/payments/:id" element={<PaymentRoute />} />
          <Route path="/benchmark" element={<Suspense fallback={<p role="status">Loading benchmark…</p>}><Benchmark /></Suspense>} />
          <Route path="*" element={<Page label="AgentGuard" title="Page not found" description="This address does not match a dashboard page."><Link to="/ledger">Open Live Ledger</Link></Page>} />
        </Routes>
      </main>
    </div>
  </div>;
}

export function App() {
  useRecordingMode();
  return <HostedStartup><Workspace /></HostedStartup>;
}

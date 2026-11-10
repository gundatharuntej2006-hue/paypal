import { DemoPanel } from '../demo/DemoPanel';
import { useMemo, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { Link } from 'react-router-dom';
import { dashboardGridTheme } from '../grid';
import type { ColDef } from 'ag-grid-community';
import type { CustomCellRendererProps } from 'ag-grid-react';
import { AgGridReact } from 'ag-grid-react';
import type { EventEnvelope } from '../contracts/types';
import { decisionLabels, decisions, projectLedger } from './rows';
import type { Layer, LedgerRow } from './rows';
import './ledger.css';

const displayTime = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
const money = (row: LedgerRow) => row.amount ? [row.currency, row.amount].filter(Boolean).join(' ') : '—';
const decisionName = (row: LedgerRow) => row.decision ? decisionLabels[row.decision] : 'Pending';
const rowReasons = (row: LedgerRow) => [row.humanReview, ...(row.reasons ?? [])].filter(Boolean).join(' · ');
const merchantName = (row: LedgerRow) => row.merchant ?? 'Unknown merchant';

function DecisionCell({ data }: CustomCellRendererProps<LedgerRow>) {
  return data ? <span className={'pill ' + (data.decision ?? '')}>{decisionName(data)}</span> : null;
}

const columns: ColDef<LedgerRow>[] = [
  { field: 'time', headerName: 'Time', width: 158, sort: 'desc', valueFormatter: ({ value }) => displayTime.format(value) },
  { field: 'paymentId', headerName: 'Payment ID', width: 128, cellClass: 'ledger-id', valueFormatter: ({ value }) => value || '—' },
  { field: 'merchant', headerName: 'Merchant', minWidth: 150, flex: 1, valueGetter: ({ data }) => data && merchantName(data) },
  { field: 'amount', headerName: 'Amount', width: 115, valueGetter: ({ data }) => data && money(data),
    comparator: (first, second, firstNode, secondNode) => Number(firstNode.data?.amount ?? 0) - Number(secondNode.data?.amount ?? 0) || String(first).localeCompare(String(second)) },
  { field: 'type', headerName: 'Event type', minWidth: 160, flex: 1 },
  { field: 'decision', headerName: 'Decision', width: 116, cellRenderer: DecisionCell, valueGetter: ({ data }) => data && decisionName(data) },
  { field: 'risk', headerName: 'Risk score', width: 122, filter: 'agNumberColumnFilter', valueFormatter: ({ value }) => value == null ? '—' : value.toFixed(2) },
  { headerName: 'Reasons', minWidth: 180, flex: 2, valueGetter: ({ data }) => (data && rowReasons(data)) || '—' },
];
const defaults: ColDef<LedgerRow> = { sortable: true, resizable: true, filter: 'agTextColumnFilter', enableCellChangeFlash: true };

function SetFilter({ title, options, selected, onChange }: {
  title: string; options: string[]; selected: string[]; onChange: (values: string[]) => void;
}) {
  return <details className="ledger-filter">
    <summary>{title}{selected.length > 0 && <span> · {selected.length}</span>}</summary>
    <div className="panel ledger-filter-menu">
      {options.length === 0 && <p>No values yet</p>}
      {options.map((option) => <label key={option}>
        <input type="checkbox" checked={selected.includes(option)}
          onChange={(event) => onChange(event.target.checked ? [...selected, option] : selected.filter((value) => value !== option))} />
        {option}
      </label>)}
    </div>
  </details>;
}

const layers: { key: Layer; label: string }[] = [
  { key: 'mandate', label: 'Mandate' }, { key: 'injection', label: 'Injection scan' },
  { key: 'judge', label: 'Intent judge' }, { key: 'risk', label: 'Risk engine' },
];

function Details({ row, onClose }: { row: LedgerRow; onClose: () => void }) {
  return <aside className="panel ledger-inspector" aria-label="Payment inspector">
    <div className="ledger-inspector-title">
      <div><p className="eyebrow">Payment inspector</p><h2>{row.paymentId}</h2></div>
      <button className="secondary" type="button" onClick={onClose} aria-label="Close payment inspector">×</button>
    </div>
    <p>{merchantName(row)} · {money(row)}</p>
    <span className={'pill ' + (row.decision ?? '')}>{decisionName(row)}</span>
    {rowReasons(row) && <p>{rowReasons(row)}</p>}
    <h3>Layer verdicts</h3>
    <ul className="ledger-verdicts">{layers.map(({ key, label }) => {
      const verdict = row.checks[key];
      const status = verdict?.status ?? 'Pending';
      const tone = status === 'pass' ? 'allow' : status === 'fail' ? 'block' : 'step_up';
      return <li key={key}><div><strong>{label}</strong><span className={'pill ' + tone}>{status}</span></div>
        {verdict?.rationale && <p>{verdict.rationale}</p>}</li>;
    })}</ul>
    <h3>PayPal IDs</h3>
    <dl className="ledger-paypal">
      <dt>Order</dt><dd>{row.paypal.order_id ?? '—'}</dd>
      <dt>Authorization</dt><dd>{row.paypal.authorization_id ?? '—'}</dd>
      <dt>Capture</dt><dd>{row.paypal.capture_id ?? '—'}</dd>
    </dl>
    <Link className="ledger-timeline" to={'/payments/' + encodeURIComponent(row.paymentId)}>Open full timeline <span aria-hidden="true">→</span></Link>
  </aside>;
}

function Grid({ rows, onInspect, empty, gridRef }: {
  rows: LedgerRow[]; onInspect: (id: string) => void; empty: string; gridRef: RefObject<AgGridReact<LedgerRow>>;
}) {
  return <div className="ledger-grid">
    <AgGridReact<LedgerRow> ref={gridRef} theme={dashboardGridTheme} columnDefs={columns} defaultColDef={defaults} rowData={rows}
      getRowId={({ data }) => data.id}
      getRowClass={({ data }) => data && Date.now() - data.time < 1500 ? 'ledger-new-row' : undefined}
      onRowClicked={({ data }) => { if (data?.paymentId) onInspect(data.paymentId); }}
      onCellKeyDown={({ event, data }) => {
        if (event instanceof KeyboardEvent && event.key === 'Enter' && data?.paymentId) onInspect(data.paymentId);
      }}
      overlayNoRowsTemplate={empty} />
  </div>;
}

type Group = 'none' | 'decision' | 'merchant';

export function Ledger({ events, connected }: { events: EventEnvelope[]; connected: boolean }) {
  const gridRef = useRef<AgGridReact<LedgerRow>>(null);
  const [paused, setPaused] = useState<EventEnvelope[]>();
  const [view, setView] = useState<'payments' | 'events'>('payments');
  const [group, setGroup] = useState<Group>('none');
  const [expanded, setExpanded] = useState<string>();
  const [query, setQuery] = useState('');
  const [decisionFilter, setDecisionFilter] = useState<string[]>([]);
  const [merchantFilter, setMerchantFilter] = useState<string[]>([]);
  const [selected, setSelected] = useState('');

  const visibleEvents = paused ?? events;
  const projection = useMemo(() => projectLedger(visibleEvents), [visibleEvents]);
  const source = view === 'payments' ? projection.summaries : projection.rows;
  const merchants = [...new Set(source.map(merchantName))].sort();
  const search = query.trim().toLowerCase();
  const rows = source.filter((row) =>
    (!decisionFilter.length || decisionFilter.includes(decisionName(row))) &&
    (!merchantFilter.length || merchantFilter.includes(merchantName(row))) &&
    (!search || [row.paymentId, merchantName(row), money(row), row.type, decisionName(row), row.risk, rowReasons(row)]
      .some((value) => String(value ?? '').toLowerCase().includes(search))));
  const groups = new Map<string, LedgerRow[]>();
  if (group !== 'none') for (const row of rows) {
    const key = group === 'decision' ? decisionName(row) : merchantName(row);
    const items = groups.get(key);
    if (items) items.push(row);
    else groups.set(key, [row]);
  }
  const openGroup = expanded === undefined ? groups.keys().next().value : expanded;
  const inspector = projection.payments.get(selected);
  const empty = visibleEvents.length === 0 ? (connected ? 'Waiting for payment events…' : 'Waiting for the stream to connect…') : 'No rows match this view or filter.';

  function clearFilters() {
    setQuery('');
    setDecisionFilter([]);
    setMerchantFilter([]);
    gridRef.current?.api.setFilterModel(null);
  }

  return <section className="ledger">
    <header className="page-heading">
      <p className="eyebrow">Activity / Audit stream</p><h1>Live Ledger</h1>
      <p>Inspect payment decisions, security checks, and PayPal activity as events arrive.</p>
    </header>
    <DemoPanel />
    <div className="ledger-counters" aria-label="Payment counts">
      <div className="panel"><span>Total payments</span><strong>{projection.counts.total}</strong></div>
      <div className="panel"><span>Allowed</span><strong className="counter-allow">{projection.counts.allow}</strong></div>
      <div className="panel"><span>Step-up</span><strong className="counter-review">{projection.counts.step_up}</strong></div>
      <div className="panel"><span>Blocked</span><strong className="counter-block">{projection.counts.block}</strong></div>
    </div>
    <div className={'ledger-body' + (inspector ? ' has-inspector' : '')}>
      <section className="panel ledger-board" aria-label="Payment and event grid">
        <div className="ledger-toolbar">
          <div className="actions" aria-label="Ledger view">
            <button type="button" className="secondary" aria-pressed={view === 'payments'} onClick={() => setView('payments')}>Payments</button>
            <button type="button" className="secondary" aria-pressed={view === 'events'} onClick={() => setView('events')}>All events</button>
          </div>
          <button type="button" className="secondary" onClick={() => setPaused(paused ? undefined : events)}>{paused ? 'Resume stream' : 'Pause stream'}</button>
        </div>
        <div className="ledger-toolbar ledger-filters">
          <input type="search" aria-label="Quick filter" placeholder="Filter payment, merchant, or event…" value={query}
            onChange={(event) => setQuery(event.target.value)} />
          <SetFilter title="Decision" options={[...decisions.map((value) => decisionLabels[value]), 'Pending']} selected={decisionFilter} onChange={setDecisionFilter} />
          <SetFilter title="Merchant" options={merchants} selected={merchantFilter} onChange={setMerchantFilter} />
          <select aria-label="Group rows" value={group} onChange={(event) => { setGroup(event.target.value as Group); setExpanded(undefined); }}>
            <option value="none">None</option><option value="decision">By decision</option><option value="merchant">By merchant</option>
          </select>
          <button type="button" className="secondary" onClick={clearFilters}>Clear filters</button>
        </div>
        {groups.size > 0 ? <div className="ledger-groups">{[...groups].map(([name, items]) =>
          <section key={name}>
            <button type="button" className="ledger-group" aria-expanded={openGroup === name}
              onClick={() => setExpanded(openGroup === name ? '' : name)}>
              <span aria-hidden="true">{openGroup === name ? '▾' : '▸'}</span> {name} <span className="pill">{items.length}</span>
            </button>
            {openGroup === name && <Grid rows={items} onInspect={setSelected} empty={empty} gridRef={gridRef} />}
          </section>)}</div> : <Grid key={view} rows={rows} onInspect={setSelected} empty={empty} gridRef={gridRef} />}
        <footer className="ledger-footer">
          <span>{rows.length.toLocaleString()} {view === 'payments' ? 'payments' : 'events'} shown · {visibleEvents.length.toLocaleString()} retained events</span>
          <span>{paused ? 'Display paused · incoming events buffered' : connected ? 'Receiving events' : 'Stream disconnected'}</span>
        </footer>
      </section>
      {inspector && <Details row={inspector} onClose={() => setSelected('')} />}
    </div>
  </section>;
}

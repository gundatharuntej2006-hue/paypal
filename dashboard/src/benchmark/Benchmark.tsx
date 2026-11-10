import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { AgGridReact } from 'ag-grid-react';
import type { ColDef, ICellRendererParams } from 'ag-grid-community';
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { client } from '../api/client';
import { dashboardGridTheme } from '../grid';
import { benchmarkArms, catchingLayers } from './report';
import type { BenchmarkReport, BenchmarkScenario, LoadedReport } from './report';
import './benchmark.css';

const percent = new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 });
const count = new Intl.NumberFormat();
const humanize = (value: string) => value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase());
const formatRate = (value: number) => percent.format(value);
const unavailable = 'Unavailable';
const metricRate = (value: number | null) => value === null ? unavailable : formatRate(value);
const metricCount = (value: number | null) => value === null ? unavailable : count.format(value);
const metricLatency = (value: number | null) => value === null ? unavailable : count.format(value) + ' ms';
const formatDecision = (value: BenchmarkScenario['actual']) => value === null ? unavailable : value === 'step_up' ? 'Step-up' : humanize(value);
const tooltipStyle = { background: 'var(--surface)', borderColor: 'var(--border)', color: 'var(--text)', borderRadius: '.5rem' };
const axis = { fill: 'var(--muted)', fontSize: 12 };
const rateTicks = [0, 0.25, 0.5, 0.75, 1];

function Chart({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <section className="panel card benchmark-chart" aria-label={title}>
    <h2>{title}</h2><p>{description}</p><div className="benchmark-plot">{children}</div>
  </section>;
}

function Charts({ report }: { report: BenchmarkReport }) {
  const byClass = report.classes.filter((entry) => entry.block_rate !== null).map((entry) => ({ ...entry, name: humanize(entry.name) }));
  const byLayer = catchingLayers.flatMap((layer) => {
    const caught = report.catching_layer_counts[layer];
    return caught == null ? [] : [{ name: humanize(layer), caught }];
  });
  const ablation = [
    { metric: 'Block rate', llm: report.ablation.llm_only.block_rate ?? undefined, full: report.ablation.full_stack.block_rate ?? undefined },
    { metric: 'False positives', llm: report.ablation.llm_only.false_positive_rate ?? undefined, full: report.ablation.full_stack.false_positive_rate ?? undefined },
  ].filter((entry) => entry.llm !== undefined || entry.full !== undefined);
  return <div className="benchmark-charts">
    <Chart title="Block rate by class" description="Share of attacks blocked in each class.">
      {byClass.length === 0 ? <p>Unavailable</p> :
        <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 320, height: 232 }}>
          <BarChart data={byClass} layout="vertical" margin={{ left: 0, right: 14 }}>
            <CartesianGrid horizontal={false} stroke="var(--border)" />
            <XAxis type="number" domain={[0, 1]} ticks={rateTicks} tick={axis} tickFormatter={formatRate} />
            <YAxis type="category" dataKey="name" width={112} tick={axis} interval={0} />
            <Tooltip formatter={(value) => typeof value === 'number' ? formatRate(value) : unavailable} contentStyle={tooltipStyle} itemStyle={{ color: 'var(--text)' }} />
            <Bar dataKey="block_rate" name="Blocked" fill="var(--benchmark-primary)" radius={[0, 5, 5, 0]} maxBarSize={20} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>}
    </Chart>
    <Chart title="Catching layer" description="Number of attacks attributed to each layer.">
      {byLayer.length === 0 ? <p>Unavailable</p> : <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 320, height: 232 }}>
        <BarChart data={byLayer} margin={{ left: 0, right: 14 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="name" tick={axis} interval={0} />
          <YAxis tick={axis} allowDecimals={false} width={34} />
          <Tooltip contentStyle={tooltipStyle} itemStyle={{ color: 'var(--text)' }} />
          <Bar dataKey="caught" name="Caught" fill="var(--benchmark-secondary)" radius={[5, 5, 0, 0]} maxBarSize={32} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>}
    </Chart>
    <Chart title="Ablation comparison" description="LLM-only decisions compared with the full firewall.">
      {ablation.length === 0 ? <p>Unavailable</p> : <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 320, height: 232 }}>
        <BarChart data={ablation} margin={{ left: 0, right: 14 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="metric" tick={axis} />
          <YAxis domain={[0, 1]} ticks={rateTicks} tick={axis} tickFormatter={formatRate} width={42} />
          <Tooltip formatter={(value) => typeof value === 'number' ? formatRate(value) : unavailable} contentStyle={tooltipStyle} itemStyle={{ color: 'var(--text)' }} />
          <Legend />
          <Bar dataKey="llm" name="LLM only" fill="var(--benchmark-secondary)" radius={[4, 4, 0, 0]} maxBarSize={36} isAnimationActive={false} />
          <Bar dataKey="full" name="Full stack" fill="var(--benchmark-primary)" radius={[4, 4, 0, 0]} maxBarSize={36} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>}
    </Chart>
  </div>;
}

type ScenarioRow = BenchmarkScenario & { outcome: 'Pass' | 'Fail' | 'Unresolved' };
const statusCell = ({ value }: ICellRendererParams<ScenarioRow, ScenarioRow['outcome']>) =>
  <span className={'pill ' + (value === 'Pass' ? 'allow' : value === 'Fail' ? 'block' : 'step_up')}>{value}</span>;
const columns: ColDef<ScenarioRow>[] = [
  { field: 'id', headerName: 'Scenario ID', width: 150 },
  { field: 'label', headerName: 'Scenario', minWidth: 210, flex: 2 },
  { field: 'class', width: 160, valueFormatter: ({ value }) => humanize(value) },
  { field: 'outcome', headerName: 'Result', width: 105, cellRenderer: statusCell },
  { field: 'expected', width: 112, valueFormatter: ({ value }) => formatDecision(value) },
  { field: 'actual', width: 105, valueFormatter: ({ value }) => formatDecision(value) },
  { field: 'layer', width: 115, valueFormatter: ({ value }) => value ? humanize(value) : '—' },
  { field: 'latency_ms', headerName: 'Latency', width: 115, filter: 'agNumberColumnFilter',
    valueFormatter: ({ value }) => metricLatency(value ?? null) },
];
const columnDefaults: ColDef<ScenarioRow> = { sortable: true, filter: 'agTextColumnFilter', resizable: true };

function ScenarioTable({ scenarios }: { scenarios: BenchmarkScenario[] }) {
  const [classFilter, setClass] = useState('');
  const [outcome, setOutcome] = useState('');
  const [layer, setLayer] = useState('');
  const classes = [...new Set(scenarios.map((row) => row.class))].sort();
  const rows: ScenarioRow[] = scenarios.map((scenario) => ({ ...scenario, outcome: scenario.actual === null || scenario.actual === 'error' ? 'Unresolved' : scenario.expected === scenario.actual ? 'Pass' : 'Fail' }));
  const filtered = rows.filter((row) => (!classFilter || row.class === classFilter) &&
    (!outcome || row.outcome === outcome) && (!layer || (row.layer ?? 'none') === layer));
  return <section className="panel card benchmark-table" aria-label="Scenario results">
    <div className="event-heading"><h2>Scenario results</h2><span className="pill">{filtered.length} of {rows.length} scenarios</span></div>
    <div className="actions benchmark-filters">
      <label>Class <select aria-label="Class" value={classFilter} onChange={(event) => setClass(event.target.value)}>
        <option value="">All classes</option>{classes.map((name) => <option key={name} value={name}>{humanize(name)}</option>)}
      </select></label>
      <label>Result <select aria-label="Result" value={outcome} onChange={(event) => setOutcome(event.target.value)}>
        <option value="">All results</option><option>Pass</option><option>Fail</option><option>Unresolved</option>
      </select></label>
      <label>Layer <select aria-label="Layer" value={layer} onChange={(event) => setLayer(event.target.value)}>
        <option value="">All layers</option>{catchingLayers.map((name) => <option key={name} value={name}>{humanize(name)}</option>)}
        <option value="none">No layer</option>
      </select></label>
    </div>
    <div className="benchmark-grid">
      <AgGridReact<ScenarioRow> theme={dashboardGridTheme} columnDefs={columns} defaultColDef={columnDefaults}
        rowData={filtered} getRowId={({ data }) => data.id} overlayNoRowsTemplate="No scenarios match these filters." />
    </div>
  </section>;
}

const armLabels = { full_stack: 'Full stack', llm_only: 'LLM only' } as const;
const provenanceLabels = {
  genuine: 'Genuine measurements',
  synthetic: 'Synthetic test-double results',
  unexecuted: 'Unexecuted validation report',
};

function CoverageTable({ report }: { report: BenchmarkReport }) {
  return <section className="panel card benchmark-coverage" aria-label="Run coverage">
    <h2>Run coverage</h2>
    <div className="table-wrap">
      <table>
        <thead><tr><th scope="col">Arm</th><th scope="col">Cohort</th><th scope="col">Planned</th>
          <th scope="col">Observed</th><th scope="col">Unresolved</th></tr></thead>
        <tbody>{benchmarkArms.flatMap((arm) => (['malicious', 'benign'] as const).map((cohort) => {
          const { planned, observed, unresolved } = report.coverage[arm][cohort];
          return <tr key={arm + cohort}><th scope="row">{armLabels[arm]}</th><td>{humanize(cohort)}</td>
            <td>{metricCount(planned)}</td><td>{metricCount(observed)}</td><td>{metricCount(unresolved)}</td></tr>;
        }))}</tbody>
      </table>
    </div>
  </section>;
}

export function Results({ loaded }: { loaded: LoadedReport }) {
  const { report, source, notice } = loaded;
  const full = report.coverage.full_stack;
  const stats = [
    { label: 'Overall block rate', value: metricRate(report.ablation.full_stack.block_rate), detail: metricCount(full.malicious.observed) + ' observed malicious decisions' },
    { label: 'False-positive rate', value: metricRate(report.false_positive_rate), detail: metricCount(full.benign.observed) + ' observed benign decisions' },
    { label: 'Median latency', value: metricLatency(report.median_latency_ms), detail: 'P95 ' + metricLatency(report.p95_latency_ms) },
    { label: 'Latency samples', value: metricCount(report.latency_sample_count), detail: 'Full-stack arm' },
  ];
  return <>
    <div className="benchmark-banner" role="status">
      {source === 'sample' && <strong>Sample data</strong>}
      <strong>{provenanceLabels[report.provenance]}</strong>
      {report.provenance === 'synthetic' && <span>These results describe simulated behavior.</span>}
      {report.provenance === 'unexecuted' && <span>Validation only. No benchmark decisions were measured.</span>}
      {notice && <small>{notice}</small>}
    </div>
    <div className="benchmark-run">
      {report.generated_at && <span>{source === 'sample' ? 'Sample run' : 'Report generated'} <time dateTime={report.generated_at}>{new Date(report.generated_at).toLocaleString()}</time></span>}
      {report.run_id && <span>Run ID <code>{report.run_id}</code></span>}
      {source !== 'sample' && <span className="pill">{source === 'gateway' ? 'Gateway report' : 'Published report'}</span>}
    </div>
    <div className="cards benchmark-stats" aria-label="Benchmark metrics">
      {stats.map(({ label, value, detail }) => <div className="panel card" key={label}>
        <p>{label}</p><strong>{value}</strong><small>{detail}</small>
      </div>)}
    </div>
    <CoverageTable report={report} />
    <Charts report={report} />
    {report.scenarios.length > 0 ? <ScenarioTable key={report.run_id} scenarios={report.scenarios} /> :
      <div className="panel notice">Scenario details were not included in this summary.</div>}
  </>;
}

export default function Benchmark() {
  const [state, setState] = useState<{ loaded?: LoadedReport; error?: string }>({});
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true;
    setState({});
    void client.getBenchmarkReport().then(
      (loaded) => { if (current) setState({ loaded }); },
      (error: unknown) => { if (current) setState({ error: error instanceof Error ? error.message : 'Unable to load the benchmark report.' }); },
    );
    return () => { current = false; };
  }, [attempt]);

  return <section className="benchmark-page">
    <header className="page-heading">
      <p className="eyebrow">Evaluation</p><h1>Benchmark</h1>
      <p>Explore attack coverage, false positives, and decision latency by run.</p>
    </header>
    {state.loaded ? <Results loaded={state.loaded} /> : state.error ?
      <div className="panel notice" role="alert"><p>{state.error}</p><button type="button" onClick={() => setAttempt((value) => value + 1)}>Try again</button></div> :
      <div className="panel notice" role="status">Loading benchmark report…</div>}
  </section>;
}

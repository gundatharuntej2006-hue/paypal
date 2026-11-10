import type { Decision } from '../contracts/types';

export const catchingLayers = ['mandate', 'injection', 'intent', 'risk', 'other'] as const;
export const benchmarkArms = ['full_stack', 'llm_only'] as const;
const cohorts = ['malicious', 'benign'] as const;
type Layer = typeof catchingLayers[number];
type Arm = typeof benchmarkArms[number];
type Cohort = typeof cohorts[number];
type Metric = number | null;
type Provenance = 'genuine' | 'synthetic' | 'unexecuted';

export interface Coverage {
  planned: Metric;
  observed: Metric;
  unresolved: Metric;
}

export interface BenchmarkScenario {
  id: string;
  class: string;
  label: string;
  expected: Decision;
  actual: Decision | 'error' | null;
  layer: Layer | null;
  latency_ms: Metric;
}

interface Rates {
  block_rate: Metric;
  false_positive_rate: Metric;
}

export interface BenchmarkReport {
  provenance: Provenance;
  run_id?: string;
  generated_at?: string;
  classes: { name: string; total: Metric; blocked: Metric; block_rate: Metric }[];
  false_positive_rate: Metric;
  median_latency_ms: Metric;
  p95_latency_ms: Metric;
  latency_sample_count: Metric;
  coverage: Record<Arm, Record<Cohort, Coverage>>;
  ablation: Record<Arm, Rates>;
  catching_layer_counts: Partial<Record<Layer, Metric>>;
  scenarios: BenchmarkScenario[];
}

export interface LoadedReport {
  report: BenchmarkReport;
  source: 'static' | 'gateway' | 'sample';
  notice?: string;
}

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;
const duration = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const count = (value: unknown) => duration(value) && Number.isInteger(value);
const rate = (value: unknown) => duration(value) && value <= 1;
const decision = (value: unknown) => value === 'allow' || value === 'step_up' || value === 'block';

function invalid(field: string): never {
  throw new Error('Report does not match the benchmark contract (' + field + ').');
}

function metric(value: unknown, check: (value: unknown) => boolean, field: string): Metric {
  if (value == null) return null;
  if (!check(value)) invalid(field);
  return value as number;
}

function coverageFor(arm: Record<string, unknown>, cohort: Cohort): Coverage {
  const values = record(arm[cohort]);
  const read = (name: keyof Coverage) =>
    metric(values[name] ?? arm[name + '_' + cohort] ?? arm[cohort + '_' + name], count, cohort + '.' + name);
  return { planned: read('planned'), observed: read('observed'), unresolved: read('unresolved') };
}

function rows(value: unknown, field: string): unknown[] {
  if (value == null) return [];
  if (!Array.isArray(value)) invalid(field);
  return value;
}

export function validateReport(value: unknown): BenchmarkReport {
  const report = record(value);
  const { provenance } = report;
  if (provenance !== 'genuine' && provenance !== 'synthetic' && provenance !== 'unexecuted') invalid('provenance');
  const runId = report.run_id;
  const generatedAt = report.generated_at;
  if (runId != null && !text(runId)) invalid('run_id');
  if (generatedAt != null && (!text(generatedAt) || !Number.isFinite(Date.parse(generatedAt)))) invalid('generated_at');

  const ablation = record(report.ablation);
  const coverage = record(report.coverage);
  const armCoverage = (arm: Arm) => {
    const data = record(coverage[arm] ?? record(ablation[arm]).coverage);
    return { malicious: coverageFor(data, 'malicious'), benign: coverageFor(data, 'benign') };
  };
  const fullCoverage = armCoverage('full_stack');
  const llmCoverage = armCoverage('llm_only');
  const armRates = (arm: Arm, counts: Record<Cohort, Coverage>): Rates => {
    const data = record(ablation[arm]);
    const block = metric(Object.hasOwn(data, 'block_rate') ? data.block_rate : report[arm + '_block_rate'], rate, arm + '.block_rate');
    const fp = metric(Object.hasOwn(data, 'false_positive_rate') ? data.false_positive_rate :
      arm === 'full_stack' ? report.false_positive_rate : undefined, rate, arm + '.false_positive_rate');
    return {
      block_rate: provenance === 'unexecuted' || counts.malicious.observed === 0 ? null : block,
      false_positive_rate: provenance === 'unexecuted' || counts.benign.observed === 0 ? null : fp,
    };
  };
  const fullStack = armRates('full_stack', fullCoverage);
  const classes = rows(report.classes ?? report.by_class, 'classes').map((entry) => {
    const row = record(entry);
    const name = row.name ?? row.class;
    if (!text(name)) invalid('classes.name');
    const total = metric(row.total, count, 'classes.total');
    const blocked = metric(row.blocked, count, 'classes.blocked');
    if (total !== null && blocked !== null && blocked > total) invalid('classes.blocked');
    const blockRate = Object.hasOwn(row, 'block_rate') ? metric(row.block_rate, rate, 'classes.block_rate') :
      total && blocked !== null ? blocked / total : null;
    return { name, total, blocked, block_rate: provenance === 'unexecuted' || total === 0 ? null : blockRate };
  });

  const scenarioIds = new Set<string>();
  const scenarios = rows(report.scenarios, 'scenarios').map((entry): BenchmarkScenario => {
    const { id, class: name, label, expected, actual = null, layer = null, latency_ms } = record(entry);
    if (!text(id) || scenarioIds.has(id) || !text(name) || !text(label) || !decision(expected) ||
        (actual !== null && !decision(actual) && actual !== 'error') ||
        (layer !== null && !catchingLayers.some((value) => value === layer))) invalid('scenarios');
    scenarioIds.add(id);
    return { id, class: name, label, expected: expected as Decision, actual: actual as BenchmarkScenario['actual'],
      layer: layer as Layer | null, latency_ms: metric(latency_ms, duration, 'scenarios.latency_ms') };
  });

  const counts = record(report.catching_layer_counts);
  const layerCounts: BenchmarkReport['catching_layer_counts'] = {};
  for (const layer of catchingLayers) {
    if (Object.hasOwn(counts, layer) && provenance !== 'unexecuted') layerCounts[layer] = metric(counts[layer], count, layer);
  }

  const fullStackData = record(ablation.full_stack);
  const latencyValue = (key: string) => Object.hasOwn(fullStackData, key) ? fullStackData[key] : report[key];
  const latencySamples = metric(latencyValue('latency_sample_count'), count, 'latency_sample_count');
  const median = metric(latencyValue('median_latency_ms'), duration, 'median_latency_ms');
  const p95 = metric(latencyValue('p95_latency_ms'), duration, 'p95_latency_ms');
  return {
    provenance,
    run_id: runId ?? undefined,
    generated_at: generatedAt ?? undefined,
    classes,
    false_positive_rate: fullStack.false_positive_rate,
    median_latency_ms: provenance === 'unexecuted' || latencySamples === 0 ? null : median,
    p95_latency_ms: provenance === 'unexecuted' || latencySamples === 0 ? null : p95,
    latency_sample_count: latencySamples,
    coverage: { full_stack: fullCoverage, llm_only: llmCoverage },
    ablation: { full_stack: fullStack, llm_only: armRates('llm_only', llmCoverage) },
    catching_layer_counts: layerCounts,
    scenarios,
  };
}

async function readReport(url: string, source: 'static' | 'gateway') {
  const response = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(5000) });
  // Static SPA hosts can rewrite a missing report to the HTML entry point.
  if (response.status === 404 || response.status === 204 ||
      (source === 'static' && response.ok && response.headers.get('content-type')?.includes('text/html'))) return;
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return validateReport(await response.json());
}

export async function loadBenchmarkReport({ source, staticUrl, gatewayUrl }: {
  source: 'auto' | 'static' | 'gateway';
  staticUrl: string;
  gatewayUrl?: string;
}): Promise<LoadedReport> {
  const locations: { source: 'static' | 'gateway'; url: string }[] = [];
  if (source !== 'gateway') locations.push({ source: 'static', url: staticUrl });
  if (source !== 'static' && gatewayUrl) locations.push({ source: 'gateway', url: gatewayUrl });
  const failures: string[] = [];

  for (const location of locations) {
    try {
      const report = await readReport(location.url, location.source);
      if (report) return { report, source: location.source };
    } catch (error) {
      failures.push((location.source === 'static' ? 'Static report' : 'Gateway report') + ': ' +
        (error instanceof Error ? error.message : 'Unable to load.'));
    }
  }

  const sample = await import('./benchmark-report.sample.json');
  return { report: validateReport(sample.default), source: 'sample', notice: failures.join(' ') || undefined };
}

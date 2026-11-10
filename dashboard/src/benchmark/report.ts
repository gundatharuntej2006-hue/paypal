import type { Decision } from '../contracts/types';

export const catchingLayers = ['mandate', 'injection', 'intent', 'risk', 'other'] as const;
type Layer = typeof catchingLayers[number];
type Rates = { block_rate: number; false_positive_rate: number };

export interface BenchmarkScenario {
  id: string;
  class: string;
  label: string;
  expected: Decision;
  actual: Decision | 'error';
  layer: Layer | null;
  latency_ms: number;
}

export interface BenchmarkReport {
  sample?: boolean;
  run_id: string;
  generated_at: string;
  totals: { attacks: number; benign: number };
  by_class: { class: string; total: number; blocked: number; block_rate: number }[];
  false_positive_rate: number;
  median_latency_ms: number;
  p95_latency_ms: number;
  catching_layer_counts: Record<Layer, number>;
  ablation: { llm_only: Rates; full_stack: Rates };
  scenarios: BenchmarkScenario[];
}

export interface LoadedReport {
  report: BenchmarkReport;
  source: 'static' | 'gateway' | 'sample';
  notice?: string;
}

const record = (value: unknown) => value && typeof value === 'object' ? value as Record<string, unknown> : {};
const text = (value: unknown) => typeof value === 'string' && value.trim().length > 0;
const duration = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const count = (value: unknown): value is number => duration(value) && Number.isInteger(value);
const rate = (value: unknown) => duration(value) && value <= 1;
const decision = (value: unknown) => value === 'allow' || value === 'step_up' || value === 'block';
const validRates = (value: unknown) => rate(record(value).block_rate) && rate(record(value).false_positive_rate);

export function validateReport(value: unknown): BenchmarkReport {
  const report = record(value);
  const totals = record(report.totals);
  const counts = record(report.catching_layer_counts);
  const ablation = record(report.ablation);
  const scenarioIds = new Set<string>();
  if (!text(report.run_id) || typeof report.generated_at !== 'string' || !Number.isFinite(Date.parse(report.generated_at)) ||
      (report.sample !== undefined && typeof report.sample !== 'boolean') ||
      !count(totals.attacks) || !count(totals.benign) || !rate(report.false_positive_rate) ||
      !duration(report.median_latency_ms) || !duration(report.p95_latency_ms) ||
      !catchingLayers.every((layer) => count(counts[layer])) ||
      !validRates(ablation.llm_only) || !validRates(ablation.full_stack) ||
      !Array.isArray(report.by_class) || !report.by_class.every((row) => {
        const { class: name, total, blocked, block_rate } = record(row);
        return text(name) && count(total) && count(blocked) && blocked <= total && rate(block_rate);
      }) ||
      !Array.isArray(report.scenarios) || !report.scenarios.every((row) => {
        const { id, class: name, label, expected, actual, layer, latency_ms } = record(row);
        if (typeof id !== 'string' || !text(id) || scenarioIds.has(id)) return false;
        scenarioIds.add(id);
        return text(name) && text(label) && decision(expected) && (decision(actual) || actual === 'error') &&
          (layer === null || catchingLayers.some((value) => value === layer)) && duration(latency_ms);
      })) {
    throw new Error('Report does not match the benchmark contract.');
  }
  return value as BenchmarkReport;
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
      if (report && !report.sample) return { report, source: location.source };
      if (report?.sample) failures.push('The ' + location.source + ' report is marked as sample.');
    } catch (error) {
      failures.push((location.source === 'static' ? 'Static report' : 'Gateway report') + ': ' +
        (error instanceof Error ? error.message : 'Unable to load.'));
    }
  }

  const sample = await import('./benchmark-report.sample.json');
  return { report: validateReport(sample.default), source: 'sample', notice: failures.join(' ') || undefined };
}

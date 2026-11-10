import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import genuine from './fixtures/genuine_partial_coverage.json';
import synthetic from './fixtures/synthetic.json';
import unexecuted from './fixtures/unexecuted.json';
import { Results } from './Benchmark';
import { loadBenchmarkReport, validateReport } from './report';

const cohort = (planned: number, observed: number) => ({ planned, observed, unresolved: planned - observed });
const fixture = {
  provenance: 'genuine',
  classes: [{ name: 'Merchant injection', total: 2, blocked: 0, block_rate: 0 }],
  coverage: {
    full_stack: { malicious: cohort(4, 2), benign: cohort(2, 0) },
    llm_only: { malicious: cohort(4, 0), benign: cohort(2, 1) },
  },
  ablation: {
    full_stack: { block_rate: 0, false_positive_rate: null },
    llm_only: { block_rate: null, false_positive_rate: 0 },
  },
  median_latency_ms: 420,
  p95_latency_ms: null,
  latency_sample_count: 2,
};

afterEach(() => vi.unstubAllGlobals());

describe('benchmark adapter summaries', () => {
  it.each([
    [genuine, 'Genuine measurements', 20, 30, 2, 57],
    [synthetic, 'Synthetic test-double results', 40, 70, 7, 55],
    [unexecuted, 'Unexecuted validation report', null, null, 0, 60],
  ] as const)('loads handoff fixture %#', async (fixture, label, median, p95, samples, unresolved) => {
    const fetchReport = vi.fn().mockResolvedValue(Response.json(fixture));
    vi.stubGlobal('fetch', fetchReport);
    const loaded = await loadBenchmarkReport({ source: 'static', staticUrl: '/benchmark-report.json' });
    expect(fetchReport).toHaveBeenCalledWith('/benchmark-report.json', expect.any(Object));
    expect(loaded.source).toBe('static');
    expect(loaded.report.coverage.full_stack.malicious.unresolved).toBe(unresolved);
    expect(loaded.report.coverage.llm_only).toEqual(fixture.ablation.llm_only.coverage);
    expect(loaded.report.latency_sample_count).toBe(samples);
    expect(loaded.report.median_latency_ms).toBe(median);
    expect(loaded.report.p95_latency_ms).toBe(p95);
    expect(loaded.report.classes.map(({ block_rate }) => block_rate)).toEqual(fixture.by_class.map(({ block_rate }) => block_rate));
    const html = renderToStaticMarkup(<Results loaded={loaded} />);
    expect(html).toContain(label);
    expect(html).toContain(median === null ? '>Unavailable<' : '>' + median + ' ms<');
    expect(html).toContain('P95 ' + (p95 === null ? 'Unavailable' : p95 + ' ms'));
  });

  it('keeps measured zero distinct from unavailable values and renders per-arm coverage', () => {
    const report = validateReport(fixture);
    expect(report.ablation.full_stack.block_rate).toBe(0);
    expect(report.false_positive_rate).toBeNull();
    expect(report.coverage.full_stack.malicious.unresolved).toBe(2);
    expect(report.scenarios).toEqual([]);
    const html = renderToStaticMarkup(<Results loaded={{ report, source: 'static' }} />);
    expect(html).toContain('Genuine measurements');
    expect(html).toContain('Run coverage');
    expect(html).toContain('>0%<');
    expect(html).toContain('>Unavailable<');
  });

  it('uses a published synthetic summary without replacing it with the bundled sample', async () => {
    const fetchReport = vi.fn().mockResolvedValue(Response.json({ ...fixture, provenance: 'synthetic' }));
    vi.stubGlobal('fetch', fetchReport);
    const loaded = await loadBenchmarkReport({ source: 'auto', staticUrl: '/benchmark-report.json', gatewayUrl: '/v1/benchmark/report' });
    expect(fetchReport).toHaveBeenCalledTimes(1);
    expect(loaded.source).toBe('static');
    expect(renderToStaticMarkup(<Results loaded={loaded} />)).toContain('Synthetic test-double results');
  });

  it('omits charts with no measurements on an unexecuted report', () => {
    const report = validateReport({ ...fixture, provenance: 'unexecuted', median_latency_ms: null, latency_sample_count: 0 });
    const html = renderToStaticMarkup(<Results loaded={{ report, source: 'static' }} />);
    expect(report.classes[0].block_rate).toBeNull();
    expect(report.ablation.full_stack.block_rate).toBeNull();
    expect(html).toContain('Unexecuted validation report');
    expect(html).not.toContain('recharts-surface');
  });

  it('rejects malformed metrics and reports without explicit provenance', () => {
    expect(() => validateReport({ ...fixture, median_latency_ms: '420' })).toThrow('median_latency_ms');
    expect(() => validateReport({ ...fixture, provenance: undefined })).toThrow('provenance');
  });
});

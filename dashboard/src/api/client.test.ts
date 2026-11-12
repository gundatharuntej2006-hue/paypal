import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe('hosted dashboard lists', () => {
  it.each([
    ['listMandates', 'mandate service'],
    ['listApprovals', 'approval inbox'],
  ] as const)('surfaces a missing route from %s', async (method, feature) => {
    vi.stubEnv('VITE_USE_STUB', 'false');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 404 })));
    const { client } = await import('./client');
    await expect(client[method]()).rejects.toThrow('The Gateway ' + feature + ' is unavailable');
  });
});

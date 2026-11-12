const [dashboardUrl] = process.argv.slice(2);
if (!dashboardUrl) {
  console.error('Usage: node dashboard/scripts/check-hosted.mjs https://<published-dashboard-host>');
  process.exit(1);
}

const dashboard = new URL(dashboardUrl);
const gateway = 'https://agentguard-gateway.onrender.com';
const intel = 'https://agentguard-intel.onrender.com';

async function request(url, options) {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(65000) });
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return response;
}

async function check(label, run) {
  try {
    await run();
    console.log('PASS ' + label);
  } catch (error) {
    console.error('FAIL ' + label + ' (' + error.message + ')');
    process.exitCode = 1;
  }
}

await Promise.all([
  check('Intel live Gemini health', async () => {
    const health = await (await request(intel + '/health')).json();
    if (health.status !== 'ok' || health.mode !== 'live' ||
        health.llm_provider !== 'gemini' || health.llm_configured !== true) {
      throw new Error('Expected Intel to report live mode and Gemini configured');
    }
  }),
  check('Gateway health and dashboard CORS', async () => {
    const response = await request(gateway + '/health', { headers: { Origin: dashboard.origin } });
    const origin = response.headers.get('access-control-allow-origin');
    if (origin !== dashboard.origin && origin !== '*') {
      throw new Error('Gateway health does not allow the dashboard origin');
    }
    if ((await response.json()).status !== 'healthy') throw new Error('Gateway is not healthy');
  }),
  ...['/', '/ledger'].map(path => check('Dashboard ' + path, async () => {
    const response = await request(new URL(path, dashboard));
    if (!(await response.text()).includes('id="root"')) throw new Error('Dashboard app shell missing');
  })),
]);

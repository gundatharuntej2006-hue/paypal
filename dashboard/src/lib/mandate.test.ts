import { describe, expect, it } from 'vitest';
import type { Mandate } from '../contracts/types';
import { canonicalize, signMandate, toBase64, validateDefinition, verifyMandate } from './mandate';

const unsigned: Omit<Mandate, 'signature'> = {
  mandate_id: 'mdt_test', principal_id: 'principal_test', agent_id: 'agent_shopper',
  task: 'Order café supplies', currency: 'USD', max_total: '60.00', max_per_txn: '40.00', step_up_over: '25.00',
  allowed_merchants: ['merchant_b', 'merchant_a'], allowed_categories: ['office_supplies'],
  valid_from: '2026-11-10T00:00:00Z', expires_at: '2026-11-11T00:00:00Z',
  nonce: 'demo-nonce', paypal_order_id: 'DEMO-ORDER',
};

describe('mandate validation', () => {
  it('accepts valid amounts, including equal caps and thresholds', () => {
    expect(validateDefinition(unsigned)).toEqual([]);
    expect(validateDefinition({ ...unsigned, max_per_txn: '60.00', step_up_over: '60.00' })).toEqual([]);
  });

  it.each(['0.00', '-1.00', '1', '1.0', '1.001', '1e2', 'NaN'])('rejects invalid amount %s', (max_total) => {
    expect(validateDefinition({ ...unsigned, max_total })).toContain('Total cap must be a positive amount with two decimal places.');
  });

  it('rejects inverted caps and thresholds using exact decimal comparisons', () => {
    expect(validateDefinition({ ...unsigned, max_total: '900719925474099.01', max_per_txn: '900719925474099.02' }))
      .toContain('Per-transaction cap must be at or below the total cap.');
    expect(validateDefinition({ ...unsigned, step_up_over: '40.01' }))
      .toContain('Step-up threshold must be at or below the per-transaction cap.');
  });

  it.each(['invalid', unsigned.valid_from, '2026-11-09T00:00:00Z'])('rejects invalid expiry %s', (expires_at) => {
    expect(validateDefinition({ ...unsigned, expires_at })).toContain('Expiry must be after the validity start.');
  });
});

describe('mandate signing', () => {
  it('canonicalizes sorted compact UTF-8 JSON, sorting allowlists and excluding the signature', () => {
    const canonical = new TextDecoder().decode(canonicalize({ ...unsigned, signature: 'ignored' }));
    expect(canonical).toBe('{"agent_id":"agent_shopper","allowed_categories":["office_supplies"],"allowed_merchants":["merchant_a","merchant_b"],"currency":"USD","expires_at":"2026-11-11T00:00:00Z","mandate_id":"mdt_test","max_per_txn":"40.00","max_total":"60.00","nonce":"demo-nonce","paypal_order_id":"DEMO-ORDER","principal_id":"principal_test","public_key":"","step_up_over":"25.00","task":"Order café supplies","valid_from":"2026-11-10T00:00:00Z"}');
  });

  it('verifies an Ed25519 signature and detects order or amount changes', async () => {
    const pair = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
    const publicKey = toBase64(await crypto.subtle.exportKey('raw', pair.publicKey));
    const signed = await signMandate(unsigned, pair.privateKey);
    expect(atob(signed.signature)).toHaveLength(64);
    expect(await verifyMandate(signed, publicKey)).toBe(true);
    expect(await verifyMandate({ ...signed, paypal_order_id: 'OTHER' }, publicKey)).toBe(false);
    expect(await verifyMandate({ ...signed, max_total: '60.01' }, publicKey)).toBe(false);
    await expect(signMandate({ ...unsigned, paypal_order_id: '' }, pair.privateKey)).rejects.toThrow('Approve a PayPal order');
  });
});

it('verifies the shared Go signing vector in the browser implementation', async () => {
  const mandate = { ...{"mandate_id":"mdt_test_vector_001","principal_id":"principal_cross_lang","agent_id":"agent_shopper_lang","task":"Procure test equipment","currency":"USD","max_total":"250.00","max_per_txn":"100.00","step_up_over":"50.00","allowed_merchants":["merchant_staples","merchant_books_123"],"allowed_categories":["books","office_supplies"],"valid_from":"2026-11-09T10:00:00Z","expires_at":"2026-11-10T10:00:00Z","nonce":"nonce_fixed_vector_999","public_key":"ebVWLo/mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ=","signature":"5wz1emL1Vy6BURPS8J1qdOdMKdrOW5hWWVXp1zwVuqy/4sgWaEeegmOzKLvF14giQimRp6PzXpES5UcOp98wAQ=="}, paypal_order_id: '' };
  expect(new TextDecoder().decode(canonicalize(mandate))).toBe("{\"agent_id\":\"agent_shopper_lang\",\"allowed_categories\":[\"books\",\"office_supplies\"],\"allowed_merchants\":[\"merchant_books_123\",\"merchant_staples\"],\"currency\":\"USD\",\"expires_at\":\"2026-11-10T10:00:00Z\",\"mandate_id\":\"mdt_test_vector_001\",\"max_per_txn\":\"100.00\",\"max_total\":\"250.00\",\"nonce\":\"nonce_fixed_vector_999\",\"principal_id\":\"principal_cross_lang\",\"public_key\":\"ebVWLo/mVPlAeLES6KmLp5AfhTrmlb7X4OORC60ElmQ=\",\"step_up_over\":\"50.00\",\"task\":\"Procure test equipment\",\"valid_from\":\"2026-11-09T10:00:00Z\"}");
  expect(await verifyMandate(mandate, mandate.public_key)).toBe(true);
});

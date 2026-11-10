// Mirrors the examples in /contracts. Render builds this app from /dashboard.
export interface Mandate {
  mandate_id: string;
  principal_id: string;
  agent_id: string;
  task: string;
  currency: string;
  max_total: string;
  max_per_txn: string;
  step_up_over: string;
  allowed_merchants: string[];
  allowed_categories: string[];
  valid_from: string;
  expires_at: string;
  nonce: string;
  paypal_order_id: string;
  public_key?: string;
  signature: string;
}

interface PurchaseProposal {
  merchant_id: string;
  amount: string;
  currency: string;
  items: { name: string; qty: number; unit_price: string }[];
}

interface SourceContext {
  ref: string;
  text: string;
}

export interface PaymentIntentRequest extends PurchaseProposal {
  mandate: Mandate;
  agent_reasoning: string;
  source_contexts: SourceContext[];
  idempotency_key: string;
}

export type Decision = 'allow' | 'step_up' | 'block';

export interface DecisionResponse {
  payment_id: string;
  decision: Decision;
  risk_score: number;
  reasons: string[];
  checks: Record<'mandate' | 'injection' | 'intent' | 'risk', string>;
  paypal?: {
    order_id?: string;
    authorization_id?: string;
    capture_id?: string;
  };
  audit_event_id: string;
}

export interface ScanContentRequest {
  contexts: SourceContext[];
}

export interface ScanContentResponse {
  injection_suspected: boolean;
  confidence: number;
  snippets: string[];
}

export interface JudgeIntentRequest {
  task: string;
  mandate: Mandate;
  proposal: PurchaseProposal;
  reasoning: string;
}

export interface JudgeIntentResponse {
  aligned: boolean;
  confidence: number;
  rationale: string;
  influenced_by_external: boolean;
}

export interface RiskScoreRequest {
  mandate: Mandate;
  proposal: PurchaseProposal;
  history: unknown[];
}

export interface RiskScoreResponse {
  score: number;
  factors: string[];
}

export type EventType =
  | 'intent.received'
  | 'mandate.verified'
  | 'mandate.failed'
  | 'scan.completed'
  | 'judge.completed'
  | 'risk.scored'
  | 'decision.made'
  | 'human.approved'
  | 'human.rejected'
  | 'paypal.order_created'
  | 'paypal.authorized'
  | 'paypal.captured'
  | 'paypal.voided'
  | 'paypal.refunded'
  | 'webhook.received'
  | 'reconciliation.mismatch'
  | 'reconciliation.completed'
  | 'payout.created'
  | 'payout.updated'
  | 'invoice.paid'
  | 'payment.step_up_token_issued'
  | 'payment.step_up_token_consumed'
  | 'paypal.capture_failed';

export interface EventEnvelope {
  id: string;
  type: EventType;
  payment_id: string;
  mandate_id: string;
  ts: string;
  payload: Record<string, unknown>;
}

import type { DecisionResponse } from '../contracts/types';

export interface Payment extends DecisionResponse {
  mandate_id: string;
  merchant_id: string;
  amount: string;
  currency: string;
  ts: string;
}

export interface ApprovalPayment extends Omit<Payment, 'risk_score' | 'checks' | 'audit_event_id'> {
  risk_score?: Payment['risk_score'];
  checks?: Payment['checks'];
  audit_event_id?: string;
  items?: { name: string; qty: number; unit_price: string }[];
  injection_rationale?: string;
  intent_rationale?: string;
  approval_url?: string;
  approval_order_id?: string;
  approval_intent?: 'authorize' | 'capture';
}

export interface BenchmarkReport {
  classes: { name: string; blocked: number; total: number }[];
  false_positive_rate: number;
  median_latency_ms: number;
  full_stack_block_rate: number;
  llm_only_block_rate: number;
}

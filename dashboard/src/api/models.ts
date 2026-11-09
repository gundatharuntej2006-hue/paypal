import type { DecisionResponse } from '../contracts/types';

export interface Payment extends DecisionResponse {
  mandate_id: string;
  merchant_id: string;
  amount: string;
  currency: string;
  ts: string;
}

export interface BenchmarkReport {
  classes: { name: string; blocked: number; total: number }[];
  false_positive_rate: number;
  median_latency_ms: number;
  full_stack_block_rate: number;
  llm_only_block_rate: number;
}

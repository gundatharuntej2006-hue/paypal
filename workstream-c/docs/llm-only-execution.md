# LLM-only model execution

`benchmark.llm_executor.run_llm_only` is a separate Python entry point requiring
an injected ShoppingModel, explicit nonsecret ModelConfiguration, and provenance.
It re-materializes the entire validated library before invoking the model only
for shopping_agent capabilities. Other cases retain their snapshots and reasons
with execution=null. Counts are derived from the materializer, not an execution
allowlist. No graph default, deterministic fallback, or external service tool is
used. Models receive the exact materialized agent_input, including original task
and evidence. Constructed counterfactuals remain input metadata only.

The typed ModelExecution retains the actual ModelDecision (or legacy Selection),
sanitized provider error code, and measured end-to-end select() latency using a
monotonic performance clock, including failures. This measures the model adapter
invocation and its local response processing, not graph browsing or payment
latency. Legacy None means no_match; unexpected return types mean invalid_response
without retaining arbitrary unsafe objects. A purchase_proposal is a reported
model outcome, not evidence that the proposal is safe or a gateway allowed it.

The report has its own report_type and does not enter the existing reporter,
dashboard, or calculate_metrics. Existing Observation permits an observed result
only with allow/step_up/block; unresolved observations cannot carry decision
latency. Therefore it cannot faithfully represent these model executions.
No model outcome is currently mapped to Observation. Before official block-rate
use, a separately reviewed mapping must define proposal evaluation, explicit
security-decision criteria, error/unresolved denominators, and latency scope.
Refusal, no_match, invalid_response, and provider_error must never automatically
become a security block. Gateway observations and model outputs remain separate.

Offline fake models use provenance=test_double and synthetic=true. Genuine mode
requires allow_provider_execution=True and a concrete AnthropicShoppingModel
without injected clients/factories, with matching configuration. Injected Python
code is trusted and is not a network sandbox; callers must supply offline doubles
for tests. No provider is constructed by this executor. Genuine CLI execution and
the full-stack executor remain disabled. No genuine execution was used to test
this implementation.

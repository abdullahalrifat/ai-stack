# Runtime efficiency calibration

AI Stack is the runtime owner of empirical model calibration. Jarvis supplies real workload categories and quality signals; AI Stack records model execution evidence and exposes calibrated route selection.

## Evidence

`RuntimeObservation` records model, workload category, success, quality, latency, input/output/cache tokens, estimated USD cost, tool failures, source, and timestamp.

Evidence uses a 30-day half-life. A model needs a minimum sample count before runtime evidence can change automatic routing, and candidates below the quality floor are rejected. This prevents one unusually good or cheap request from moving production traffic to an unsafe route.

## Workflow

1. Run the real Jarvis workload benchmark against local, auto, and cloud-first routes.
2. Record execution observations in the runtime calibration JSONL store.
3. Evaluate recent evidence using quality, correctness, tool failures, latency, and cost.
4. Use calibrated selection only when enough evidence exists; otherwise retain the existing benchmark/health routing fallback.
5. Re-run the workload suite periodically so routing adapts to model/provider changes.

The calibration store is deliberately provider-neutral. Provider-specific pricing remains in `LLM_PRICING_JSON` and model capability profiles remain in `JARVIS_MODEL_PROFILES_JSON`.

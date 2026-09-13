# Real-workload routing feedback loop

The intended production loop is:

`Jarvis workload -> AI Stack execution -> usage/quality observation -> empirical calibration -> next route decision`

Jarvis remains responsible for task-level evaluation. AI Stack remains responsible for execution telemetry and conservative model selection. `jarvis-core` remains provider-neutral and owns reusable benchmark primitives.

This separation avoids duplicate routing policy while allowing real Jarvis workloads to improve the gateway's model selection over time.

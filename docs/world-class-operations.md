# World-class operations

## Required retained evidence

- real-repository benchmark results by model/route;
- prompt-injection and secret-canary results;
- restart/network/lease/cancellation chaos results;
- target hardware soak results;
- backup/restore and disaster-recovery drill results;
- Core/CLI/Server/inference compatibility report.

## Shared-host isolation

The existing runner policy is fail-closed when required namespace isolation is unavailable. For hostile multi-tenant workloads, deploy one task per independently constrained container/VM with CPU, memory, PID and disk quotas plus explicit egress policy. Do not treat lease fencing as security isolation.

## Operational commands

```bash
bash scripts/world_class_check.sh
bash scripts/chaos_smoke.sh
bash scripts/backup_verify.sh
python scripts/compatibility_report.py
```

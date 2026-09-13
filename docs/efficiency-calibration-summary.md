# Empirical routing safeguards

Automatic runtime calibration uses real execution evidence but remains conservative:

- minimum sample count before a route can change;
- quality floor before a route can become preferred;
- recency weighting with a 30-day half-life;
- correctness and tool failures penalize utility;
- latency and estimated USD cost are included in utility;
- no provider/model self-confidence is treated as benchmark evidence;
- when evidence is insufficient, existing health and benchmark routing remains authoritative.

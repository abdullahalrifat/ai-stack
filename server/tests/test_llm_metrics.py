import pytest
from app.llm import metrics


def test_metrics_counters_and_timings():
    # exercise the metric helpers and ensure basic aggregation works
    metrics.incr('test.counter', 2)
    metrics.record_timing('test.t', 0.1)
    metrics.record_timing('test.t', 0.2)
    data = metrics.get_metrics()
    assert 'counters' in data
    assert data['counters'].get('test.counter') >= 2
    assert 'avg_timings' in data
    assert 'test.t' in data['avg_timings']
    assert data['avg_timings']['test.t'] == pytest.approx(0.15, rel=1e-2)

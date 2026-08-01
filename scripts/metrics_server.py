"""Run a simple metrics HTTP server for LLM metrics.

Usage: PYTHONPATH=agents python3 scripts/metrics_server.py
"""
from agents.app.llm.metrics_http import start_metrics_server

if __name__ == '__main__':
    srv = start_metrics_server()
    print('metrics server started on port 9181; press Ctrl-C to stop')
    try:
        while True:
            pass
    except KeyboardInterrupt:
        print('stopping')
        srv.shutdown()

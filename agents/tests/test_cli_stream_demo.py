import os
import subprocess


def test_cli_stream_demo_simulate_mode():
    script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "cli_stream_demo.py"))
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    result = subprocess.run(
        ["python3", script, "Explain quicksort briefly.", "--simulate"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert "Quicksort is a divide-and-conquer sorting algorithm" in result.stdout
    assert "[done in" in result.stderr

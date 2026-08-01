# Lightweight profiler for CLI hotpath discovery
# Run via: PYTHONPATH=cli/src python3 scripts/profile_cli.py

import time

# Import CLI package (ensure PYTHONPATH=cli/src when running)
import aistack_cli.main as main

class FakeClient:
    def list_runs(self, limit=100):
        return [
            {"requested_workspace": "/workspace/other", "conversation_id": "other", "status": "completed"},
            {"requested_workspace": "/workspace/repo", "conversation_id": "latest-local", "status": "completed"},
        ]


def workload():
    # match_workspace
    paths = ["/mnt/work/code/ai-stack", "/tmp/unrelated", "/mnt/work/code/ai-stack/cli/src"]
    choices = ["/workspace", "/workspace/ai-stack", "/workspace/other"]
    for p in paths:
        for _ in range(200):
            main.match_workspace(main.Path(p), choices)

    # resolve_project
    projects = [
        {"id": "abc-123", "name": "ai-stack", "workspace": "/workspace/ai-stack"},
        {"id": "def-456", "name": "other", "workspace": "/workspace/other"},
    ]
    for _ in range(200):
        try:
            main.resolve_project(projects, "ai-stack")
        except Exception:
            pass

    # latest_conversation_id
    client = FakeClient()
    for _ in range(200):
        try:
            main.latest_conversation_id(client, "/workspace/repo")
        except Exception:
            pass

    # parser build
    for _ in range(200):
        main.build_parser()


if __name__ == "__main__":
    import cProfile, pstats
    prof_file = "stats_cli.prof"
    pr = cProfile.Profile()
    pr.enable()
    start = time.time()
    workload()
    end = time.time()
    pr.disable()
    pr.dump_stats(prof_file)
    print(f"Workload finished in {end-start:.3f}s; profile saved to {prof_file}")
    ps = pstats.Stats(pr).sort_stats("cumulative")
    ps.strip_dirs().print_stats(20)

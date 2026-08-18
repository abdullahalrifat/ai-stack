"""Demo CLI streamer that prints incremental text from `chat_stream_text`.

Run with:
    PYTHONPATH=server python3 scripts/cli_stream_demo.py "Explain quicksort briefly."

If `openai` is not installed, this script falls back to a simulated stream so
the responsive CLI UX can still be demonstrated.
"""

import argparse
import sys
import time

try:
    from app.llm.client import chat_stream_text
    REAL_CLIENT = True
except Exception:
    chat_stream_text = None
    REAL_CLIENT = False


def simulated_stream(prompt: str):
    text = (
        "Quicksort is a divide-and-conquer sorting algorithm. It picks a pivot, "
        "partitions the array, and recursively sorts the resulting subarrays. "
        "This makes it fast on average and easy to understand."
    )
    for i in range(0, len(text), 20):
        yield text[i : i + 20]
        time.sleep(0.05)


def stream_response(prompt: str, simulate: bool = False):
    if simulate or not REAL_CLIENT:
        if not simulate:
            print(
                "[demo-mode] real LLM client unavailable; using simulated stream",
                file=sys.stderr,
            )
        yield from simulated_stream(prompt)
        return

    messages = [{"role": "user", "content": prompt}]
    try:
        yield from chat_stream_text(messages)
    except Exception as exc:
        print(
            "[warning] real stream failed, falling back to simulated stream:",
            exc,
            file=sys.stderr,
        )
        yield from simulated_stream(prompt)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Demonstrate streaming LLM output in a lightweight CLI UX."
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Prompt text for the model. If omitted, the prompt is read from stdin."
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Always use simulated streaming output instead of the real model.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.prompt:
        prompt = " ".join(args.prompt).strip()
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    else:
        try:
            prompt = input("Prompt: ").strip()
        except EOFError:
            return 1
    if not prompt:
        print("Error: no prompt provided.", file=sys.stderr)
        return 1

    start = time.time()
    try:
        for chunk in stream_response(prompt, simulate=args.simulate):
            print(chunk, end="", flush=True)
        print()
    except KeyboardInterrupt:
        print("\nStreaming cancelled.", file=sys.stderr)
        return 1
    elapsed = time.time() - start
    print(f"\n[done in {elapsed:.2f}s]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

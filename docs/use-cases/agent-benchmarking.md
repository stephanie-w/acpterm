# Multi-Agent Benchmarking & Performance Comparison

This guide explains how to use `acpterm` to benchmark and compare different ACP-compatible agents (such as `opencode`, `kiro`, `agy`, or custom binaries) on identical coding tasks.

---

## 📊 Benchmark Metrics Tracked

When running `acpterm` with `--export transcript.md`, the resulting Markdown transcript records ground-truth metadata:
* **Turn Duration**: Total execution time in seconds (`ACPTERM_DURATION_SECONDS`).
* **Token Consumption**: Total input/output context tokens used.
* **Stop Reason**: Why the turn ended (`end_turn`, `max_tokens`, `refusal`).
* **Tool Call Efficiency**: Count of tool calls made vs. redundant calls.

---

## 🚀 Running Benchmark Comparison

Run the exact same prompt across multiple agents:

```bash
# Benchmark 1: Opencode
acpterm -a opencode exec "Implement LRU Cache with O(1) ops and pytest tests" \
  --export benchmarks/opencode_lru.md

# Benchmark 2: Kiro
acpterm -a kiro exec "Implement LRU Cache with O(1) ops and pytest tests" \
  --export benchmarks/kiro_lru.md

# Benchmark 3: Antigravity (agy)
acpterm -a agy exec "Implement LRU Cache with O(1) ops and pytest tests" \
  --export benchmarks/agy_lru.md
```

---

## 🛠️ Automated Benchmark Matrix Comparison Script

Use this python script to parse benchmark Markdown exports and output a side-by-side comparison table:

### `scripts/compare_benchmarks.py`

```python
#!/usr/bin/env python3
from pathlib import Path
import re
import sys

def parse_benchmark(file_path: Path) -> dict:
    content = file_path.read_text(encoding="utf-8")
    
    duration_match = re.search(r"Duration\*\*:\s*([\d.]+)\s*seconds", content)
    context_match = re.search(r"Context Usage\*\*:\s*([\d,]+)", content)
    tools_match = len(re.findall(r"### `([^`]+)`", content))
    
    return {
        "agent": file_path.stem.replace("_lru", ""),
        "duration": float(duration_match.group(1)) if duration_match else 0.0,
        "tokens": context_match.group(1) if context_match else "N/A",
        "tool_calls": tools_match,
    }

def compare_all(benchmark_dir: Path):
    files = list(benchmark_dir.glob("*.md"))
    if not files:
        print("No benchmark transcripts found!")
        return

    results = [parse_benchmark(f) for f in files]
    
    print("=" * 60)
    print("🏆 ACP Agent Benchmark Matrix")
    print("=" * 60)
    print(f"{'Agent':<15} | {'Duration (s)':<15} | {'Context Tokens':<15} | {'Tool Calls':<10}")
    print("-" * 60)
    
    for r in sorted(results, key=lambda x: x["duration"]):
        print(f"{r['agent']:<15} | {r['duration']:<15.2f} | {r['tokens']:<15} | {r['tool_calls']:<10}")
    print("=" * 60)

if __name__ == "__main__":
    benchmark_dir = Path("benchmarks") if Path("benchmarks").exists() else Path(".")
    compare_all(benchmark_dir)
```

### Output Example:

```
============================================================
🏆 ACP Agent Benchmark Matrix
============================================================
Agent           | Duration (s)    | Context Tokens  | Tool Calls
------------------------------------------------------------
opencode        | 6.95            | 15,574          | 3         
kiro            | 9.40            | 18,210          | 5         
agy             | 11.20           | 22,400          | 4         
============================================================
```

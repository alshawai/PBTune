"""Generate BO vs PBT convergence plots from result JSON artifacts.

This helper reads one PBT result file and one BO result file, then writes a
self-contained HTML report with side-by-side convergence charts:
1) Score vs evaluation index (sample-efficiency view)
2) Score vs elapsed time (wall-clock view; PBT time is estimated per generation)
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _latest_file(pattern: str) -> Path:
    candidates = sorted(Path(".").glob(pattern))
    if not candidates:
        raise FileNotFoundError(f"No files found for pattern: {pattern}")
    return candidates[-1]


def _extract_pbt_series(pbt: Dict[str, Any]) -> Tuple[List[Dict[str, float]], float]:
    generation_history = pbt.get("generation_history", [])
    session = pbt.get("tuning_session", {})
    total_time = float(session.get("total_time_seconds", 0.0))

    series: List[Dict[str, float]] = []
    running_best = float("-inf")
    count = max(1, len(generation_history) - 1)

    for idx, gen in enumerate(generation_history, start=1):
        best_score = float(gen.get("best_score", float("nan")))
        running_best = max(running_best, best_score)

        # PBT history currently stores generation-level scores; distribute total
        # runtime proportionally across generations for a consistent wall-clock axis.
        elapsed_est = 0.0
        if total_time > 0.0:
            elapsed_est = total_time * ((idx - 1) / count)

        series.append(
            {
                "evaluation": float(idx),
                "score": best_score,
                "best_so_far": running_best,
                "elapsed_seconds": elapsed_est,
            }
        )

    return series, total_time


def _extract_bo_series(bo: Dict[str, Any]) -> Tuple[List[Dict[str, float]], float]:
    history = bo.get("evaluation_history", [])

    series: List[Dict[str, float]] = []
    running_best = float("-inf")
    elapsed = 0.0

    for idx, item in enumerate(history, start=1):
        score = float(item.get("score", float("nan")))
        eval_time = float(item.get("evaluation_time_seconds", 0.0))
        elapsed += max(eval_time, 0.0)
        running_best = max(running_best, score)

        series.append(
            {
                "evaluation": float(idx),
                "score": score,
                "best_so_far": running_best,
                "elapsed_seconds": elapsed,
            }
        )

    total_time = elapsed
    if total_time <= 0.0:
        session = bo.get("bo_session", {})
        total_time = float(session.get("total_time_seconds", 0.0))

    return series, total_time


def _build_html(
    pbt_series: List[Dict[str, float]],
    bo_series: List[Dict[str, float]],
    pbt_file: Path,
    bo_file: Path,
    pbt_total_time: float,
    bo_total_time: float,
) -> str:
    generated_at = datetime.now().isoformat(timespec="seconds")

    pbt_eval_x = [item["evaluation"] for item in pbt_series]
    pbt_best_eval_y = [item["best_so_far"] for item in pbt_series]
    pbt_time_x = [item["elapsed_seconds"] for item in pbt_series]

    bo_eval_x = [item["evaluation"] for item in bo_series]
    bo_best_eval_y = [item["best_so_far"] for item in bo_series]
    bo_time_x = [item["elapsed_seconds"] for item in bo_series]

    payload = {
        "pbtEvalX": pbt_eval_x,
        "pbtBestEvalY": pbt_best_eval_y,
        "pbtTimeX": pbt_time_x,
        "boEvalX": bo_eval_x,
        "boBestEvalY": bo_best_eval_y,
        "boTimeX": bo_time_x,
    }

    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>PBT vs BO Comparison</title>
  <script src=\"https://cdn.jsdelivr.net/npm/chart.js\"></script>
  <style>
    :root {{
      --bg: #f6f3ee;
      --card: #fffaf2;
      --ink: #1f1b16;
      --muted: #665d52;
      --pbt: #0b6e4f;
      --bo: #b24c00;
      --line: #e6dccf;
    }}
    body {{
      margin: 0;
      font-family: 'IBM Plex Sans', 'Segoe UI', sans-serif;
      color: var(--ink);
      background: radial-gradient(circle at top right, #fff5e8, var(--bg));
    }}
    .wrap {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 28px 20px 40px;
    }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    .meta {{ color: var(--muted); font-size: 14px; margin-bottom: 18px; }}
    .grid {{ display: grid; gap: 16px; grid-template-columns: 1fr; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      box-shadow: 0 8px 20px rgba(0, 0, 0, 0.05);
    }}
    .metrics {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
    .pill {{ background: #fff; border: 1px solid var(--line); border-radius: 10px; padding: 10px; }}
    .name {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    .val {{ font-size: 20px; font-weight: 700; }}
    canvas {{ width: 100%; height: 360px; }}
    @media (max-width: 740px) {{
      .metrics {{ grid-template-columns: 1fr; }}
      canvas {{ height: 300px; }}
    }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <h1>PBT vs BO Convergence</h1>
    <div class=\"meta\">Generated {generated_at}</div>

    <div class=\"card\">
      <div class=\"metrics\">
        <div class=\"pill\">
          <div class=\"name\">PBT Result File</div>
          <div class=\"val\" style=\"font-size:13px\">{pbt_file.as_posix()}</div>
        </div>
        <div class=\"pill\">
          <div class=\"name\">BO Result File</div>
          <div class=\"val\" style=\"font-size:13px\">{bo_file.as_posix()}</div>
        </div>
        <div class=\"pill\">
          <div class=\"name\">PBT Total Time (s)</div>
          <div class=\"val\">{pbt_total_time:.2f}</div>
        </div>
        <div class=\"pill\">
          <div class=\"name\">BO Total Time (s)</div>
          <div class=\"val\">{bo_total_time:.2f}</div>
        </div>
      </div>
    </div>

    <div class=\"grid\">
      <div class=\"card\">
        <h3>Best Score vs Evaluation Index</h3>
        <canvas id=\"evalChart\"></canvas>
      </div>
      <div class=\"card\">
        <h3>Best Score vs Elapsed Time (seconds)</h3>
        <canvas id=\"timeChart\"></canvas>
      </div>
    </div>
  </div>

  <script>
    const payload = {json.dumps(payload)};

    function pairXY(xs, ys) {{
      return xs.map((x, i) => ({{ x, y: ys[i] }}));
    }}

    const colors = {{ pbt: getComputedStyle(document.documentElement).getPropertyValue('--pbt').trim(), bo: getComputedStyle(document.documentElement).getPropertyValue('--bo').trim() }};

    const evalChart = new Chart(document.getElementById('evalChart'), {{
      type: 'line',
      data: {{
        datasets: [
          {{ label: 'PBT (best so far)', data: pairXY(payload.pbtEvalX, payload.pbtBestEvalY), borderColor: colors.pbt, backgroundColor: colors.pbt, tension: 0.15, pointRadius: 2 }},
          {{ label: 'BO/SMAC (best so far)', data: pairXY(payload.boEvalX, payload.boBestEvalY), borderColor: colors.bo, backgroundColor: colors.bo, tension: 0.15, pointRadius: 2 }},
        ],
      }},
      options: {{
        parsing: false,
        responsive: true,
        scales: {{
          x: {{ type: 'linear', title: {{ display: true, text: 'Evaluation Index' }} }},
          y: {{ title: {{ display: true, text: 'Best Score' }} }},
        }},
      }},
    }});

    const timeChart = new Chart(document.getElementById('timeChart'), {{
      type: 'line',
      data: {{
        datasets: [
          {{ label: 'PBT (best so far)', data: pairXY(payload.pbtTimeX, payload.pbtBestEvalY), borderColor: colors.pbt, backgroundColor: colors.pbt, tension: 0.15, pointRadius: 2 }},
          {{ label: 'BO/SMAC (best so far)', data: pairXY(payload.boTimeX, payload.boBestEvalY), borderColor: colors.bo, backgroundColor: colors.bo, tension: 0.15, pointRadius: 2 }},
        ],
      }},
      options: {{
        parsing: false,
        responsive: true,
        scales: {{
          x: {{ type: 'linear', title: {{ display: true, text: 'Elapsed Time (s)' }} }},
          y: {{ title: {{ display: true, text: 'Best Score' }} }},
        }},
      }},
    }});
  </script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot BO vs PBT convergence from JSON outputs")
    parser.add_argument("--pbt-result", type=str, help="Path to pbt_results_*.json")
    parser.add_argument("--bo-result", type=str, help="Path to bo_results_*.json")
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output HTML path (default: results/comparisons/bo_vs_pbt_<timestamp>.html)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        pbt_path = Path(args.pbt_result) if args.pbt_result else _latest_file(
            "results/**/pbt_runs/**/tuning_sessions/pbt_results_*.json"
        )
    except FileNotFoundError as exc:
        print(str(exc))
        print("Tip: run a PBT session first or pass --pbt-result <path>.")
        return 2

    try:
        bo_path = Path(args.bo_result) if args.bo_result else _latest_file(
            "results/**/bo_runs/**/bo_results_*.json"
        )
    except FileNotFoundError as exc:
        print(str(exc))
        print(
            "Tip: run BO first (python -m src.scripts.run_bo_comparison ...) "
            "or pass --bo-result <path>."
        )
        return 2

    pbt_payload = _load_json(pbt_path)
    bo_payload = _load_json(bo_path)

    pbt_series, pbt_total_time = _extract_pbt_series(pbt_payload)
    bo_series, bo_total_time = _extract_bo_series(bo_payload)

    if not pbt_series:
        raise ValueError("PBT result does not contain generation_history entries.")
    if not bo_series:
        raise ValueError("BO result does not contain evaluation_history entries.")

    if args.output:
        output_path = Path(args.output)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path("results") / "comparisons" / f"bo_vs_pbt_{stamp}.html"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    html = _build_html(
        pbt_series=pbt_series,
        bo_series=bo_series,
        pbt_file=pbt_path,
        bo_file=bo_path,
        pbt_total_time=pbt_total_time,
        bo_total_time=bo_total_time,
    )

    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(html)

    print(f"Wrote comparison report: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

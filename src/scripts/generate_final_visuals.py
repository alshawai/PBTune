import argparse
import logging
from pathlib import Path

from src.visualization.plots.convergence_curve import generate as generate_convergence
from src.visualization.plots.pareto_frontier import generate as generate_pareto
from src.visualization.plots.resource_efficiency import generate as generate_resource
from src.visualization.plots.performance_distribution import generate as generate_distribution


def main():
    parser = argparse.ArgumentParser(description="Generate final PBTune visuals including Default baseline using a multi-arm comparison report.")
    parser.add_argument("--pbt", nargs="+", required=True, help="Path(s) to PBT tuning session JSON files")
    parser.add_argument("--bo", nargs="+", required=True, help="Path(s) to BO baseline JSON files")
    parser.add_argument("--comparison-path", type=str, default=None, help="Path to the multi-arm comparison JSON report (optional)")
    parser.add_argument("--output-dir", type=str, default="analysis_final", help="Directory to save generated figures")
    parser.add_argument("--venue", type=str, default="preview", choices=["pvldb", "springer", "preview"], help="Target publication venue formatting")
    parser.add_argument(
        "--metric", type=str, default="score",
        choices=["score", "latency_p95", "latency_p99", "throughput"],
        help="Which metric to compare on the convergence curve Y-axis (default: score)"
    )

    args = parser.parse_args()

    Path(args.output_dir).mkdir(exist_ok=True, parents=True)

    try:
        metric_key = args.metric if args.metric != "score" else None
        generate_convergence(args.pbt, args.bo, args.comparison_path, args.output_dir, venue=args.venue, metric_key=metric_key)
    except NotImplementedError:
        print("Convergence curve not fully implemented")

    try:
        generate_pareto(args.pbt, args.bo, args.comparison_path, args.output_dir, venue=args.venue)
    except NotImplementedError:
        print("Pareto frontier not fully implemented")

    try:
        generate_resource(args.pbt, args.bo, args.comparison_path, args.output_dir, venue=args.venue)
    except NotImplementedError:
        print("Resource efficiency not fully implemented")

    try:
        generate_distribution(args.pbt, args.bo, args.comparison_path, args.output_dir, venue=args.venue)
    except NotImplementedError:
        print("Performance distribution not fully implemented")

    print(f"Visuals generated in {args.output_dir}")


if __name__ == "__main__":
    main()

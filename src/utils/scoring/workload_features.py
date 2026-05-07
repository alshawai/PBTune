"""Workload feature extraction for policy-aware scoring."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import numpy as np


@dataclass
class TemplateWorkloadMetadata:
    """Normalized metadata for template SQL workload feature extraction."""

    queries: list[str]
    weights: list[float]
    num_threads: int
    schema: dict[str, Any]


class WorkloadFeatureExtractor:
    """Extract static feature vectors for benchmark and template workloads."""

    _SQL_TOKEN_PATTERNS: dict[str, re.Pattern[str]] = {
        "select": re.compile(r"\bselect\b", re.IGNORECASE),
        "update": re.compile(r"\bupdate\b", re.IGNORECASE),
        "insert": re.compile(r"\binsert\b", re.IGNORECASE),
        "delete": re.compile(r"\bdelete\b", re.IGNORECASE),
        "join": re.compile(r"\bjoin\b", re.IGNORECASE),
        "group_by": re.compile(r"\bgroup\s+by\b", re.IGNORECASE),
        "order_by": re.compile(r"\border\s+by\b", re.IGNORECASE),
        "having": re.compile(r"\bhaving\b", re.IGNORECASE),
        "aggregate": re.compile(
            r"\b(count|sum|avg|min|max|stddev|variance)\s*\(", re.IGNORECASE
        ),
    }

    def extract_sysbench_features(
        self,
        *,
        script: str,
        threads: int,
        cpu_cores: int,
        table_size: int,
        tables: int,
    ) -> dict[str, float]:
        """Extract static workload priors for sysbench modes."""
        mode = script.strip().lower()
        read_ratio, write_ratio = {
            "oltp_read_only": (1.0, 0.0),
            "oltp_read_write": (0.75, 0.25),
            "oltp_write_only": (0.10, 0.90),
        }.get(mode, (0.75, 0.25))

        cpu = max(float(cpu_cores), 1.0)
        concurrency = max(float(threads), 1.0) / cpu
        total_rows = float(max(table_size, 1) * max(tables, 1))

        return {
            "read_ratio": read_ratio,
            "write_ratio": write_ratio,
            "olap_complexity": 0.15,
            "join_intensity": 0.05,
            "aggregation_intensity": 0.05,
            "sort_intensity": 0.10,
            "concurrency_pressure": float(min(concurrency, 8.0) / 8.0),
            "working_set_millions": total_rows / 1_000_000.0,
            "query_mix_entropy": 0.50,
            "tail_latency_sensitivity": 0.55,
        }

    def extract_tpch_features(
        self,
        *,
        scale_factor: float,
        warmup_passes: int,
        query_count: int = 22,
    ) -> dict[str, float]:
        """Extract static workload priors for TPC-H workloads."""
        scale = max(scale_factor, 0.01)
        warmed = 1.0 if warmup_passes > 0 else 0.0
        return {
            "read_ratio": 1.0,
            "write_ratio": 0.0,
            "olap_complexity": 0.95,
            "join_intensity": 0.90,
            "aggregation_intensity": 0.85,
            "sort_intensity": 0.80,
            "concurrency_pressure": 0.12,
            "working_set_millions": scale,
            "query_mix_entropy": min(query_count / 22.0, 1.0),
            "tail_latency_sensitivity": 0.90,
            "cache_warmup_applied": warmed,
        }

    def extract_template_features(
        self,
        *,
        metadata: TemplateWorkloadMetadata,
    ) -> dict[str, float]:
        """Extract feature vector from weighted SQL templates and schema metadata."""
        queries = metadata.queries
        if not queries:
            return {
                "read_ratio": 0.5,
                "write_ratio": 0.5,
                "olap_complexity": 0.5,
                "join_intensity": 0.0,
                "aggregation_intensity": 0.0,
                "sort_intensity": 0.0,
                "concurrency_pressure": 0.0,
                "working_set_millions": 0.0,
                "query_mix_entropy": 0.0,
                "tail_latency_sensitivity": 0.5,
            }

        weights = np.array(metadata.weights, dtype=float)
        if len(weights) != len(queries):
            weights = np.ones(len(queries), dtype=float)
        weight_sum = float(weights.sum())
        if weight_sum <= 0.0:
            weights = np.ones(len(queries), dtype=float)
            weight_sum = float(weights.sum())
        weights /= weight_sum

        op_counts = {
            "select": 0.0,
            "update": 0.0,
            "insert": 0.0,
            "delete": 0.0,
            "join": 0.0,
            "group_by": 0.0,
            "order_by": 0.0,
            "having": 0.0,
            "aggregate": 0.0,
        }

        complexities: list[float] = []
        mix_distribution: list[float] = []

        for query, weight in zip(queries, weights, strict=True):
            query_lower = query.lower()
            local_score = 0.15
            local_hits: dict[str, bool] = {}

            for token, pattern in self._SQL_TOKEN_PATTERNS.items():
                matched = bool(pattern.search(query_lower))
                local_hits[token] = matched
                if matched:
                    op_counts[token] += float(weight)

            if local_hits.get("join", False):
                local_score += 0.22
            if local_hits.get("group_by", False) or local_hits.get("aggregate", False):
                local_score += 0.18
            if local_hits.get("order_by", False):
                local_score += 0.12
            if local_hits.get("having", False):
                local_score += 0.08

            table_placeholders = len(re.findall(r"\{table\d*\}", query_lower))
            if table_placeholders > 1:
                local_score += 0.06

            complexities.append(min(local_score, 1.0))
            mix_distribution.append(float(weight))

        write_ratio = op_counts["update"] + op_counts["insert"] + op_counts["delete"]
        read_ratio = max(0.0, 1.0 - write_ratio)
        entropy = 0.0
        for p in mix_distribution:
            if p > 0.0:
                entropy += -p * float(np.log2(p))
        entropy /= float(np.log2(max(len(mix_distribution), 2)))

        schema_tables = int(metadata.schema.get("tables", 1))
        schema_table_size = int(metadata.schema.get("table_size", 100000))
        total_rows = max(schema_tables * schema_table_size, 0)

        return {
            "read_ratio": float(min(max(read_ratio, 0.0), 1.0)),
            "write_ratio": float(min(max(write_ratio, 0.0), 1.0)),
            "olap_complexity": float(np.mean(complexities) if complexities else 0.0),
            "join_intensity": float(min(op_counts["join"], 1.0)),
            "aggregation_intensity": float(
                min(op_counts["aggregate"] + op_counts["group_by"], 1.0)
            ),
            "sort_intensity": float(min(op_counts["order_by"], 1.0)),
            "concurrency_pressure": float(min(metadata.num_threads / 16.0, 1.0)),
            "working_set_millions": float(total_rows / 1_000_000.0),
            "query_mix_entropy": float(min(max(entropy, 0.0), 1.0)),
            "tail_latency_sensitivity": float(
                min(
                    1.0,
                    0.45
                    + 0.25 * min(op_counts["join"], 1.0)
                    + 0.20 * min(op_counts["order_by"], 1.0)
                    + 0.10 * min(op_counts["having"], 1.0),
                )
            ),
        }

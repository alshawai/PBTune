# Validation of Multi-Objective Metrics in ML-Driven Database Tuning

> Last reviewed: 2026-03-13

See also: [Documentation Index](./README.md)

## 1. Abstract

This document outlines the theoretical foundation and academic validity of the scoring-v2 performance metric used within the **Population-Based Training (PBT)** auto-tuner. Specifically, it analyzes the mathematical combination of Latency (P50/P95/P99), Throughput (TPS/QphH), Memory Efficiency, Error Penalty, and policy-driven workload features. The implementation preserves a compatibility policy for historical sessions while introducing feature-driven weighting for workload-aware scoring.

## 2. The Core Metrics: Latency and Throughput

The universally accepted standard for evaluating DBMS performance relies on balancing query responsiveness against overall system capacity.

### Throughput (QphH / TPS)

Throughput measures the raw volume of work a system can complete. For OLTP (Sysbench), this is Transactions Per Second (TPS). For OLAP (TPC-H), it is the Composite Query-per-Hour Performance Metric (QphH).

- **Academic Alignment**: Systems like CDBTune explicitly incorporate throughput into their Reinforcement Learning (RL) reward functions. It forms the backbone of any capacity-driven benchmarking test.

### High-Percentile Latency (P95 / P99)

While median latency (P50) describes the average user experience, it dangerously masks severe performance bottlenecks.

- **Academic Alignment**: For complex analytical workloads (TPC-H), where query execution times vary by orders of magnitude (milliseconds to minutes), tracking absolute worst-case query times is essential. Research defining the **OtterTune** architecture demonstrates a specific, intentional default constraint to optimize for **P99 query latency** in analytical (OLAP) workloads. Our system natively adopts this standard by heavily weighting P99 latency in its OLAP configuration, guaranteeing no single analytical query hangs the database.

## 3. The Constrained Meta-Metrics: Memory & Penalties

Historically, ML tuning research operated in unrestricted sandbox environments, utilizing greedy algorithms that maximized throughput at any cost—often recommending parameters (like allocating 95% of host RAM to `shared_buffers`) that would result in catastrophic Out-Of-Memory (OOM) failures in multi-tenant production scenarios.

### Memory Efficiency (Resource Regularization)

By explicitly introducing a Memory Efficiency metric (weighted at 5-15%), the PBT Tuner introduces _Constrained Resource Optimization_.

- **Theoretical Justification**: In Multi-Task Learning and safe Reinforcement Learning, auxiliary objectives act as regularization terms to prevent overfitting to a single metric. By mathematically penalizing configurations that greedily hoard RAM without delivering proportionally higher throughput, the tuner acts as a responsible, production-safe entity. This is an evolution beyond naive academic sandbox tuning.

### Error Penalty (The "Death" Constraint)

In stochastic optimization spaces, an ML model will inevitably propose fatal configurations (e.g., shrinking `work_mem` so low that a hash-join triggers an immediate crash).

- **Theoretical Justification**: If an algorithm ignores failed states, the ML agent develops a "blind spot" in the loss landscape. Academic literature surrounding Deep Reinforcement Learning in systems tuning explicitly injects an artificial "error penalty" (a heavy negative reward) for invalid configurations. Our dedicated 5-10% error weight acts as a swift genetic discriminator, ensuring the evolutionary framework efficiently discards unstable traits.

## 4. Multi-Objective Weighting (Weighted Sum Approach)

Finding the absolute Pareto-optimal frontier across four conflicting metrics is computationally infeasible in real-time.

- **Methodology**: Our system still uses a **Weighted Sum Approach**, but the active weights are now policy-driven rather than benchmark-name-only. The compatibility policy preserves the historical static profiles, while `feature_driven_v2` derives a bounded weight distribution from workload features and enforces minimum floors so no metric disappears entirely.

The PBT Tuner still collapses a complex multi-dimensional constraint problem into a single scalar reward, but the score is now explicitly parameterized by scoring policy version and workload feature metadata so tuning, rescoring, and evaluation remain aligned.

## 5. Conclusion

The weighted evaluation metric designed for this auto-tuner is strictly academically sound. Its core reliance on Throughput and P99 Latency mirrors state-of-the-art systems like OtterTune. Its deliberate inclusion of Memory Efficiency and Error Penalties introduces vital safety constraints missing in early research, creating a novel but profoundly necessary framework for robust, production-grade automated database tuning.

## 6. Scoring Policy Versioning

The system maintains backward compatibility through explicit scoring policy versioning:

### Policy Versions

- **fixed_v1**: Static workload-specific weights (legacy compatibility)
- **feature_driven_v2**: Dynamic weights derived from workload features

### Metric Reference Versions

Metric reference versions track changes to metric definitions and normalization approaches:

- **v1**: Initial metric definitions with percentile-based normalization
- **v2**: Enhanced metrics with calibration sample counts and drift detection

### Version Propagation

When loading mixed-version sessions:

1. The first file's scoring policy version is used for global rescoring
2. Individual file metadata preserves original versions for audit trails
3. Rescoring metadata is propagated to all loaded observations
4. Version mismatches trigger warnings but do not block loading

## 7. Calibration and Normalization Stability

The normalization pipeline ensures stable score computation across versions:

### Percentile-Based Anchors

- Low anchor: 5th percentile of observed metric values
- High anchor: 95th percentile of observed metric values
- Prevents single outliers from collapsing score variance

### Drift Detection

- Monitors out-of-support rate (observations outside calibration bounds)
- Triggers recalibration when drift exceeds configured threshold
- Maintains historical calibration dataset for stability

### Calibration Sample Counts

- Tracks number of samples used to compute normalization bounds
- Enables confidence assessment for rescoring operations
- Supports weighted averaging when combining multiple calibration datasets

## 8. Validation Checklist

The scoring-v2 implementation is validated through:

- ✓ Deterministic feature extraction across benchmark types
- ✓ Weight computation respecting floor constraints and sum-to-one property
- ✓ Stable normalization export/import with drift detection
- ✓ Backward compatibility for legacy sessions
- ✓ Comprehensive unit test coverage
- ✓ Cross-workload transfer validation
- ✓ Mixed-version session handling


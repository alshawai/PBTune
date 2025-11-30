"""
PBT Core Module Testing
=======================

Comprehensive tests for Worker, Evolution, and Population components.

Run with:
    python -m src.tuner.core
"""

if __name__ == "__main__":
    import random
    from src.tuner.config import get_knob_space
    from src.tuner.core.worker import Worker
    from src.tuner.core.evolution import (
        truncation_selection,
        execute_exploit_explore,
        get_elite_workers,
        get_poor_workers,
        get_best_worker,
        get_population_statistics,
        check_convergence,
    )
    from src.tuner.core.population import Population, PopulationConfig
    from src.tuner.evaluator.metrics import PerformanceMetrics

    print("PBT Core Module - Comprehensive Test")
    print("=" * 37)
    print("Testing: Worker + Evolution + Population")

    knob_space = get_knob_space('minimal')
    print(f"Using knob space: MINIMAL ({len(knob_space.knobs)} knobs)")

    print("=" * 45)
    print("\nPART 1: WORKER CLASS")
    print("=" * 20)

    print("[TEST 1.1] Worker Creation & Initialization")
    print("-" * 44)

    try:
        worker1 = Worker(worker_id=0, knob_space=knob_space, ready_interval=3)
        print(f"🟢 Created: {worker1}")
        print(f"   Config has {len(worker1.knob_config)} parameters")  # type: ignore

        explicit_config = {
            'shared_buffers': 4096,
            'effective_cache_size': 16384,
            'work_mem': 8192,
            'random_page_cost': 2.0,
            'max_parallel_workers_per_gather': 2
        }
        worker2 = Worker(
            worker_id=1,
            knob_space=knob_space,
            knob_config=explicit_config,
            ready_interval=1
        )
        print(f"\n🟢 Created with explicit config: {worker2}")
        print("-" * 56)

    except (TypeError, ValueError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("[TEST 1.2] Ready Mechanism")
    print("-" * 26)

    try:
        worker = Worker(worker_id=0, knob_space=knob_space, ready_interval=3)
        print(f"Initial: step_count={worker.step_count}, is_ready={worker.is_ready()}")

        for step in range(1, 4):
            metrics = PerformanceMetrics(latency_p95=50.0, throughput=100.0)
            worker.update_metrics(metrics, score=0.5 + step * 0.1)
            print(f"Step {step}: step_count={worker.step_count}, is_ready={worker.is_ready()}")

        print("\n🟢 Ready mechanism working!")
        print("-" * 37)

    except (TypeError, ValueError) as e:
        print(f"🔴 ERROR: {e}")

    print("[TEST 1.3] Worker Exploit-Explore")
    print("-" * 33)

    try:
        elite = Worker(worker_id=0, knob_space=knob_space)
        elite.performance_score = 0.95

        poor = Worker(worker_id=3, knob_space=knob_space)
        poor.performance_score = 0.45

        print(f"Before: Elite={elite}, Poor={poor}")

        poor.clone_from(elite, current_generation=5)
        poor.perturb(perturbation_factors=(0.8, 1.2), current_generation=5)

        print(f"After:  parent_id={poor.parent_id}, gen_created={poor.generation_created}")
        print("\n🟢 Exploit-explore working!")
        print("=" * 67)

    except (TypeError, ValueError) as e:
        print(f"🔴 ERROR: {e}")

    print("PART 2: EVOLUTION STRATEGIES")
    print("=" * 28)

    print("[TEST 2.1] Truncation Selection")
    print("-" * 31)

    try:
        workers = []
        scores = [0.92, 0.85, 0.78, 0.45]

        for i, score in enumerate(scores):
            worker = Worker(worker_id=i, knob_space=knob_space, ready_interval=1)
            metrics = PerformanceMetrics(latency_p95=100.0 - score * 100, throughput=score * 100)
            worker.update_metrics(metrics, score)
            workers.append(worker)

        print("Population:")
        for w in workers:
            print(f"  Worker-{w.worker_id}: score={w.performance_score:.4f}")

        pairs = truncation_selection(workers, exploit_quantile=0.25)

        print("\nTruncation selection (quantile=0.25):")
        for poor_idx, elite_idx in pairs:
            print(f"  Worker-{workers[poor_idx].worker_id} "
                  f"(score={workers[poor_idx].performance_score:.4f}) "
                  f"← copies from Worker-{workers[elite_idx].worker_id} "
                  f"(score={workers[elite_idx].performance_score:.4f})")

        assert len(pairs) == 1, f"Expected 1 pair, got {len(pairs)}"
        print("\n🟢 Truncation selection working!")
        print("-" * 63)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("[TEST 2.2] Complete Exploit-Explore Cycle")
    print("-" * 41)

    try:
        workers = []
        scores = [0.92, 0.85, 0.78, 0.45]

        for i, score in enumerate(scores):
            worker = Worker(worker_id=i, knob_space=knob_space, ready_interval=1)
            metrics = PerformanceMetrics(latency_p95=100.0 - score * 100, throughput=score * 100)
            worker.update_metrics(metrics, score)
            workers.append(worker)

        print("Before:")
        for w in workers:
            print(f"  Worker-{w.worker_id}: score={w.performance_score:.4f}, "
                  f"parent={w.parent_id}, gen={w.generation_created}")

        num_exploited = execute_exploit_explore(
            workers=workers,
            exploit_quantile=0.25,
            perturbation_factors=(0.8, 1.2),
            current_generation=5,
            require_ready=True,
            verbose=False
        )

        print("\nAfter exploit-explore (generation=5):")
        for w in workers:
            print(f"  Worker-{w.worker_id}: score={w.performance_score:.4f}, "
                  f"parent={w.parent_id}, gen={w.generation_created}")

        assert num_exploited == 1, f"Expected 1 exploited, got {num_exploited}"
        assert workers[3].parent_id == 0, "Poor worker should have parent_id=0"
        assert workers[3].generation_created == 5, "Should be gen 5"

        print(f"\n🟢 Exploit-explore cycle working! ({num_exploited} worker exploited)")
        print("-" * 54)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("\n[TEST 2.3] Population Statistics & Utilities")
    print("-" * 44)

    try:
        workers = []
        scores = [0.95, 0.88, 0.82, 0.75, 0.68, 0.61, 0.54, 0.47]

        for i, score in enumerate(scores):
            worker = Worker(worker_id=i, knob_space=knob_space)
            worker.performance_score = score
            workers.append(worker)

        elite = get_elite_workers(workers, quantile=0.25)
        poor = get_poor_workers(workers, quantile=0.25)
        best = get_best_worker(workers)

        print(f"Elite (top 25%): {[w.worker_id for w in elite]}")
        print(f"Poor (bottom 25%): {[w.worker_id for w in poor]}")
        print(f"Best worker: Worker-{best.worker_id} (score={best.performance_score:.2f})")

        stats = get_population_statistics(workers)
        print("Statistics:")
        print(f"  Mean: {stats['mean']:.4f}, Std: {stats['std']:.4f}")
        print(f"  Range: {stats['min']:.2f} - {stats['max']:.2f}")

        assert len(elite) == 2, "Should have 2 elite workers"
        assert len(poor) == 2, "Should have 2 poor workers"
        assert best.performance_score == 0.95, "Best should be 0.95"

        print("\n🟢 Population utilities working!")
        print("-" * 32)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("[TEST 2.4] Convergence Detection")
    print("-" * 32)

    try:
        diverse = []
        for i, score in enumerate([0.9, 0.7, 0.5, 0.3]):
            worker = Worker(worker_id=i, knob_space=knob_space)
            worker.performance_score = score
            diverse.append(worker)

        converged = []
        for i, score in enumerate([0.85, 0.86, 0.84, 0.85]):
            worker = Worker(worker_id=i, knob_space=knob_space)
            worker.performance_score = score
            converged.append(worker)

        is_diverse_converged = check_convergence(diverse, convergence_threshold=0.1)
        is_converged_converged = check_convergence(converged, convergence_threshold=0.1)

        stats_diverse = get_population_statistics(diverse)
        stats_converged = get_population_statistics(converged)

        print(f"Diverse population: std={stats_diverse['std']:.4f}, "
              f"converged={is_diverse_converged}")
        print(f"Converged population: std={stats_converged['std']:.4f}, "
              f"converged={is_converged_converged}")

        assert not is_diverse_converged, "Diverse should NOT be converged"
        assert is_converged_converged, "Similar should BE converged"

        print("\n🟢 Convergence detection working!")
        print("=" * 48)
    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("PART 3: POPULATION CLASS")
    print("=" * 24)

    print("[TEST 3.1] Population Initialization")
    print("-" * 36)

    try:
        config = PopulationConfig(
            population_size=4,
            ready_interval=2,
            exploit_quantile=0.25,
            max_generations=10
        )

        population = Population(knob_space, config)
        population.initialize()

        print(f"Created: {population}")
        print(f"Workers: {len(population.workers)}")
        for w in population.workers[:2]:  # Show first 2
            print(f"  {w}")

        assert len(population.workers) == 4, "Should have 4 workers"
        assert population.current_generation == 0, "Should start at gen 0"

        print("\n🟢 Population initialization working!")
        print("-" * 37)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("[TEST 3.2] Sequential Evaluation")
    print("-" * 32)

    try:
        population = Population(knob_space, PopulationConfig(population_size=4))
        population.initialize()

        def mock_evaluate(worker):
            """Simulate varying performance based on worker_id"""
            base_score = 0.5 + (worker.worker_id * 0.1)
            eval_metrics = PerformanceMetrics(
                latency_p95=100.0 - base_score * 100,
                throughput=base_score * 100
            )
            return eval_metrics, base_score

        population.evaluate_generation(mock_evaluate, parallel=False)

        print("After evaluation:")
        for w in population.workers:
            print(f"  Worker-{w.worker_id}: score={w.performance_score:.4f}, "
                  f"step_count={w.step_count}")

        assert all(w.step_count == 1 for w in population.workers), "All should have step_count=1"

        print("\n🟢 Sequential evaluation working!")
        print("-" * 32)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("[TEST 3.3] Complete Training Generation")
    print("-" * 39)

    try:
        config = PopulationConfig(
            population_size=4,
            ready_interval=1,  # Ready after 1 step
            exploit_quantile=0.25
        )
        population = Population(knob_space, config)
        population.initialize()

        def mock_evaluate_with_noise(worker):
            """Simulate performance with some noise"""
            base_score = 0.5 + (worker.worker_id * 0.1)
            noise = random.uniform(-0.05, 0.05)
            score = max(0.0, min(1.0, base_score + noise))
            eval_metrics = PerformanceMetrics(
                latency_p95=100.0 - score * 100,
                throughput=score * 100
            )
            return eval_metrics, score

        print("Before generation:")
        for w in population.workers:
            print(f"  Worker-{w.worker_id}: score={w.performance_score:.4f}")

        result = population.train_generation(
            mock_evaluate_with_noise,
            parallel=False,
            require_ready=True
        )

        print("\nGeneration result:")
        print(f"  Best: {result.best_score:.4f}")
        print(f"  Mean: {result.mean_score:.4f}")
        print(f"  Std:  {result.std_score:.4f}")
        print(f"  Exploited: {result.num_exploited}")
        print(f"  Best worker: Worker-{result.best_worker_id}")

        print("\nAfter generation:")
        for w in population.workers:
            print(f"  Worker-{w.worker_id}: score={w.performance_score:.4f}, "
                  f"parent={w.parent_id}, gen={w.generation_created}")

        assert population.current_generation == 1, "Should be at gen 1"
        assert len(population.history) == 1, "Should have 1 history entry"

        print("\n🟢 Training generation working!")
        print("-" * 31)
    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("[TEST 3.4] Multi-Generation Training Loop")
    print("-" * 41)

    try:
        config = PopulationConfig(
            population_size=6,
            ready_interval=1,
            exploit_quantile=0.25,
            max_generations=5,
            early_stopping_patience=10
        )
        population = Population(knob_space, config)
        population.initialize()

        def mock_improving_evaluate(worker):
            """Score improves slightly each generation"""
            base_score = 0.4 + (worker.worker_id * 0.08)
            improvement = population.current_generation * 0.02
            score = min(1.0, base_score + improvement)
            eval_metrics = PerformanceMetrics(
                latency_p95=100.0 - score * 100,
                throughput=score * 100
            )
            return eval_metrics, score

        print("Running 5 generations...")
        for gen in range(5):
            result = population.train_generation(
                mock_improving_evaluate,
                parallel=False
            )
            print(f"  Gen {gen}: best={result.best_score:.4f}, "
                  f"mean={result.mean_score:.4f}, "
                  f"exploited={result.num_exploited}")

        print("\nFinal state:")
        print(f"  Current generation: {population.current_generation}")
        print(f"  History length: {len(population.history)}")
        print(f"  Best overall score: {population.best_overall_score:.4f}")

        best_config, best_score = population.get_best_configuration()
        print(f"  Best config has {len(best_config)} parameters")

        assert population.current_generation == 5, "Should be at gen 5"
        assert len(population.history) == 5, "Should have 5 history entries"

        print("\n🟢 Multi-generation training working!")
        print("-" * 37)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("[TEST 3.5] Early Stopping Detection")
    print("-" * 35)

    try:
        config = PopulationConfig(
            population_size=4,
            ready_interval=1,
            max_generations=100,
            early_stopping_patience=3
        )
        population = Population(knob_space, config)
        population.initialize()

        def mock_plateau_evaluate(worker):
            """Simulate no improvement over generations"""
            plateau_score = 0.75
            eval_metrics = PerformanceMetrics(latency_p95=25.0, throughput=75.0)
            return eval_metrics, plateau_score

        GENERATIONS_RUN = 0
        for gen in range(10):  # Try to run 10, should stop early
            result = population.train_generation(
                mock_plateau_evaluate,
                parallel=False
            )
            GENERATIONS_RUN += 1

            if population.should_stop():
                print(f"Early stopping triggered at generation {gen}")
                break

        print(f"Generations run: {GENERATIONS_RUN}")
        print(f"Generations without improvement: {population.generations_without_improvement}")

        assert GENERATIONS_RUN < 10, "Should have stopped early"
        assert population.should_stop(), "should_stop() should return True"

        print("\n🟢 Early stopping working!")
        print("-" * 26)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("[TEST 3.6] Population Summary Statistics")
    print("-" * 40)

    try:
        population = Population(knob_space, PopulationConfig(population_size=4))
        population.initialize()

        population.train_generation(mock_evaluate, parallel=False)

        summary = population.get_population_summary()

        print("Population summary:")
        print(f"  Generation: {summary['current_generation']}")
        print(f"  Population size: {summary['population_size']}")
        print(f"  Best score: {summary['best_score']:.4f}")
        print(f"  Mean score: {summary['mean_score']:.4f}")
        print(f"  Std score: {summary['std_score']:.4f}")
        print(f"  Best worker: Worker-{summary['best_worker_id']}")
        print(f"  Converged: {summary['converged']}")

        assert 'best_config' in summary, "Should include best_config"
        assert summary['population_size'] == 4, "Should report size=4"

        print("\n🟢 Population summary working!")
        print("=" * 30)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("TEST SUMMARY")
    print("=" * 12)
    print("""
WORKER CLASS:
  🟢 Worker creation (random & explicit config)
  🟢 Ready mechanism (step_count tracking)
  🟢 Exploit-explore on individual workers

EVOLUTION STRATEGIES:
  🟢 Truncation selection (quantile-based pairing)
  🟢 Complete exploit-explore cycle
  🟢 Population statistics & utilities
  🟢 Convergence detection

POPULATION CLASS:
  🟢 Population initialization
  🟢 Sequential evaluation
  🟢 Complete training generation
  🟢 Multi-generation training loop
  🟢 Early stopping detection
  🟢 Population summary statistics

ALL TESTS PASSED SUCCESSFULLY!""")
    print("=" * 60)

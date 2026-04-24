from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.scripts.bo.interface import build_configspace
from ConfigSpace import ConfigurationSpace
from smac.runhistory.dataclasses import StatusType, TrialKey, TrialValue

from src.scripts.run_bo_comparison import BORunner
from src.scripts.bo.interface import PBTObjectiveAdapter
from src.tuner.config.knob_space import KnobDefinition, KnobScale, KnobSpace, KnobType


class FakeKnobSpace:
    def __init__(self, knob_definitions: list[KnobDefinition]):
        self.knobs = {knob.name: knob for knob in knob_definitions}

    def get_knob_names(self) -> list[str]:
        return list(self.knobs.keys())

    def __getitem__(self, knob_name: str) -> KnobDefinition:
        return self.knobs[knob_name]

    def __len__(self) -> int:
        return len(self.knobs)

    def resolve_hardware_ranges(self, *_args, **_kwargs) -> None:
        return None

    def config_to_fractions(self, config: dict[str, object]) -> dict[str, object]:
        return dict(config)


class FakeRunHistory:
    def __init__(
        self,
        entries: list[tuple[TrialKey, TrialValue]],
        ids_config: dict[int, dict[str, object]],
        costs_by_param: dict[int, float],
    ):
        self._entries = entries
        self.ids_config = ids_config
        self._costs_by_param = costs_by_param

    def items(self):
        return list(self._entries)

    def get_cost(self, configuration):
        config = dict(configuration)
        param = int(config["param"])
        return self._costs_by_param[param]


class FakeSMAC:
    def __init__(self, scenario, target_function, initial_design, overwrite):
        self.scenario = scenario
        self.target_function = target_function
        self.initial_design = initial_design
        self.overwrite = overwrite
        self.runhistory = None

    @staticmethod
    def get_initial_design(scenario, n_configs):
        return {"scenario": scenario, "n_configs": n_configs}

    def optimize(self):
        entries: list[tuple[TrialKey, TrialValue]] = []
        ids_config: dict[int, dict[str, object]] = {}

        for config_id, param in enumerate([1, 2], start=1):
            config = {"param": param}
            cost = self.target_function(config)
            ids_config[config_id] = config
            entries.append(
                (
                    TrialKey(config_id=config_id, instance=None, seed=42, budget=None),
                    TrialValue(
                        cost=cost,
                        time=1.5 + config_id,
                        cpu_time=1.0 + config_id,
                        status=StatusType.SUCCESS,
                        starttime=100.0 + config_id,
                        endtime=101.0 + config_id,
                        additional_info={"config_id": config_id},
                    ),
                )
            )

        self.runhistory = FakeRunHistory(entries, ids_config, {1: -10.0, 2: -25.0})
        return {"param": 2}


class FakeScenario:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def fake_knob_space() -> FakeKnobSpace:
    return FakeKnobSpace(
        [
            KnobDefinition(
                name="shared_buffers",
                knob_type=KnobType.INTEGER,
                min_value=128,
                max_value=1024,
                scale=KnobScale.LINEAR,
                default=256,
            ),
            KnobDefinition(
                name="random_page_cost",
                knob_type=KnobType.REAL,
                min_value=1.0,
                max_value=4.0,
                scale=KnobScale.LOG,
                default=2.0,
            ),
            KnobDefinition(
                name="synchronous_commit",
                knob_type=KnobType.ENUM,
                enum_values=["on", "off"],
                default="on",
            ),
            KnobDefinition(
                name="enable_seqscan",
                knob_type=KnobType.BOOLEAN,
                enum_values=["on", "off"],
                default="on",
            ),
        ]
    )


def test_knobspace_to_configspace_conversion_mapping(fake_knob_space):
    import ConfigSpace as cs
    config_space = build_configspace(fake_knob_space, seed=7)
    hyperparameters = {hyperparameter.name: hyperparameter for hyperparameter in config_space.get_hyperparameters()}

    integer_hp = hyperparameters["shared_buffers"]
    real_hp = hyperparameters["random_page_cost"]
    enum_hp = hyperparameters["synchronous_commit"]
    bool_hp = hyperparameters["enable_seqscan"]

    assert isinstance(integer_hp, cs.UniformIntegerHyperparameter)
    assert integer_hp.lower == 128
    assert integer_hp.upper == 1024
    assert integer_hp.log is False

    assert isinstance(real_hp, cs.UniformFloatHyperparameter)
    assert real_hp.lower == 1.0
    assert real_hp.upper == 4.0
    assert real_hp.log is True

    assert isinstance(enum_hp, cs.CategoricalHyperparameter)
    assert enum_hp.choices == ("on", "off")

    assert isinstance(bool_hp, cs.CategoricalHyperparameter)
    assert bool_hp.choices == ("on", "off")


def test_bo_loop_runs_without_live_postgres(monkeypatch, tmp_path, fake_knob_space):
    fake_evaluator = MagicMock()
    fake_evaluator.evaluate.side_effect = [
        (None, 10.0),
        (None, 25.0),
    ]

    fake_worker = SimpleNamespace(knob_config=None, update_knob_config=MagicMock(), reconfigure_and_restart=MagicMock())
    adapter = PBTObjectiveAdapter(evaluator=fake_evaluator, worker=fake_worker, logger=MagicMock())

    runner = BORunner.__new__(BORunner)
    runner.args = SimpleNamespace(
        tier="minimal",
        config="rapid",
        workload="oltp",
        workload_file=None,
        benchmark=None,
        duration=None,
        warmup=None,
        scale_factor=None,
        sysbench_tables=None,
        sysbench_table_size=None,
        max_evals=2,
        seed=42,
        initial_design_size=1,
        force_recreate_instances=False,
        cleanup_instances=False,
        skip_schema_init=True,
        verbose="INFO",
        output_dir=str(tmp_path),
    )
    runner.timestamp = "20260412_0000"
    runner.start_time = 0.0
    runner.knob_space = fake_knob_space
    runner.pbt_config = SimpleNamespace(
        scale_factor=0.01,
        sysbench_tables=2,
        sysbench_table_size=10000,
        warmup_duration=10.0,
        evaluation_duration=15.0,
        warmup_passes=0,
    )
    runner.workload_type = SimpleNamespace(value="oltp")
    runner.benchmark_name = "sysbench"
    runner.db_config = SimpleNamespace(dbname="test", user="postgres", password="")
    runner.output_dir = Path(tmp_path) / "oltp" / "bo_runs" / "minimal"
    runner.output_dir.mkdir(parents=True, exist_ok=True)
    runner.adapter = adapter
    runner.worker = fake_worker

    class FakeInstanceManager:
        def __init__(self):
            self.in_docker = False

        def setup_instances(self, num_workers, force_recreate=False):
            assert num_workers == 1
            return [SimpleNamespace(port=5440)]

        def stop_all(self):
            return True

        def cleanup(self, remove_data=False):
            return None

    runner.instance_manager = FakeInstanceManager()

    def fake_setup(self):
        self.worker = fake_worker
        self.adapter = adapter

    class FakeBOEngine:
        def __init__(self, **kwargs):
            pass
        def optimize(self):
            return {"param": 2}, 25.0, [
                {"score": 10.0, "status": str(StatusType.SUCCESS), "config": {"param": 1}},
                {"score": 25.0, "status": str(StatusType.SUCCESS), "config": {"param": 2}}
            ]

    monkeypatch.setattr(BORunner, "setup", fake_setup)
    monkeypatch.setattr("src.scripts.run_bo_comparison.BOEngine", FakeBOEngine)
    monkeypatch.setattr("src.scripts.run_bo_comparison.build_configspace", lambda knob_space, seed=None: object())
    monkeypatch.setattr("src.scripts.run_bo_comparison.get_system_info", lambda: {"cpu_model": "fake"})

    result = runner.run()

    assert result["bo_session"]["optimizer"] == "smac"
    assert result["bo_session"]["max_evaluations"] == 2
    assert result["best_configuration"]["score"] == -25.0
    assert len(result["evaluation_history"]) == 2
    assert result["evaluation_history"][0]["score"] == 10.0
    assert result["evaluation_history"][1]["score"] == 25.0
    assert result["evaluation_history"][0]["status"] == str(StatusType.SUCCESS)

    output_file = runner.output_dir / "bo_results_20260412_0000.json"
    assert output_file.exists()


def test_bo_output_json_structure_contains_expected_keys(monkeypatch, tmp_path, fake_knob_space):
    fake_evaluator = MagicMock()
    fake_evaluator.evaluate.return_value = (None, 12.5)
    fake_worker = SimpleNamespace(knob_config=None, update_knob_config=MagicMock(), reconfigure_and_restart=MagicMock())
    adapter = PBTObjectiveAdapter(evaluator=fake_evaluator, worker=fake_worker, logger=MagicMock())

    runner = BORunner.__new__(BORunner)
    runner.args = SimpleNamespace(
        tier="minimal",
        config="rapid",
        workload="oltp",
        workload_file=None,
        benchmark=None,
        duration=None,
        warmup=None,
        scale_factor=None,
        sysbench_tables=None,
        sysbench_table_size=None,
        max_evals=2,
        seed=42,
        initial_design_size=1,
        force_recreate_instances=False,
        cleanup_instances=False,
        skip_schema_init=True,
        verbose="INFO",
        output_dir=str(tmp_path),
    )
    runner.timestamp = "20260412_0001"
    runner.start_time = 0.0
    runner.knob_space = fake_knob_space
    runner.pbt_config = SimpleNamespace(
        scale_factor=0.01,
        sysbench_tables=2,
        sysbench_table_size=10000,
        warmup_duration=10.0,
        evaluation_duration=15.0,
        warmup_passes=0,
    )
    runner.workload_type = SimpleNamespace(value="oltp")
    runner.benchmark_name = "sysbench"
    runner.db_config = SimpleNamespace(dbname="test", user="postgres", password="")
    runner.output_dir = Path(tmp_path) / "oltp" / "bo_runs" / "minimal"
    runner.output_dir.mkdir(parents=True, exist_ok=True)
    runner.adapter = adapter
    runner.worker = fake_worker

    class FakeInstanceManager:
        def __init__(self):
            self.in_docker = False

        def setup_instances(self, num_workers, force_recreate=False):
            return [SimpleNamespace(port=5440)]

        def stop_all(self):
            return True

        def cleanup(self, remove_data=False):
            return None

    runner.instance_manager = FakeInstanceManager()

    class FakeRunHistorySingle(FakeRunHistory):
        pass

    class FakeBOEngineSingle:
        def __init__(self, **kwargs):
            pass
        def optimize(self):
            return {"param": 1}, 12.5, [
                {"score": -12.5, "status": str(StatusType.SUCCESS), "config": {"param": 1}}
            ]

    monkeypatch.setattr(BORunner, "setup", lambda self: None)
    monkeypatch.setattr("src.scripts.run_bo_comparison.BOEngine", FakeBOEngineSingle)
    monkeypatch.setattr("src.scripts.run_bo_comparison.build_configspace", lambda knob_space, seed=None: object())
    monkeypatch.setattr("src.scripts.run_bo_comparison.get_system_info", lambda: {"cpu_model": "fake"})

    result = runner.run()
    output_file = runner.output_dir / "bo_results_20260412_0001.json"

    assert output_file.exists()
    payload = output_file.read_text(encoding="utf-8")
    assert '"bo_session"' in payload
    assert '"best_configuration"' in payload
    assert '"evaluation_history"' in payload
    assert '"system_info"' in payload
    assert result["evaluation_history"]
    assert result["evaluation_history"][0]["config"] == {"param": 1}

"""CLI entrypoint for knob retrieval and preprocessing.

Running ``python -m src.knobs`` executes the preprocessing pipeline and
writes tiered CSV files under ``data/tuner_knobs``.
"""

from src.knobs.preprocess_knobs import preprocess_and_save_knobs


if __name__ == "__main__":
    preprocess_and_save_knobs()

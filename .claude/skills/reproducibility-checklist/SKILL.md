---
name: reproducibility-checklist
description: ML reproducibility checklist, hardware specs, data versioning, code archival. Use prior to publishing code, sending a paper for review, or finalizing an experiment setup.
---

# Reproducibility Checklist

Prior to finalizing a paper, experiment suite, or publishing an open-source repository based on research, adhere to this checklist inspired by the ML Reproducibility Checklist (Pineau et al.).

## 1. For Models and Algorithms
- [ ] **Description**: Are all hyperparameters, including those not tuned, explicitly documented?
- [ ] **Bounds**: Is the exact search space for all hyperparameters defined?
- [ ] **Metrics**: Are the evaluation metrics mathematically defined (or standard implementations cited)?
- [ ] **Baselines**: Are baselines clearly described, including whether they were re-implemented or run from official sources?

## 2. For Datasets and Workloads
- [ ] **Provenance**: Are the datasets/workloads publicly available? If not, is a synthetic equivalent provided?
- [ ] **Splits**: Are the exact train/validation/test splits documented (or the seed used to generate them)?
- [ ] **Preprocessing**: Are all data filtering, normalization, or preprocessing steps explicitly scripted?

## 3. For Experimental Setup
- [ ] **Hardware Specs**: Is the full hardware description included? (CPU architecture, core count, RAM, Disk type (NVMe/SSD/HDD), Network conditions).
- [ ] **Software Environment**: Are OS, kernel version, runtime versions (e.g., Python 3.11, CUDA 12.1), and framework versions listed?
- [ ] **Dependencies**: Is a locked `requirements.txt`, `Pipfile.lock`, or Dockerfile provided?
- [ ] **Randomness**: Are the exact random seeds used reported?
- [ ] **Statistical Significance**: Are error bars, confidence intervals, or standard deviations reported alongside central tendencies?
- [ ] **Budget**: Is the total computational budget (e.g., GPU hours, wall-clock time) reported?

## 4. For Source Code
- [ ] **Readme**: Does an entry point `README.md` exist containing clear instructions on how to install dependencies and run a minimal example?
- [ ] **Automation**: Is there a single script (e.g., `run_all.sh` or a `Makefile`) that reproduces the key results or tables in the paper?
- [ ] **Pre-trained Models/Configs**: Are final generated configurations, models, or data artifacts accessible via an open repository (e.g., Zenodo, Figshare)?

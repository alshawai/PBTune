---
name: sklearn-model-analysis
description: Scikit-learn model fitting, feature importance, cross-validation, SHAP integration. Use when training models, analyzing feature contributions, interpreting ML results, or evaluating model performance.
---

# Scikit-Learn Model Analysis

Follow these practices when fitting models, evaluating performance, and interpreting results with scikit-learn.

## 1. Data Preparation

- **Train/Test Split**: Always split before any preprocessing that learns from data (e.g., scaling, encoding). Use `train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)` for classification tasks.
- **Feature Scaling**: Use `StandardScaler` or `MinMaxScaler` via `Pipeline` to prevent data leakage. Fit the scaler on the training set only.
  ```python
  from sklearn.pipeline import Pipeline
  from sklearn.preprocessing import StandardScaler
  from sklearn.ensemble import RandomForestClassifier

  pipe = Pipeline([
      ("scaler", StandardScaler()),
      ("clf", RandomForestClassifier(n_estimators=100, random_state=42)),
  ])
  pipe.fit(X_train, y_train)
  ```
- **Missing Values**: Handle before model fitting. Use `SimpleImputer` for basic strategies or domain-specific logic. Document the imputation strategy.

## 2. Cross-Validation

- Use `cross_val_score` or `cross_validate` instead of a single train/test split for more reliable performance estimates.
- Use stratified k-fold (`StratifiedKFold`) for classification to preserve class balance across folds.
- For time-series data, use `TimeSeriesSplit` to prevent future data leaking into training.
- Report mean ± standard deviation across folds, not just the mean.
  ```python
  from sklearn.model_selection import cross_val_score

  scores = cross_val_score(pipe, X, y, cv=5, scoring="f1_macro")
  print(f"F1: {scores.mean():.3f} ± {scores.std():.3f}")
  ```

## 3. Feature Importance

### Built-in Importance (Tree-Based Models)
- Access via `model.feature_importances_` for Random Forests, Gradient Boosting, etc.
- **Caveat**: Built-in importance is biased toward high-cardinality features. Use permutation importance as a complement.

### Permutation Importance
- More reliable than built-in importance. Measures how much performance drops when a feature is randomly shuffled.
  ```python
  from sklearn.inspection import permutation_importance

  result = permutation_importance(model, X_test, y_test, n_repeats=10, random_state=42)
  ```

### SHAP Values
- SHAP provides per-prediction explanations, not just global importance.
- Use `shap.TreeExplainer` for tree-based models (fast) and `shap.KernelExplainer` for model-agnostic interpretation (slow).
  ```python
  import shap

  explainer = shap.TreeExplainer(model)
  shap_values = explainer.shap_values(X_test)
  shap.summary_plot(shap_values, X_test)
  ```
- **Beeswarm plots** show global feature importance with directionality (which feature values push predictions up or down).
- **Dependence plots** show how a single feature's SHAP value changes across its range, revealing non-linear relationships.

## 4. Model Evaluation

- **Classification**: Report precision, recall, F1-score, and confusion matrix — not just accuracy (which is misleading for imbalanced datasets).
- **Regression**: Report RMSE, MAE, and R². Visualize residuals to check for patterns.
- **Always compare against a baseline**: `DummyClassifier` or `DummyRegressor` establishes the floor performance. If your model barely beats the dummy, the features or approach may need rethinking.

## 5. Hyperparameter Tuning

- Use `RandomizedSearchCV` over `GridSearchCV` for large search spaces — it's more efficient.
- Define parameter distributions, not just grids:
  ```python
  from scipy.stats import randint, uniform

  param_dist = {
      "n_estimators": randint(50, 500),
      "max_depth": [None, 5, 10, 20],
      "min_samples_split": randint(2, 20),
  }
  ```
- Set `n_iter` based on budget and use `scoring` to match the actual optimization objective.

## 6. Serialization

- Save trained models with `joblib.dump(model, "model.pkl")` (preferred over `pickle` for NumPy-heavy objects).
- Always save the scikit-learn version alongside the model (`sklearn.__version__`). Models are not guaranteed to load across major version changes.

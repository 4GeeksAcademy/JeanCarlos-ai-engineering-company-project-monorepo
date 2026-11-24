"""scripts/evaluate_sales_model.py: the temporal cross-validation keeps chronological order
and does not leak lag/rolling features across fold boundaries.

    uv run pytest tests/pipelines/test_temporal_cv.py
"""

import numpy as np
import pandas as pd
import pytest

import evaluate_sales_model as ev
import train_sales_forecast as sf


@pytest.fixture(scope="module")
def split():
    return sf.split_train_test(sf.load_sales())


@pytest.fixture(scope="module")
def frame(split):
    return sf.build_training_frame(split.train)


@pytest.fixture
def few_trees(monkeypatch):
    monkeypatch.setattr(sf, "N_TREES", 60)


def test_temporal_cv_preserves_chronological_order(frame):
    folds = ev.temporal_folds(len(frame))
    months = frame["month"]

    assert len(folds) >= 5
    previous_val_months = []
    for train_idx, val_idx in folds:
        assert max(train_idx) < min(val_idx)  # train strictly before validation
        assert list(train_idx) == sorted(train_idx) and list(val_idx) == sorted(val_idx)  # never shuffled
        assert months.iloc[train_idx].is_monotonic_increasing and months.iloc[val_idx].is_monotonic_increasing
        assert months.iloc[train_idx].max() < months.iloc[val_idx].min()
        # no month of a later fold appears before a month of an earlier fold
        if previous_val_months:
            assert months.iloc[val_idx].min() > max(previous_val_months)
        previous_val_months = list(months.iloc[val_idx])


def test_each_fold_validates_one_full_training_year(frame):
    years = [sorted(frame["month"].iloc[val_idx].dt.year.unique()) for _, val_idx in ev.temporal_folds(len(frame))]
    assert years == [[2019], [2020], [2021], [2022], [2023]]  # 2024-2025 (test) never used


def test_folds_expand_and_start_at_the_beginning(frame):
    folds = ev.temporal_folds(len(frame))
    assert all(train_idx[0] == 0 for train_idx, _ in folds)
    assert [len(train_idx) for train_idx, _ in folds] == [21, 33, 45, 57, 69]


def test_shuffled_folds_are_rejected(monkeypatch):
    from sklearn.model_selection import KFold

    class ShuffledSplit:
        def __init__(self, n_splits, test_size):
            self.inner = KFold(n_splits=n_splits, shuffle=True, random_state=sf.SEED)

        def split(self, x):
            return self.inner.split(x)

    monkeypatch.setattr(ev, "TimeSeriesSplit", ShuffledSplit)
    with pytest.raises(AssertionError):
        ev.temporal_folds(81)


def test_lag_features_do_not_leak_across_the_fold_boundary(split, frame, few_trees):
    """Changing every real value of a validation year (and everything after it) changes
    nothing that the fold trains on or predicts: lags of validation months come from the
    history before the boundary and the model's own predictions."""
    train_idx, val_idx = ev.temporal_folds(len(frame))[2]  # validates 2021
    boundary = frame["month"].iloc[val_idx[0]]

    altered_train = split.train.copy()
    altered_train.loc[altered_train["month"] >= boundary, sf.TARGET] *= 5
    altered_split = sf.Split(altered_train, split.test, split.train_years, split.test_years)
    altered_frame = sf.build_training_frame(altered_train)

    # training rows of the fold are identical (their windows end before the boundary)
    pd.testing.assert_frame_equal(frame.iloc[train_idx], altered_frame.iloc[train_idx])

    original = ev.fit_and_forecast(split, frame, train_idx, val_idx)
    tampered = ev.fit_and_forecast(altered_split, altered_frame, train_idx, val_idx)
    assert original["train_in_sample"] == tampered["train_in_sample"]
    assert original["validation"]["rmse_usd"] != tampered["validation"]["rmse_usd"]  # only the score sees the change
    predicted_original = np.array(original["validation_error_pct_by_month"]) / 100 + 1
    predicted_tampered = (np.array(tampered["validation_error_pct_by_month"]) / 100 + 1) * 5
    assert np.allclose(predicted_original, predicted_tampered, atol=1e-3)  # same forecast, 5x the actuals


def test_cross_validation_reports_mean_and_std(split, few_trees):
    result = ev.cross_validate(split)
    validation = result["summary"]["validation"]
    for metric in ("mae_usd", "rmse_usd"):
        assert len(validation[metric]["per_fold"]) == 5
        assert validation[metric]["mean"] == pytest.approx(np.mean(validation[metric]["per_fold"]), rel=1e-3)
        assert validation[metric]["std"] > 0
    assert {"train_in_sample", "train_out_of_bag", "validation"} <= set(result["summary"])

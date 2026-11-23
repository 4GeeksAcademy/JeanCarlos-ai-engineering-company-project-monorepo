"""scripts/train_sales_forecast.py: the 8/2-year split and the absence of data leakage.

    uv run pytest tests/pipelines/test_sales_forecast.py
"""

import numpy as np
import pandas as pd
import pytest

import train_sales_forecast as sf


@pytest.fixture(scope="module")
def sales():
    return sf.load_sales()


@pytest.fixture
def few_trees(monkeypatch):
    monkeypatch.setattr(sf, "N_TREES", 60)  # same logic, faster tests


def test_dataset_matches_the_briefing(sales):
    assert list(sales.columns) == sf.COLUMNS
    assert len(sales) == 120 and (sales["region"] == "consolidated").all()
    assert sales["month"].min() == pd.Timestamp("2016-01-01") and sales["month"].max() == pd.Timestamp("2025-12-01")
    assert (sales["revenue_usd"] > 0).all() and sales.attrs["nulls_filled"] == 0


def test_split_is_first_8_years_train_and_last_2_years_test(sales):
    split = sf.split_train_test(sales)

    assert split.train_years == list(range(2016, 2024)) and split.test_years == [2024, 2025]
    assert len(split.train) == 8 * 12 and len(split.test) == 2 * 12
    assert split.train["month"].dt.year.nunique() == 8 and split.test["month"].dt.year.nunique() == 2


def test_split_has_no_overlap_and_is_chronological(sales):
    split = sf.split_train_test(sales)

    assert split.train["month"].max() < split.test["month"].min()  # every train month is before every test month
    assert set(split.train["month"]).isdisjoint(split.test["month"])
    assert len(split.train) + len(split.test) == len(sales)  # nothing dropped, nothing duplicated
    assert split.train["month"].is_monotonic_increasing and split.test["month"].is_monotonic_increasing


def test_training_rows_only_contain_training_months(sales):
    split = sf.split_train_test(sales)
    frame = sf.build_training_frame(split.train)

    assert frame["month"].max() == pd.Timestamp("2023-12-01")
    assert len(frame) == 96 - sf.MAX_LAG  # the first months only feed lags; never imputed from the future
    assert not frame[sf.FEATURES].isna().any().any()


def test_features_only_look_at_the_past(sales):
    """A month's features are identical whether or not later months exist."""
    revenue = sales["revenue_usd"].to_numpy()
    for i in (sf.MAX_LAG, 60, 95):
        month = sales["month"].iloc[i]
        truncated = sf.month_features(month, revenue[:i])
        perturbed_future = revenue.copy()
        perturbed_future[i:] *= 10  # the month itself and everything after
        assert sf.month_features(month, perturbed_future[:i]) == truncated


def test_test_actuals_never_reach_the_model(sales, few_trees):
    """Changing every 2024-2025 value changes nothing the model trains on or predicts."""
    altered = sales.copy()
    test_rows = altered["month"].dt.year >= 2024
    altered.loc[test_rows, "revenue_usd"] *= 3
    altered.loc[test_rows, "visits_count"] = 1

    original = sf.train_and_evaluate(sales)
    tampered = sf.train_and_evaluate(altered)

    for column in ("predicted_revenue_usd", "p10_usd", "p90_usd", "baseline_seasonal_naive_usd"):
        assert original["predictions"][column].equals(tampered["predictions"][column])
    assert original["metrics"]["model"]["feature_importance"] == tampered["metrics"]["model"]["feature_importance"]
    assert original["metrics"]["test"]["mse_usd2"] != tampered["metrics"]["test"]["mse_usd2"]  # only the score moves


def test_run_is_reproducible_with_the_fixed_seed(sales, few_trees):
    first, second = sf.train_and_evaluate(sales), sf.train_and_evaluate(sales)
    assert first["predictions"].equals(second["predictions"])
    assert first["metrics"] == second["metrics"]


def test_metrics_are_computed_on_the_24_test_months(sales, few_trees):
    result = sf.train_and_evaluate(sales)
    predictions = result["predictions"]
    assert list(predictions["month"]) == [f"{m:%Y-%m-%d}" for m in pd.date_range("2024-01-01", "2025-12-01", freq="MS")]
    assert np.allclose(predictions["actual_revenue_usd"], sf.split_train_test(sales).test["revenue_usd"])
    test = result["metrics"]["test"]
    assert {"mse_usd2", "r2_score", "gini_normalized"} <= set(test)
    assert "model_score_seasonal_ratio" in result["metrics"]["psi"]
    assert (predictions["p10_usd"] <= predictions["p90_usd"]).all()


def test_split_rejects_a_series_that_is_not_10_full_years(sales):
    with pytest.raises(sf.SalesDataError, match="expected 10 years"):
        sf.split_train_test(sales[sales["month"].dt.year >= 2017])
    with pytest.raises(sf.SalesDataError, match="not complete"):
        sf.split_train_test(sales[sales["month"] != pd.Timestamp("2020-06-01")])


def test_nulls_are_treated_and_gaps_rejected(tmp_path, sales):
    raw = pd.read_csv(sf.DATA_PATH)
    damaged = raw.copy()
    damaged.loc[10, "revenue_usd"] = None  # rebuilt from visits x revenue per visit
    damaged.loc[20, "avg_revenue_per_visit_usd"] = None  # interpolated between neighbours
    damaged.to_csv(tmp_path / "sales.csv", index=False)

    loaded = sf.load_sales(tmp_path / "sales.csv")
    assert loaded.attrs["nulls_filled"] == 2 and not loaded.isna().any().any()
    assert loaded.loc[10, "revenue_usd"] == pytest.approx(raw.loc[10, "revenue_usd"], rel=1e-3)

    raw.drop(index=30).to_csv(tmp_path / "gap.csv", index=False)
    with pytest.raises(sf.SalesDataError, match="missing months"):
        sf.load_sales(tmp_path / "gap.csv")


def test_metric_helpers():
    actual = np.array([1.0, 2.0, 3.0, 4.0])
    assert sf.normalized_gini(actual, actual) == pytest.approx(1.0)
    assert sf.normalized_gini(actual, -actual) < 0
    sample = np.random.default_rng(sf.SEED).normal(size=500)
    assert sf.psi(sample, sample) == pytest.approx(0.0)
    assert sf.psi(sample, sample + 3) > 0.25

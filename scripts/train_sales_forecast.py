"""Monthly revenue forecast for HealthCore (Finance RFI): Random Forest regression.

Loads data/raw/healthcore_sales.csv (the official consolidated series, never simulated),
trains on the first 8 years (2016-2023) and forecasts the last 2 (2024-2025) recursively,
without ever reading a 2024-2025 value: every lag of a test month comes from the training
history or from the model's own earlier predictions. That is the situation Finance will be
in when it asks for "the next months".

    uv run python scripts/train_sales_forecast.py

Writes data/eval/sales_forecast/{metrics.json, test_predictions.csv, sales_forecast.png}.

Why Random Forest and not XGBoost (decision criterion):
- Data size: 96 training months (84 once the first 12 are spent on lags). Boosting fits
  each tree to the previous residuals and overfits fast on that little data unless
  learning rate, depth and rounds are tuned, and 2 years of test leave no room for a
  separate tuning window. A Random Forest averages bootstrapped trees, works with
  near-default settings and has few knobs that matter.
- Explainability: the stakeholder is Finance (Sandra, Tom), and the tech lead asked for
  "no black box". A forest is "500 trees vote and we average"; feature importances and
  the spread between trees are direct to explain.
- Variability range: the spread of the 500 trees gives the prediction band for free,
  without extra quantile models.
- Training time is not a factor at this size (both train in under a second).
The known weakness of trees, that they cannot predict above the highest value they saw,
is handled by the target design: the model predicts a scale-free ratio (month revenue /
mean of the previous 12 months), and the level comes from the series itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = REPO_ROOT / "data" / "raw" / "healthcore_sales.csv"
OUTPUT_DIR = REPO_ROOT / "data" / "eval" / "sales_forecast"

# Columns exactly as documented in the HealthCore sales-forecasting BRIEFING.
COLUMNS = ["month", "revenue_usd", "visits_count", "avg_revenue_per_visit_usd", "region"]
MODEL_REGION = "consolidated"
TARGET = "revenue_usd"
TRAIN_YEARS = 8
TEST_YEARS = 2
SEED = 42  # fixed everywhere: same dataset + same code = same model and metrics
N_TREES = 500
BAND = (10, 90)  # P10-P90 variability band
PSI_BINS = 5  # quintiles: 24 test months would leave ~2 per bin with deciles
MAX_LAG = 15  # the longest look-back used by a feature (yoy_growth_3m)

FEATURES = [
    "month_of_year",
    "month_sin",
    "month_cos",
    "is_flu_season",  # Oct-Dec: +15-20 % (BRIEFING)
    "is_summer_dip",  # Jul-Aug: -12-18 % (BRIEFING)
    "lag_1_ratio",
    "lag_2_ratio",
    "lag_3_ratio",
    "lag_12_ratio",
    "rolling_mean_3_ratio",
    "rolling_std_12_ratio",
    "yoy_growth_3m",
]


class SalesDataError(ValueError):
    """The sales file does not match the contract in the BRIEFING."""


# ---------------------------------------------------------------- data preparation


def load_sales(path: Path = DATA_PATH) -> pd.DataFrame:
    """Load and validate the consolidated monthly series.

    Nulls: a missing revenue is rebuilt from visits_count * avg_revenue_per_visit_usd when
    both exist (it is their product, up to rounding); any other gap is filled by linear
    interpolation between its neighbours (never with a future-only value at the edges).
    The provided file has no nulls, so this only guards against a damaged export; the
    count of filled cells is returned in df.attrs["nulls_filled"].
    """
    df = pd.read_csv(path)
    if list(df.columns) != COLUMNS:
        raise SalesDataError(f"columns {list(df.columns)} != BRIEFING {COLUMNS}")
    df = df[df["region"].astype(str).str.strip() == MODEL_REGION].copy()
    df["month"] = pd.to_datetime(df["month"], format="%Y-%m-%d", errors="coerce")
    if df["month"].isna().any():
        raise SalesDataError("unparseable month values")
    if not (df["month"].dt.day == 1).all():
        raise SalesDataError("month must be the first day of the month (YYYY-MM-01)")
    df = df.sort_values("month").drop_duplicates("month", keep="last").reset_index(drop=True)

    numeric = ["revenue_usd", "visits_count", "avg_revenue_per_visit_usd"]
    for column in numeric:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    nulls_before = int(df[numeric].isna().sum().sum())
    rebuilt = df["revenue_usd"].isna() & df["visits_count"].notna() & df["avg_revenue_per_visit_usd"].notna()
    df.loc[rebuilt, "revenue_usd"] = df.loc[rebuilt, "visits_count"] * df.loc[rebuilt, "avg_revenue_per_visit_usd"]
    for column in numeric:
        df[column] = df[column].interpolate(method="linear", limit_area="inside")
    if df[numeric].isna().any().any():
        raise SalesDataError("nulls at the start or end of the series cannot be filled without inventing data")
    df["visits_count"] = df["visits_count"].round().astype(int)
    df.attrs["nulls_filled"] = nulls_before

    expected = pd.date_range(df["month"].min(), df["month"].max(), freq="MS")
    missing = expected.difference(df["month"])
    if len(missing):
        raise SalesDataError(f"missing months: {[m.strftime('%Y-%m') for m in missing]}")
    if (df["revenue_usd"] <= 0).any():
        raise SalesDataError("revenue_usd must be positive")
    return df


@dataclass(frozen=True)
class Split:
    train: pd.DataFrame
    test: pd.DataFrame
    train_years: list[int]
    test_years: list[int]


def split_train_test(df: pd.DataFrame, train_years: int = TRAIN_YEARS, test_years: int = TEST_YEARS) -> Split:
    """Chronological split by calendar year: the first `train_years` full years train,
    the most recent `test_years` test. Never shuffled; the two sets share no month."""
    years = sorted(df["month"].dt.year.unique())
    if len(years) != train_years + test_years:
        raise SalesDataError(f"expected {train_years + test_years} years, found {len(years)}: {years}")
    for year in years:
        if (df["month"].dt.year == year).sum() != 12:
            raise SalesDataError(f"year {year} is not complete")
    train_set, test_set = years[:train_years], years[train_years:]
    train = df[df["month"].dt.year.isin(train_set)].reset_index(drop=True)
    test = df[df["month"].dt.year.isin(test_set)].reset_index(drop=True)
    return Split(train, test, train_set, test_set)


# ---------------------------------------------------------------- features


def month_features(month: pd.Timestamp, history: np.ndarray) -> dict[str, float]:
    """Causal features of `month` from `history`: the revenue of the months strictly
    before it, oldest first. Nothing from `month` itself or later is read.

    Scaling: every revenue feature is divided by the level (mean of the last 12 months),
    so 2016 and 2025 values, ~45 % apart in USD, become comparable ratios. This is the
    scaling that matters for this series; see build_pipeline for StandardScaler.
    """
    if len(history) < MAX_LAG:
        raise ValueError(f"{month:%Y-%m} needs {MAX_LAG} months of history, got {len(history)}")
    level = history[-12:].mean()
    moy = month.month
    return {
        "month_of_year": moy,
        "month_sin": np.sin(2 * np.pi * moy / 12),
        "month_cos": np.cos(2 * np.pi * moy / 12),
        "is_flu_season": float(moy in (10, 11, 12)),
        "is_summer_dip": float(moy in (7, 8)),
        "lag_1_ratio": history[-1] / level,
        "lag_2_ratio": history[-2] / level,
        "lag_3_ratio": history[-3] / level,
        "lag_12_ratio": history[-12] / level,
        "rolling_mean_3_ratio": history[-3:].mean() / level,
        "rolling_std_12_ratio": history[-12:].std(ddof=1) / level,
        "yoy_growth_3m": history[-3:].mean() / history[-15:-12].mean() - 1,
        "_level": level,
    }


@dataclass(frozen=True)
class FeatureSet:
    """How features are built: a causal builder (month, past revenue) -> dict with `_level`,
    the columns the model uses, and the months of history the builder needs."""

    builder: Callable[[pd.Timestamp, np.ndarray], dict[str, float]]
    names: list[str]
    min_history: int


DEFAULT_FEATURE_SET = FeatureSet(month_features, FEATURES, MAX_LAG)


def build_training_frame(train: pd.DataFrame, feature_set: FeatureSet = DEFAULT_FEATURE_SET) -> pd.DataFrame:
    """One row per training month with full history. The first `min_history` months only
    feed lags (their features would be NaN), so they are dropped, not imputed.
    Target: revenue / level, a scale-free ratio the trees can learn without extrapolating."""
    revenue = train[TARGET].to_numpy()
    rows = []
    for i in range(feature_set.min_history, len(train)):
        features = feature_set.builder(train["month"].iloc[i], revenue[:i])
        features["month"] = train["month"].iloc[i]
        features["target_ratio"] = revenue[i] / features["_level"]
        features[TARGET] = revenue[i]
        rows.append(features)
    return pd.DataFrame(rows)


def build_pipeline(**model_params) -> Pipeline:
    """StandardScaler + Random Forest. Trees split on thresholds, so standardising does not
    change their predictions; it is kept, fitted on training rows only, so the feature
    matrix is on one scale for importances and for anyone swapping in a scale-sensitive
    model. Hyper-parameters are conservative for 81 rows: shallow-ish leaves, half the
    features per split to decorrelate trees. `model_params` override them (evaluation)."""
    params = {"n_estimators": N_TREES, "min_samples_leaf": 2, "max_features": 0.5,
              "oob_score": True, "random_state": SEED, "n_jobs": 1, **model_params}
    return Pipeline([("scale", StandardScaler()), ("model", RandomForestRegressor(**params))])


# ---------------------------------------------------------------- forecasting


def recursive_forecast(
    pipeline: Pipeline, history: np.ndarray, months: list[pd.Timestamp], with_paths: bool = True,
    feature_set: FeatureSet = DEFAULT_FEATURE_SET,
) -> dict[str, np.ndarray]:
    """Forecast `months` one after another, feeding each prediction back as history.

    Returns the forest forecast ("point") and one trajectory per tree ("paths", shape
    trees x months): each tree feeds back its own predictions, so the band widens with the
    horizon as errors compound, like a real 24-month plan. with_paths=False skips them
    (cross-validation only needs the point forecast).
    """
    scaler, forest = pipeline.named_steps["scale"], pipeline.named_steps["model"]

    def step(hist: np.ndarray, month: pd.Timestamp) -> tuple[np.ndarray, float]:
        features = feature_set.builder(month, hist)
        x = scaler.transform(pd.DataFrame([features])[feature_set.names])
        return x, features["_level"]

    point_hist = history.astype(float).copy()
    point = []
    for month in months:
        x, level = step(point_hist, month)
        value = forest.predict(x)[0] * level
        point.append(value)
        point_hist = np.append(point_hist, value)
    if not with_paths:
        return {"point": np.array(point)}

    paths = np.empty((len(forest.estimators_), len(months)))
    for t, tree in enumerate(forest.estimators_):
        tree_hist = history.astype(float).copy()
        for m, month in enumerate(months):
            x, level = step(tree_hist, month)
            paths[t, m] = tree.predict(x)[0] * level
            tree_hist = np.append(tree_hist, paths[t, m])
    return {"point": np.array(point), "paths": paths}


def seasonal_naive_forecast(train: pd.DataFrame, months: list[pd.Timestamp]) -> np.ndarray:
    """Baseline to beat: same month of the last training year, grown by the average
    annual growth of the training years (compounded per year of horizon)."""
    yearly = train.groupby(train["month"].dt.year)[TARGET].sum()
    growth = (yearly.iloc[-1] / yearly.iloc[0]) ** (1 / (len(yearly) - 1)) - 1
    last = train[train["month"].dt.year == yearly.index[-1]]
    last_year = pd.Series(last[TARGET].to_numpy(), index=last["month"].dt.month)
    return np.array([last_year[m.month] * (1 + growth) ** (m.year - yearly.index[-1]) for m in months])


# ---------------------------------------------------------------- metrics


def psi(expected: np.ndarray, actual: np.ndarray, bins: int = PSI_BINS) -> float:
    """Population Stability Index of `actual` (test) against `expected` (train).
    Bin edges are the quantiles of the training distribution; open-ended outer bins
    catch values outside the training range. < 0.10 stable, 0.10-0.25 moderate shift,
    > 0.25 significant shift."""
    edges = np.quantile(expected, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(expected, edges)[0] / len(expected)
    a = np.histogram(actual, edges)[0] / len(actual)
    e, a = np.clip(e, 1e-4, None), np.clip(a, 1e-4, None)
    return float(np.sum((a - e) * np.log(a / e)))


def normalized_gini(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Gini of the actual values ordered by prediction, over the Gini of the perfect order.
    1.0 = the model ranks every month exactly as reality (it puts the real low-season
    months at the bottom and the peak months at the top); 0 = random ranking."""

    def gini(a: np.ndarray, p: np.ndarray) -> float:
        order = np.lexsort((np.arange(len(p)), -p))  # highest prediction first, stable
        cumulative = np.cumsum(a[order]) / a.sum()
        return float(cumulative.sum() / len(a) - (len(a) + 1) / (2 * len(a)))

    actual, predicted = np.asarray(actual, float), np.asarray(predicted, float)
    return gini(actual, predicted) / gini(actual, actual)


def regression_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    mse = mean_squared_error(actual, predicted)
    mean_revenue = float(np.mean(actual))
    return {
        "mse_usd2": float(mse),
        "rmse_usd": float(np.sqrt(mse)),
        "rmse_pct_of_mean_monthly_revenue": float(np.sqrt(mse) / mean_revenue * 100),
        "mae_usd": float(mean_absolute_error(actual, predicted)),
        "mape_pct": float(mean_absolute_percentage_error(actual, predicted) * 100),
        "r2_score": float(r2_score(actual, predicted)),
        "gini_normalized": normalized_gini(actual, predicted),
        "bias_pct": float((np.sum(predicted) / np.sum(actual) - 1) * 100),
    }


# ---------------------------------------------------------------- run


def train_and_evaluate(df: pd.DataFrame) -> dict:
    split = split_train_test(df)
    frame = build_training_frame(split.train)
    pipeline = build_pipeline().fit(frame[FEATURES], frame["target_ratio"])
    forest = pipeline.named_steps["model"]

    months = list(split.test["month"])
    actual = split.test[TARGET].to_numpy()
    forecast = recursive_forecast(pipeline, split.train[TARGET].to_numpy(), months)
    point, paths = forecast["point"], forecast["paths"]
    low, high = np.percentile(paths, BAND, axis=0)
    baseline = seasonal_naive_forecast(split.train, months)

    fitted = pipeline.predict(frame[FEATURES]) * frame["_level"]
    oob = forest.oob_prediction_ * frame["_level"]
    residuals = actual - point
    k2_statistic, k2_p = stats.normaltest(residuals)

    test_ratio = actual / np.array([month_features(m, np.append(split.train[TARGET].to_numpy(), actual[:i]))["_level"]
                                    for i, m in enumerate(months)])
    predicted_ratio = point / np.array([month_features(m, np.append(split.train[TARGET].to_numpy(), point[:i]))["_level"]
                                        for i, m in enumerate(months)])

    metrics = {
        "dataset": {
            "path": str(DATA_PATH.relative_to(REPO_ROOT)), "rows": len(df), "region": MODEL_REGION,
            "first_month": f"{df['month'].min():%Y-%m}", "last_month": f"{df['month'].max():%Y-%m}",
            "nulls_filled": df.attrs.get("nulls_filled", 0),
        },
        "split": {
            "train_years": [int(y) for y in split.train_years], "test_years": [int(y) for y in split.test_years],
            "train_months": len(split.train), "test_months": len(split.test),
            "train_rows_after_lags": len(frame), "rows_used_only_as_lag_history": MAX_LAG,
            "forecast_mode": "recursive, 24 months ahead from 2023-12, no test actuals as input",
        },
        "model": {
            "algorithm": "RandomForestRegressor", "n_estimators": N_TREES, "min_samples_leaf": 2,
            "max_features": 0.5, "random_state": SEED, "target": "revenue_usd / mean(previous 12 months)",
            "features": FEATURES,
            "feature_importance": dict(sorted(
                zip(FEATURES, map(float, forest.feature_importances_)), key=lambda kv: -kv[1])),
        },
        "test": {
            **regression_metrics(actual, point),
            "k2_score_note": "K2 Score reported as R2 (coefficient of determination); "
                             "the D'Agostino-Pearson K2 normality test of the residuals is in residual_k2",
            "residual_k2": {"statistic": float(k2_statistic), "p_value": float(k2_p)},
            "band": f"P{BAND[0]}-P{BAND[1]} of {N_TREES} tree trajectories",
            "band_coverage_pct": float(np.mean((actual >= low) & (actual <= high)) * 100),
            "band_mean_width_pct": float(np.mean((high - low) / point) * 100),
            "mean_monthly_revenue_usd": float(actual.mean()),
            "total_revenue_usd": {"actual": float(actual.sum()), "predicted": float(point.sum())},
        },
        "psi": {
            "bins": PSI_BINS,
            "model_score_seasonal_ratio": psi(frame["target_ratio"].to_numpy(), predicted_ratio),
            "actual_seasonal_ratio": psi(frame["target_ratio"].to_numpy(), test_ratio),
            "avg_revenue_per_visit_usd": psi(split.train["avg_revenue_per_visit_usd"].to_numpy(),
                                             split.test["avg_revenue_per_visit_usd"].to_numpy()),
            "revenue_usd_raw": psi(split.train[TARGET].to_numpy(), actual),
            "us_uk_visit_mix": None,
            "us_uk_visit_mix_note": "not computable: the provided file only has the consolidated row; "
                                    "avg_revenue_per_visit_usd is the proxy for a mix change",
        },
        "baseline_seasonal_naive_test": regression_metrics(actual, baseline),
        "train_in_sample": regression_metrics(frame[TARGET].to_numpy(), fitted.to_numpy()),
        "train_out_of_bag": regression_metrics(frame[TARGET].to_numpy(), oob.to_numpy()),
    }
    predictions = pd.DataFrame({
        "month": [f"{m:%Y-%m-%d}" for m in months], "actual_revenue_usd": actual.round(2),
        "predicted_revenue_usd": point.round(2), f"p{BAND[0]}_usd": low.round(2), f"p{BAND[1]}_usd": high.round(2),
        "baseline_seasonal_naive_usd": baseline.round(2), "error_pct": ((point - actual) / actual * 100).round(2),
    })
    return {"metrics": metrics, "predictions": predictions, "split": split}


def plot_forecast(split: Split, predictions: pd.DataFrame, metrics: dict, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    surface, ink, ink2, grid, blue = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6"
    months = pd.to_datetime(predictions["month"])
    fig, ax = plt.subplots(figsize=(12, 5.6), facecolor=surface)
    ax.set_facecolor(surface)
    ax.fill_between(months, predictions[f"p{BAND[0]}_usd"], predictions[f"p{BAND[1]}_usd"],
                    color=blue, alpha=0.18, linewidth=0, label=f"Rango de variabilidad P{BAND[0]}–P{BAND[1]}")
    ax.plot(split.train["month"], split.train[TARGET], color="#a3a29c", linewidth=1.6, label="Real 2016–2023 (entrenamiento)")
    ax.plot(months, predictions["actual_revenue_usd"], color=ink, linewidth=2, label="Real 2024–2025 (prueba)")
    ax.plot(months, predictions["predicted_revenue_usd"], color=blue, linewidth=2, linestyle="--",
            label="Predicción Random Forest")
    ax.axvline(months.iloc[0] - pd.Timedelta(days=15), color=ink2, linewidth=1, linestyle=":")
    split_x, bottom = months.iloc[0] - pd.Timedelta(days=15), ax.get_ylim()[0]
    ax.text(split_x - pd.Timedelta(days=25), bottom, "entrenamiento", color=ink2, fontsize=9, va="bottom", ha="right")
    ax.text(split_x + pd.Timedelta(days=25), bottom, "prueba (no vista)", color=ink2, fontsize=9, va="bottom", ha="left")

    test = metrics["test"]
    ax.set_title("HealthCore — ingresos mensuales consolidados: predicción 2024–2025 vs. real",
                 loc="left", color=ink, fontsize=13, pad=26)
    ax.text(0, 1.02, f"MAPE {test['mape_pct']:.1f} %  ·  RMSE {test['rmse_pct_of_mean_monthly_revenue']:.1f} % del ingreso "
            f"medio  ·  R² {test['r2_score']:.2f}  ·  Gini {test['gini_normalized']:.2f}  ·  "
            f"{test['band_coverage_pct']:.0f} % de los meses reales dentro de la banda",
            transform=ax.transAxes, color=ink2, fontsize=9.5)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v / 1e6:.1f} M"))
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.grid(axis="y", color=grid, linewidth=0.8)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(grid)
    ax.tick_params(colors=ink2, length=0)
    ax.set_ylabel("Ingresos (USD)", color=ink2)
    ax.legend(loc="upper left", frameon=False, labelcolor=ink2, fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=surface)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", type=Path, default=DATA_PATH)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args(argv)

    df = load_sales(args.data)
    result = train_and_evaluate(df)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "metrics.json").write_text(json.dumps(result["metrics"], indent=2) + "\n")
    result["predictions"].to_csv(args.output_dir / "test_predictions.csv", index=False)
    plot_forecast(result["split"], result["predictions"], result["metrics"], args.output_dir / "sales_forecast.png")

    test, base = result["metrics"]["test"], result["metrics"]["baseline_seasonal_naive_test"]
    print(f"train {result['metrics']['split']['train_years']} -> test {result['metrics']['split']['test_years']}")
    print(f"MSE {test['mse_usd2']:.4g} USD² (RMSE {test['rmse_pct_of_mean_monthly_revenue']:.2f} % of mean monthly revenue)")
    print(f"MAPE {test['mape_pct']:.2f} %  R² {test['r2_score']:.3f}  Gini {test['gini_normalized']:.3f}  "
          f"PSI(score) {result['metrics']['psi']['model_score_seasonal_ratio']:.3f}  "
          f"band coverage {test['band_coverage_pct']:.0f} %")
    print(f"baseline seasonal naive: MAPE {base['mape_pct']:.2f} %  R² {base['r2_score']:.3f}")
    print(f"outputs in {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

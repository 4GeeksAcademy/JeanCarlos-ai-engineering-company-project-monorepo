"""Formal evaluation of the sales forecast model (Random Forest of train_sales_forecast.py).

Answers the staging ticket with evidence, using ONLY the 8 training years (2016-2023); the
2024-2025 test years stay untouched, exactly as in the previous project.

- Temporal cross-validation: TimeSeriesSplit, 5 folds, 12-month validation windows
  (2019, 2020, 2021, 2022, 2023), expanding training window, never shuffled.
- No lag/rolling leakage across fold boundaries: every feature row only looks at months
  before it, the scaler and the forest are fitted per fold on that fold's training rows, and
  each validation year is forecast recursively from the history that ends at the fold
  boundary, so a validation month never feeds its own (or a later month's) features with
  real values.
- MAE and RMSE on training (in-sample and out-of-bag) and validation, mean +- std over folds.
- Learning curve: training vs validation RMSE as the training window grows.
- Two controlled experiments for the diagnosis: more regularisation (variance check) and
  the proposed feature change (bias check), both on the same folds.

    uv run python scripts/evaluate_sales_model.py

Writes data/eval/learning_curve.png, data/eval/cv_folds.png and
data/eval/sales_model_evaluation.json. The written diagnosis is data/eval/evaluation_report.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import TimeSeriesSplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_sales_forecast as sf  # noqa: E402

EVAL_DIR = sf.REPO_ROOT / "data" / "eval"
N_SPLITS = 5
VALIDATION_MONTHS = 12  # one full seasonal cycle per fold
PRIMARY_METRIC = "rmse_usd"  # justified in data/eval/evaluation_report.md
LEARNING_CURVE_FOLDS = (3, 4)  # 0-based: validate 2022 and 2023, the folds with >= 57 training rows
LEARNING_CURVE_SIZES = (12, 21, 30, 39, 48, 57)  # most recent N months before the fold boundary

# Proposed corrective action (bias): the BRIEFING growth alternates every year between
# X+Y and X-Y, so the growth of the previous 12 months says which kind of year comes next.
# The current model only sees 3-month growth, which mostly says "continue like last year".
PROPOSED_FEATURES = ["month_of_year", "month_sin", "month_cos", "is_flu_season", "is_summer_dip",
                     "lag_12_ratio", "yoy_growth_12m"]


def proposed_month_features(month: pd.Timestamp, history: np.ndarray) -> dict[str, float]:
    features = sf.month_features(month, history)
    features["yoy_growth_12m"] = history[-12:].mean() / history[-24:-12].mean() - 1
    return features


PROPOSED_FEATURE_SET = sf.FeatureSet(proposed_month_features, PROPOSED_FEATURES, 24)


# ---------------------------------------------------------------- temporal folds


def temporal_folds(n_rows: int, n_splits: int = N_SPLITS, test_size: int = VALIDATION_MONTHS):
    """TimeSeriesSplit indices, checked: each fold trains on a contiguous past block and
    validates on the block right after it; validation windows advance in time and never
    overlap. Raises if any of that is violated (it would mean shuffled or leaking folds)."""
    folds = list(TimeSeriesSplit(n_splits=n_splits, test_size=test_size).split(np.arange(n_rows)))
    previous_val_end = -1
    for train_idx, val_idx in folds:
        assert np.array_equal(train_idx, np.arange(train_idx[0], train_idx[-1] + 1)), "train block not contiguous"
        assert np.array_equal(val_idx, np.arange(val_idx[0], val_idx[-1] + 1)), "validation block not contiguous"
        assert train_idx[-1] < val_idx[0], "training rows after validation rows"
        assert val_idx[0] > previous_val_end, "validation windows overlap or go back in time"
        previous_val_end = val_idx[-1]
    return folds


def assert_no_feature_leakage(frame: pd.DataFrame, train_idx: np.ndarray, val_idx: np.ndarray, cut: int,
                              train_months: list[pd.Timestamp]) -> None:
    """The fold's training rows are all earlier than its first validation month, and the
    recursive forecast history stops exactly at the fold boundary."""
    first_val = frame["month"].iloc[val_idx[0]]
    assert frame["month"].iloc[train_idx].max() < first_val
    assert train_months[cut] == first_val and train_months[cut - 1] < first_val


def _metrics(actual, predicted) -> dict[str, float]:
    actual, predicted = np.asarray(actual, float), np.asarray(predicted, float)
    return {
        "mae_usd": float(mean_absolute_error(actual, predicted)),
        "rmse_usd": float(np.sqrt(mean_squared_error(actual, predicted))),
        "mape_pct": float(np.mean(np.abs(predicted - actual) / actual) * 100),
        "bias_pct": float((predicted.sum() / actual.sum() - 1) * 100),
    }


def fit_and_forecast(split: sf.Split, frame: pd.DataFrame, train_idx: np.ndarray, val_idx: np.ndarray,
                     feature_set: sf.FeatureSet = sf.DEFAULT_FEATURE_SET, **model_params) -> dict:
    """Fit on the fold's training rows; forecast its validation window recursively."""
    train_months = list(split.train["month"])
    revenue = split.train[sf.TARGET].to_numpy()
    val_months = list(frame["month"].iloc[val_idx])
    cut = train_months.index(val_months[0])
    assert_no_feature_leakage(frame, train_idx, val_idx, cut, train_months)

    rows = frame.iloc[train_idx]
    pipeline = sf.build_pipeline(**model_params).fit(rows[feature_set.names], rows["target_ratio"])
    forest = pipeline.named_steps["model"]
    predicted = sf.recursive_forecast(pipeline, revenue[:cut], val_months, with_paths=False,
                                      feature_set=feature_set)["point"]
    return {
        "validation_year": int(val_months[0].year),
        "train_rows": int(len(train_idx)),
        "train_first_month": f"{rows['month'].iloc[0]:%Y-%m}",
        "train_last_month": f"{rows['month'].iloc[-1]:%Y-%m}",
        "train_in_sample": _metrics(rows[sf.TARGET], pipeline.predict(rows[feature_set.names]) * rows["_level"]),
        "train_out_of_bag": _metrics(rows[sf.TARGET], forest.oob_prediction_ * rows["_level"]),
        "validation": _metrics(frame[sf.TARGET].iloc[val_idx], predicted),
        "validation_error_pct_by_month": [
            round(float(e), 2) for e in (predicted - frame[sf.TARGET].iloc[val_idx].to_numpy())
            / frame[sf.TARGET].iloc[val_idx].to_numpy() * 100
        ],
        "baseline_seasonal_naive": _metrics(frame[sf.TARGET].iloc[val_idx],
                                            sf.seasonal_naive_forecast(split.train.iloc[:cut], val_months)),
    }


def summarize(folds: list[dict], section: str) -> dict[str, dict[str, float]]:
    return {
        metric: {"mean": float(np.mean(values)), "std": float(np.std(values)),
                 "per_fold": [round(float(v), 2) for v in values]}
        for metric in ("mae_usd", "rmse_usd", "mape_pct", "bias_pct")
        for values in [[fold[section][metric] for fold in folds]]
    }


def cross_validate(split: sf.Split, feature_set: sf.FeatureSet = sf.DEFAULT_FEATURE_SET, **model_params) -> dict:
    frame = sf.build_training_frame(split.train, feature_set)
    folds = [fit_and_forecast(split, frame, tr, va, feature_set, **model_params)
             for tr, va in temporal_folds(len(frame))]
    return {
        "folds": folds,
        "summary": {section: summarize(folds, section)
                    for section in ("train_in_sample", "train_out_of_bag", "validation", "baseline_seasonal_naive")},
    }


def learning_curve(split: sf.Split) -> list[dict]:
    """Train on the most recent N months before the boundary of the 2022 and 2023 folds
    (same validation years at every size, so only the training size changes)."""
    frame = sf.build_training_frame(split.train)
    folds = temporal_folds(len(frame))
    points = []
    for size in LEARNING_CURVE_SIZES:
        runs = []
        for k in LEARNING_CURVE_FOLDS:
            train_idx, val_idx = folds[k]
            runs.append(fit_and_forecast(split, frame, train_idx[-size:], val_idx))
        point = {"train_rows": size}
        for section in ("train_in_sample", "train_out_of_bag", "validation"):
            values = [run[section]["rmse_usd"] for run in runs]
            point[section] = {"rmse_mean": float(np.mean(values)), "rmse_std": float(np.std(values))}
        points.append(point)
    return points


def noise_floor(split: sf.Split) -> dict[str, float]:
    """Hindsight reference: residual of log revenue after removing a separate level per year
    and a seasonal effect per month (fitted on all 8 training years at once). It knows each
    year's level after the fact, so no forecast can beat it; it is the month-to-month noise
    that is irreducible for this series."""
    train = split.train
    years = pd.get_dummies(train["month"].dt.year.astype(str))
    months = pd.get_dummies(train["month"].dt.month.astype(str)).iloc[:, 1:]
    design = pd.concat([years, months], axis=1).to_numpy(float)
    y = np.log(train[sf.TARGET].to_numpy())
    coefficients = np.linalg.lstsq(design, y, rcond=None)[0]
    residual_std = float(np.sqrt(np.sum((y - design @ coefficients) ** 2) / (len(y) - design.shape[1])))
    return {"residual_std_pct": residual_std * 100,
            "rmse_usd_at_mean_level": residual_std * float(train[sf.TARGET].mean())}


# ---------------------------------------------------------------- figures


def _style(ax, ink2: str, grid: str) -> None:
    ax.grid(axis="y", color=grid, linewidth=0.8)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(grid)
    ax.tick_params(colors=ink2, length=0)


def plot_learning_curve(points: list[dict], floor: dict, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    surface, ink, ink2, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
    train_c, oob_c, val_c = "#2a78d6", "#1baf7a", "#eb6834"
    sizes = [p["train_rows"] for p in points]
    fig, ax = plt.subplots(figsize=(10, 5.6), facecolor=surface)
    ax.set_facecolor(surface)
    for key, color, label, style in (
        ("validation", val_c, "Validación (pronóstico recursivo de 12 meses)", "-"),
        ("train_out_of_bag", oob_c, "Entrenamiento fuera de bolsa (OOB)", "--"),
        ("train_in_sample", train_c, "Entrenamiento (dentro de la muestra)", "-"),
    ):
        mean = np.array([p[key]["rmse_mean"] for p in points]) / 1e3
        std = np.array([p[key]["rmse_std"] for p in points]) / 1e3
        ax.fill_between(sizes, mean - std, mean + std, color=color, alpha=0.12, linewidth=0)
        ax.plot(sizes, mean, color=color, linewidth=2, linestyle=style, marker="o", markersize=6)
        ax.text(sizes[-1] + 1.2, mean[-1], label.split(" (")[0], color=ink2, fontsize=9, va="center")
    ax.axhline(floor["rmse_usd_at_mean_level"] / 1e3, color=ink2, linewidth=1, linestyle=":")
    ax.text(sizes[0], floor["rmse_usd_at_mean_level"] / 1e3 + 3, "ruido irreducible (referencia a posteriori)",
            color=ink2, fontsize=8.5, va="bottom")
    ax.set_title("Curva de aprendizaje — Random Forest de ingresos mensuales (HealthCore)",
                 loc="left", color=ink, fontsize=12.5, pad=24)
    ax.text(0, 1.02, "RMSE en miles de USD · validación = años 2022 y 2023 (media ± desv. estándar) · "
            "entrenamiento = los N meses anteriores", transform=ax.transAxes, color=ink2, fontsize=9)
    ax.set_xlabel("Meses de entrenamiento", color=ink2)
    ax.set_ylabel("RMSE (miles de USD)", color=ink2)
    ax.set_xticks(sizes)
    ax.set_xlim(sizes[0] - 2, sizes[-1] + 16)
    ax.set_ylim(0, None)
    _style(ax, ink2, grid)
    from matplotlib.lines import Line2D

    ax.legend(handles=[Line2D([], [], color=c, linewidth=2, linestyle=s, marker="o", label=l) for c, s, l in (
        (val_c, "-", "Validación (pronóstico recursivo de 12 meses)"),
        (oob_c, "--", "Entrenamiento fuera de bolsa (OOB)"),
        (train_c, "-", "Entrenamiento (dentro de la muestra)"))],
        loc="upper right", frameon=False, labelcolor=ink2, fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=surface)
    plt.close(fig)


def plot_cv_folds(current: dict, regularized: dict, proposed: dict, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    surface, ink, ink2, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
    series = (
        ("Modelo actual", current["folds"], "validation", "#2a78d6"),
        ("Más regularización (min_samples_leaf=5)", regularized["folds"], "validation", "#eb6834"),
        ("Propuesto: crecimiento 12 m", proposed["folds"], "validation", "#1baf7a"),
        ("Línea base estacional", current["folds"], "baseline_seasonal_naive", "#a3a29c"),
    )
    years = [f["validation_year"] for f in current["folds"]]
    x = np.arange(len(years))
    width = 0.2
    fig, ax = plt.subplots(figsize=(10, 5.2), facecolor=surface)
    ax.set_facecolor(surface)
    for i, (label, folds, section, color) in enumerate(series):
        values = [f[section]["rmse_usd"] / 1e3 for f in folds]
        ax.bar(x + (i - 1.5) * width, values, width * 0.92, color=color, label=label)
    ax.set_xticks(x, [f"{y}*" if i == 0 else str(y) for i, y in enumerate(years)])
    ax.text(0, -0.13, f"* En {years[0]} el modelo propuesto entrena con solo {proposed['folds'][0]['train_rows']} meses: "
            "necesita 24 meses de historia y los datos empiezan en 2016.", transform=ax.transAxes, color=ink2, fontsize=8.5)
    ax.set_title("Validación cruzada temporal — RMSE por fold (año validado)", loc="left", color=ink, fontsize=12.5,
                 pad=24)
    ax.text(0, 1.02, "Miles de USD · cada fold entrena con todos los meses anteriores y pronostica 12 meses",
            transform=ax.transAxes, color=ink2, fontsize=9)
    ax.set_ylabel("RMSE (miles de USD)", color=ink2)
    _style(ax, ink2, grid)
    ax.legend(loc="upper right", frameon=False, labelcolor=ink2, fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=surface)
    plt.close(fig)


# ---------------------------------------------------------------- run


def evaluate(df: pd.DataFrame) -> dict:
    split = sf.split_train_test(df)
    current = cross_validate(split)
    regularized = cross_validate(split, min_samples_leaf=5)
    proposed = cross_validate(split, PROPOSED_FEATURE_SET)
    return {
        "scope": {
            "data": "training years only (2016-2023); 2024-2025 untouched",
            "cv": f"TimeSeriesSplit(n_splits={N_SPLITS}, test_size={VALIDATION_MONTHS}), expanding window, "
                  "no shuffle, recursive 12-month forecast per fold",
            "primary_metric": PRIMARY_METRIC,
            "random_state": sf.SEED,
        },
        "noise_floor": noise_floor(split),
        "current_model": current,
        "learning_curve": learning_curve(split),
        "experiment_more_regularization": {"params": {"min_samples_leaf": 5}, **regularized},
        "experiment_proposed_features": {"features": PROPOSED_FEATURES, "min_history_months": 24, **proposed},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", type=Path, default=sf.DATA_PATH)
    parser.add_argument("--output-dir", type=Path, default=EVAL_DIR)
    args = parser.parse_args(argv)

    result = evaluate(sf.load_sales(args.data))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "sales_model_evaluation.json").write_text(json.dumps(result, indent=2) + "\n")
    plot_learning_curve(result["learning_curve"], result["noise_floor"], args.output_dir / "learning_curve.png")
    plot_cv_folds(result["current_model"], result["experiment_more_regularization"],
                  result["experiment_proposed_features"], args.output_dir / "cv_folds.png")

    def line(label: str, summary: dict) -> str:
        return (f"{label:<34} RMSE {summary['rmse_usd']['mean'] / 1e3:7.1f}k ± {summary['rmse_usd']['std'] / 1e3:5.1f}k"
                f"   MAE {summary['mae_usd']['mean'] / 1e3:7.1f}k ± {summary['mae_usd']['std'] / 1e3:5.1f}k")

    s = result["current_model"]["summary"]
    print(line("train in-sample", s["train_in_sample"]))
    print(line("train out-of-bag", s["train_out_of_bag"]))
    print(line("validation (5 temporal folds)", s["validation"]))
    print(line("baseline seasonal naive", s["baseline_seasonal_naive"]))
    print(line("more regularization (leaf=5)", result["experiment_more_regularization"]["summary"]["validation"]))
    print(line("proposed features", result["experiment_proposed_features"]["summary"]["validation"]))
    print(f"noise floor ~{result['noise_floor']['rmse_usd_at_mean_level'] / 1e3:.1f}k "
          f"({result['noise_floor']['residual_std_pct']:.2f} %)")
    print(f"outputs in {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

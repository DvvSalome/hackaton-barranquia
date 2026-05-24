"""Train wellbeing + diagnostic classifiers from the real DASS-42 dataset.

Data source (public domain, no auth required):
  Depression Anxiety Stress Scales — Open Psychometrics
  URL: https://openpsychometrics.org/_rawdata/DASS_data_21.02.19.zip
  N = 39,775 real human responses

Feature derivation for sleep/energy/screen time:
  No public sleep CSV without Kaggle auth exists, so secondary features are
  derived using research-validated correlations from:
  - Baglioni et al. 2016 (sleep ↔ depression)
  - Twenge & Campbell 2019 (screen time ↔ mental health)
  - Steptoe et al. 2008 (energy ↔ anxiety/stress)

Outputs:
  ml_model/wellbeing_model.pkl   — GradientBoosting risk classifier (3 classes)
  ml_model/diagnostic_model.pkl  — DecisionTree pattern classifier (8 patterns)

Run from api-service/:
  python ml_model/train_real.py
"""
from __future__ import annotations

import io
import json
import pickle
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from sklearn.metrics import classification_report
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder, StandardScaler

DASS_URL = "https://openpsychometrics.org/_rawdata/DASS_data_21.02.19.zip"
OUT_DIR = Path(__file__).parent
WELLBEING_MODEL_PATH = OUT_DIR / "wellbeing_model.pkl"
DIAGNOSTIC_MODEL_PATH = OUT_DIR / "diagnostic_model.pkl"

# ─── DASS-42 subscale item indices (1-based Q numbers) ────────────────────────
# Source: Lovibond & Lovibond 1995, DASS-42 manual

DEPRESSION_ITEMS = [3, 5, 10, 13, 16, 17, 21, 24, 26, 31, 34, 37, 38, 42]
ANXIETY_ITEMS    = [2, 4, 7,  9,  15, 19, 20, 23, 25, 28, 30, 36, 40, 41]
STRESS_ITEMS     = [1, 6, 8,  11, 12, 14, 18, 22, 27, 29, 32, 33, 35, 39]

# DASS-42 clinical cutoffs (raw sum, items scored 0-3)
# Source: Lovibond & Lovibond 1995
DASS_D_CUTOFFS = {"normal": 9,  "mild": 13, "moderate": 20, "severe": 27}
DASS_A_CUTOFFS = {"normal": 7,  "mild": 9,  "moderate": 14, "severe": 19}
DASS_S_CUTOFFS = {"normal": 14, "mild": 18, "moderate": 25, "severe": 33}

# Diagnostic pattern labels (primary diagnosis, priority order)
PATTERNS = [
    "distres_severo",
    "distres_moderado",
    "privacion_sueno",
    "fatiga_digital",
    "distres_leve",
    "baja_energia",
    "uso_digital_elevado",
    "sueno_deficiente",
    "deficit_foco",
    "bienestar_en_rango",
]


# ─── Step 1: Download + parse DASS dataset ────────────────────────────────────

def download_dass() -> pd.DataFrame:
    """Download DASS-42 ZIP and return the inner data.csv as a DataFrame."""
    local_cache = OUT_DIR / "dass_data.csv"
    if local_cache.exists():
        print(f"[data] Using cached DASS data: {local_cache}")
        return pd.read_csv(local_cache, sep="\t", low_memory=False)

    print(f"[data] Downloading DASS dataset from Open Psychometrics (~6.8 MB)...")
    resp = requests.get(DASS_URL, timeout=120)
    resp.raise_for_status()
    print(f"[data] Downloaded {len(resp.content) / 1024 / 1024:.1f} MB")

    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        csv_names = [n for n in z.namelist() if n.endswith(".csv")]
        if not csv_names:
            raise RuntimeError("No CSV found inside ZIP")
        csv_name = csv_names[0]
        print(f"[data] Extracting {csv_name} ({z.getinfo(csv_name).file_size // 1024} KB uncompressed)")
        with z.open(csv_name) as f:
            df = pd.read_csv(f, sep="\t", low_memory=False)

    df.to_csv(local_cache, sep="\t", index=False)
    print(f"[data] Cached to {local_cache}")
    return df


def _col(q: int) -> str:
    """Return column name for DASS question number q (e.g. 3 → 'Q3A')."""
    return f"Q{q}A"


def parse_dass(df: pd.DataFrame) -> pd.DataFrame:
    """Extract and clean DASS-42 subscale scores.

    Raw values are 1-4 (never/sometimes/often/almost always).
    Convert to 0-3 by subtracting 1.
    """
    dep_cols = [_col(q) for q in DEPRESSION_ITEMS if _col(q) in df.columns]
    anx_cols = [_col(q) for q in ANXIETY_ITEMS    if _col(q) in df.columns]
    str_cols = [_col(q) for q in STRESS_ITEMS     if _col(q) in df.columns]

    if not dep_cols:
        raise RuntimeError("DASS depression columns not found — unexpected format")

    # Coerce to numeric, drop rows with missing DASS items
    all_cols = dep_cols + anx_cols + str_cols
    df_clean = df[all_cols].apply(pd.to_numeric, errors="coerce").dropna()

    # Raw values are 1-4 → 0-3
    df_clean = df_clean - 1
    df_clean = df_clean.clip(0, 3)

    out = pd.DataFrame()
    out["dass_d"] = df_clean[dep_cols].sum(axis=1)   # 0-42
    out["dass_a"] = df_clean[anx_cols].sum(axis=1)   # 0-42
    out["dass_s"] = df_clean[str_cols].sum(axis=1)   # 0-42

    print(f"[data] DASS parsed: {len(out)} valid rows")
    print(f"[data] DASS-D mean={out['dass_d'].mean():.1f}  DASS-A mean={out['dass_a'].mean():.1f}")
    return out.reset_index(drop=True)


# ─── Step 2: Map DASS to our 12-feature space ─────────────────────────────────

def derive_features(dass: pd.DataFrame, seed: int = 42) -> pd.DataFrame:
    """Convert DASS subscale scores to the 12-feature vector used by our models.

    DASS → PHQ-9/GAD-7 mapping:
      PHQ-9 ≈ DASS-D × (27/42)   (scale matching, Kroenke et al.)
      GAD-7 ≈ DASS-A × (21/42)   (scale matching, Spitzer et al.)

    Secondary features use research-validated correlations:
      sleep_score:   correlated with -(depression + stress/2)   (Baglioni 2016)
      energy_score:  correlated with -(depression*0.6 + stress*0.4)  (Steptoe 2008)
      focus_score:   correlated with -(anxiety*0.7 + stress*0.3)
      mood_score:    correlated with -depression                 (clinical obvious)
      screen_hours:  correlated with +(depression + anxiety)/2  (Twenge 2019)
      social_pct:    correlated with +anxiety (avoidance → scrolling) (Hunt 2018)
    """
    rng = np.random.default_rng(seed)
    n = len(dass)

    # Normalize DASS subscales to 0-1
    d_norm = dass["dass_d"] / 42.0
    a_norm = dass["dass_a"] / 42.0
    s_norm = dass["dass_s"] / 42.0

    # PHQ-9 (0-27) and GAD-7 (0-21)
    phq9 = (d_norm * 27).clip(0, 27)
    gad7 = (a_norm * 21).clip(0, 21)

    # Sleep score (0-100): higher = better
    # Mean ~65 in healthy pop, drops to ~30 in severe depression
    sleep_base = 75 - d_norm * 55 - s_norm * 20
    sleep_score = (sleep_base + rng.normal(0, 8, n)).clip(5, 100)

    # Energy score (0-100)
    energy_base = 70 - d_norm * 50 - s_norm * 20
    energy_score = (energy_base + rng.normal(0, 10, n)).clip(5, 100)

    # Focus score (0-100): anxiety and stress impair focus
    focus_base = 72 - a_norm * 45 - s_norm * 25
    focus_score = (focus_base + rng.normal(0, 10, n)).clip(5, 100)

    # Mood score (0-100)
    mood_base = 75 - d_norm * 60 - a_norm * 15
    mood_score = (mood_base + rng.normal(0, 8, n)).clip(5, 100)

    # Daily screen hours (0-16)
    screen_base = 4 + (d_norm + a_norm) / 2 * 8
    daily_screen_hours = (screen_base + rng.normal(0, 1.5, n)).clip(0.5, 16)

    # Social media percentage of screen time (0-100)
    social_base = 20 + a_norm * 50
    social_pct = (social_base + rng.normal(0, 8, n)).clip(0, 95)

    # Assessment scores (proxy)
    screen_score = (15 - daily_screen_hours * 1.2).clip(0, 15)
    habits_score = (15 - d_norm * 10 - s_norm * 5).clip(0, 15)
    habits_score = (habits_score + rng.normal(0, 1, n)).clip(0, 15)

    # Drowsiness and distraction counts (from CV-like signals)
    drowsiness = (d_norm * 4 + rng.poisson(0.3, n)).clip(0, 8)
    distraction = (a_norm * 5 + rng.poisson(0.5, n)).clip(0, 10)

    # Sleep score normalization for ML model (0-8 hours equivalent)
    sleep_ml = sleep_score / 100.0 * 8

    feat = pd.DataFrame({
        "phq9_score":        phq9,
        "gad7_score":        gad7,
        "screen_score":      screen_score,
        "habits_score":      habits_score,
        "sleep_score_ml":    sleep_ml,          # 0-8 (for ML model)
        "sleep_score_raw":   sleep_score,       # 0-100 (for diagnostic rules)
        "energy_score":      energy_score,
        "focus_score":       focus_score,
        "mood_score":        mood_score,
        "daily_screen_hours": daily_screen_hours,
        "social_pct":        social_pct,
        "drowsiness_count":  drowsiness,
        "distraction_count": distraction,
        # Raw DASS scores kept for direct label assignment
        "dass_d":            dass["dass_d"].values,
        "dass_a":            dass["dass_a"].values,
    })
    return feat


# ─── Step 3: Assign labels ────────────────────────────────────────────────────

def assign_risk_label(row: pd.Series) -> int:
    """Assign wellbeing risk label from DASS severity directly.

    Uses DASS-42 clinical cutoffs (Lovibond & Lovibond 1995) as ground truth,
    then adjusts for secondary signals (screen time, sleep). This avoids the
    circular label-from-derived-features problem.
    """
    dass_d = row["dass_d"]  # raw DASS-42 depression sum (0-42)
    dass_a = row["dass_a"]  # raw DASS-42 anxiety sum (0-42)
    screen_h = row["daily_screen_hours"]
    sleep_s = row["sleep_score_raw"]

    # Depression severity (Lovibond 1995 DASS-42 cutoffs)
    if dass_d >= 21:    dep_r = 2   # severe / extremely severe
    elif dass_d >= 14:  dep_r = 1   # moderate
    elif dass_d >= 10:  dep_r = 1   # mild
    else:               dep_r = 0   # normal

    # Anxiety severity
    if dass_a >= 15:    anx_r = 2
    elif dass_a >= 10:  anx_r = 1
    elif dass_a >= 8:   anx_r = 1
    else:               anx_r = 0

    # Boost from secondary digital/sleep signals
    screen_boost = 1 if screen_h >= 8 else 0
    sleep_boost  = 1 if sleep_s < 35 else 0

    combined = max(dep_r, anx_r) + (screen_boost + sleep_boost) // 2
    return min(combined, 2)


def assign_diagnostic_label(row: pd.Series) -> str:
    """Assign primary diagnostic pattern (priority order matches DiagnosticTool)."""
    phq9 = row["phq9_score"]
    gad7 = row["gad7_score"]
    sleep = row["sleep_score_raw"]
    energy = row["energy_score"]
    focus = row["focus_score"]
    mood = row["mood_score"]
    screen_h = row["daily_screen_hours"]
    social_pct = row["social_pct"]

    if phq9 >= 15 or gad7 >= 10:
        return "distres_severo"
    if phq9 >= 10 or gad7 >= 7:
        return "distres_moderado"
    if sleep < 30:
        return "privacion_sueno"
    if screen_h >= 7 and mood < 40:
        return "fatiga_digital"
    if phq9 >= 5 or gad7 >= 5:
        return "distres_leve"
    if energy < 30:
        return "baja_energia"
    if screen_h >= 5:
        return "uso_digital_elevado"
    if sleep < 50:
        return "sueno_deficiente"
    if focus < 30:
        return "deficit_foco"
    return "bienestar_en_rango"


# ─── Step 4: Build feature matrix ─────────────────────────────────────────────

def build_X_wellbeing(feat: pd.DataFrame) -> np.ndarray:
    """12-feature vector matching wellbeing_model.pkl input format."""
    return feat[[
        "phq9_score", "gad7_score", "screen_score", "habits_score",
        "sleep_score_ml", "energy_score", "focus_score", "mood_score",
        "daily_screen_hours", "social_pct", "drowsiness_count", "distraction_count",
    ]].values


def build_X_diagnostic(feat: pd.DataFrame) -> np.ndarray:
    """Feature matrix for the diagnostic pattern classifier."""
    # Normalize energy/focus/mood to 0-1 (consistent with DiagnosticTool thresholds)
    return np.column_stack([
        feat["phq9_score"].values,
        feat["gad7_score"].values,
        feat["sleep_score_raw"].values,
        feat["energy_score"].values,
        feat["focus_score"].values,
        feat["mood_score"].values,
        feat["daily_screen_hours"].values,
        feat["social_pct"].values,
    ])


# ─── Step 5: Train models ─────────────────────────────────────────────────────

def train_wellbeing(X: np.ndarray, y: np.ndarray) -> None:
    print("\n[wellbeing] Training GradientBoostingClassifier...")
    print(f"[wellbeing] Dataset: {len(X)} samples | classes: {dict(zip(*np.unique(y, return_counts=True)))}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    model = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", GradientBoostingClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.08,
            subsample=0.8,
            random_state=42,
        )),
    ])
    model.fit(X_train, y_train)

    cv = cross_val_score(model, X, y, cv=StratifiedKFold(5), scoring="f1_macro")
    print(f"[wellbeing] 5-fold F1 macro: {cv.mean():.3f} ± {cv.std():.3f}")
    print(classification_report(y_test, model.predict(X_test),
                                target_names=["bajo", "moderado", "alto"]))

    with open(WELLBEING_MODEL_PATH, "wb") as f:
        pickle.dump(model, f)
    print(f"[wellbeing] Saved -> {WELLBEING_MODEL_PATH}")


def train_diagnostic(X: np.ndarray, y_labels: np.ndarray) -> None:
    print("\n[diagnostic] Training DecisionTreeClassifier...")
    le = LabelEncoder()
    y = le.fit_transform(y_labels)
    classes = le.classes_

    dist = {k: int(v) for k, v in zip(*np.unique(y_labels, return_counts=True))}
    print(f"[diagnostic] Dataset: {len(X)} samples | patterns: {json.dumps(dist, ensure_ascii=False)}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # DecisionTree: interpretable, appropriate for clinical decision support
    # RandomForest as fallback if tree is too noisy
    tree = DecisionTreeClassifier(
        max_depth=8,
        min_samples_leaf=50,
        class_weight="balanced",
        random_state=42,
    )
    tree.fit(X_train, y_train)

    cv = cross_val_score(tree, X, y, cv=StratifiedKFold(5), scoring="f1_macro")
    print(f"[diagnostic] Decision Tree 5-fold F1 macro: {cv.mean():.3f} ± {cv.std():.3f}")
    print(classification_report(y_test, tree.predict(X_test), target_names=classes))

    # Print the top 3 levels of the tree for interpretability
    feature_names = ["phq9", "gad7", "sleep", "energy", "focus", "mood", "screen_h", "social_pct"]
    tree_text = export_text(tree, feature_names=feature_names, max_depth=3)
    print(f"\n[diagnostic] Decision tree (top 3 levels):\n{tree_text}")

    # Bundle model + label encoder together
    bundle = {"model": tree, "label_encoder": le, "feature_names": feature_names}
    with open(DIAGNOSTIC_MODEL_PATH, "wb") as f:
        pickle.dump(bundle, f)
    print(f"[diagnostic] Saved -> {DIAGNOSTIC_MODEL_PATH}")


# ─── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("Kairós — Real data training pipeline")
    print("=" * 60)

    # 1. Download + parse DASS
    raw_df = download_dass()
    dass = parse_dass(raw_df)

    # 2. Derive full feature set
    print("\n[features] Deriving secondary features from DASS scores...")
    feat = derive_features(dass)

    # 3. Assign labels
    y_risk = feat.apply(assign_risk_label, axis=1).values
    y_diag = feat.apply(assign_diagnostic_label, axis=1).values

    # 4. Build feature matrices
    X_wb = build_X_wellbeing(feat)
    X_diag = build_X_diagnostic(feat)

    print(f"\n[labels] Risk distribution:      {dict(zip(*np.unique(y_risk, return_counts=True)))}")
    print(f"[labels] Diagnostic distribution: {dict(zip(*np.unique(y_diag, return_counts=True)))}")

    # 5. Train
    train_wellbeing(X_wb, y_risk)
    train_diagnostic(X_diag, y_diag)

    print("\n" + "=" * 60)
    print("Training complete.")
    print(f"  wellbeing  -> {WELLBEING_MODEL_PATH}")
    print(f"  diagnostic -> {DIAGNOSTIC_MODEL_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    main()

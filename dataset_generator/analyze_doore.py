#!/usr/bin/env python3
"""Exploratory analysis of the DOORE smart-room dataset.

Answers two questions with numbers:
  (1) How separable are the 5 top-level classes in sensor-feature space?
  (2) How much variation exists WITHIN each class (incl. whether sub-labels
      form distinct sub-clusters)?

EXPLORATORY ONLY — no caption generation. Produces a feature CSV + figures +
a printed summary.
"""
import os
import sys
import json
import glob
import warnings
from collections import defaultdict

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.ensemble import RandomForestClassifier, IsolationForest
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.metrics import classification_report, confusion_matrix, silhouette_score, silhouette_samples

try:
    import umap
    HAVE_UMAP = True
except Exception:
    HAVE_UMAP = False

DATA_ROOT = "/panthera/Lapras/smart_home_gs/doore"
OUT_DIR = "/panthera/Lapras/dataset_generator"
FIG_DIR = os.path.join(OUT_DIR, "figures")
os.makedirs(FIG_DIR, exist_ok=True)

# Sensor taxonomy by prefix.
NUMERIC_GROUPS = {
    "Sound": ["Sound_L", "Sound_C", "Sound_R", "Sound_P"],
    "Temperature": None,   # match by prefix
    "Humidity": None,
    "Brightness": None,
    "PodiumIR": None,
}
BOOL_GROUPS = ["Motion", "Seat"]          # True/False -> activity density
EVENT_GROUPS = ["Door", "Light", "Aircon", "Projector"]  # discrete events/state


def sensor_group(name):
    for g in ("Sound", "Temperature", "Humidity", "Brightness", "PodiumIR",
              "Motion", "Seat", "Door", "Light", "Aircon", "Projector"):
        if name == g or name.startswith(g + "_") or name.startswith(g):
            return g
    return "Other"


def to_float(v):
    try:
        return float(v)
    except Exception:
        return np.nan


def load_sessions():
    rows = []
    classes = sorted([d for d in os.listdir(DATA_ROOT)
                      if os.path.isdir(os.path.join(DATA_ROOT, d))])
    for cls in classes:
        meta_dir = os.path.join(DATA_ROOT, cls, "metadata")
        sens_dir = os.path.join(DATA_ROOT, cls, "sensor")
        for mp in sorted(glob.glob(os.path.join(meta_dir, "*.json"))):
            base = os.path.splitext(os.path.basename(mp))[0]
            cp = os.path.join(sens_dir, base + ".csv")
            if not os.path.exists(cp):
                continue
            with open(mp) as f:
                meta = json.load(f)
            rows.append({"class": cls, "session": base, "meta": meta, "csv": cp})
    return classes, rows


def extract_features(session):
    """One feature vector per session."""
    meta = session["meta"]
    cls = session["class"]
    base = session["session"]
    feat = {"session": base, "class": cls,
            "sub_label": meta.get("label", ""),
            "duration_sec": float(meta.get("duration", np.nan)),
            "avg_humans": float(meta.get("avg_n_human", np.nan))}

    df = pd.read_csv(session["csv"])
    if df.empty:
        return feat
    df["g"] = df["sensor_name"].map(sensor_group)
    dur_min = max(feat["duration_sec"], 1.0) / 60.0
    t0, t1 = df["timestamp"].min(), df["timestamp"].max()
    tmid = (t0 + t1) / 2.0
    feat["n_events_total"] = len(df)
    feat["event_rate_per_min"] = len(df) / dur_min
    feat["n_distinct_sensors"] = df["sensor_name"].nunique()

    # ---- numeric ambient groups: mean/std/min/max/range + rate + halves delta
    for g in ("Sound", "Temperature", "Humidity", "Brightness", "PodiumIR"):
        sub = df[df["g"] == g].copy()
        sub["val"] = sub["value"].map(to_float)
        sub = sub.dropna(subset=["val"])
        if len(sub):
            feat[f"{g}_mean"] = sub["val"].mean()
            feat[f"{g}_std"] = sub["val"].std()
            feat[f"{g}_min"] = sub["val"].min()
            feat[f"{g}_max"] = sub["val"].max()
            feat[f"{g}_range"] = sub["val"].max() - sub["val"].min()
            feat[f"{g}_rate_per_min"] = len(sub) / dur_min
            fh = sub[sub["timestamp"] <= tmid]["val"].mean()
            sh = sub[sub["timestamp"] > tmid]["val"].mean()
            feat[f"{g}_half_delta"] = (sh - fh) if (pd.notna(fh) and pd.notna(sh)) else np.nan
        # else leave missing (NaN) -> handled later

    # ---- per-channel sound energy
    for ch in ("Sound_L", "Sound_C", "Sound_R", "Sound_P"):
        sub = df[df["sensor_name"] == ch].copy()
        sub["val"] = sub["value"].map(to_float)
        sub = sub.dropna(subset=["val"])
        if len(sub):
            feat[f"{ch}_mean"] = sub["val"].mean()

    # ---- boolean activity density (Motion, Seat): fraction True + event rate + zones
    for g in BOOL_GROUPS:
        sub = df[df["g"] == g]
        if len(sub):
            truth = sub["value"].astype(str).str.lower().isin(["true", "1"])
            feat[f"{g}_true_frac"] = truth.mean()
            feat[f"{g}_event_rate_per_min"] = len(sub) / dur_min
            feat[f"{g}_n_zones"] = sub["sensor_name"].nunique()

    # ---- event/state groups (Door, Light, Aircon, Projector): counts + rate
    for g in EVENT_GROUPS:
        sub = df[df["g"] == g]
        feat[f"{g}_n_events"] = len(sub)
        feat[f"{g}_rate_per_min"] = len(sub) / dur_min

    # ---- overall temporal shape: event count first vs second half
    n_fh = (df["timestamp"] <= tmid).sum()
    n_sh = (df["timestamp"] > tmid).sum()
    feat["event_half_ratio"] = (n_sh + 1) / (n_fh + 1)

    return feat


def main():
    print("Loading sessions from", DATA_ROOT)
    classes, sessions = load_sessions()
    print(f"  classes={classes}  n_sessions={len(sessions)}")

    feats = [extract_features(s) for s in sessions]
    fdf = pd.DataFrame(feats)
    feat_csv = os.path.join(OUT_DIR, "doore_session_features.csv")
    fdf.to_csv(feat_csv, index=False)
    print("Wrote feature table:", feat_csv, fdf.shape)

    # ===================== DESCRIPTIVE =====================
    print("\n================ (0) DESCRIPTIVE ================")
    print("\nPer-class counts:")
    print(fdf["class"].value_counts().to_string())
    print("\nPer sub-label counts:")
    print(fdf.groupby(["class", "sub_label"]).size().to_string())
    print("\nDuration (sec) by class:")
    print(fdf.groupby("class")["duration_sec"].describe()[["count", "mean", "std", "min", "50%", "max"]].round(1).to_string())
    print("\nAvg humans by class:")
    print(fdf.groupby("class")["avg_humans"].describe()[["mean", "std", "min", "50%", "max"]].round(2).to_string())

    # sensor availability matrix (fraction of sessions in class where group present)
    avail_rows = []
    sess_by_idx = {i: s for i, s in enumerate(sessions)}
    groups_all = ["Sound", "Temperature", "Humidity", "Brightness", "PodiumIR",
                  "Motion", "Seat", "Door", "Light", "Aircon", "Projector"]
    pres = defaultdict(lambda: defaultdict(int))
    cls_count = defaultdict(int)
    for s in sessions:
        cls = s["class"]; cls_count[cls] += 1
        present = set(g for g in groups_all
                      if any(sensor_group(x) == g for x in s["meta"].get("sensors", [])))
        for g in groups_all:
            if g in present:
                pres[cls][g] += 1
    avail = pd.DataFrame({cls: {g: pres[cls][g] / cls_count[cls] for g in groups_all}
                          for cls in classes}).T
    print("\nSensor-availability matrix (fraction of sessions w/ sensor present, by class):")
    print((avail * 100).round(0).astype(int).to_string())
    avail.to_csv(os.path.join(OUT_DIR, "sensor_availability_matrix.csv"))

    # ===================== FEATURE MATRIX =====================
    meta_cols = ["session", "class", "sub_label"]
    Xdf = fdf.drop(columns=meta_cols)
    feat_names = Xdf.columns.tolist()
    # missingness report
    miss = Xdf.isna().mean().sort_values(ascending=False)
    print("\nTop feature missingness (frac of sessions):")
    print((miss[miss > 0].head(15) * 100).round(1).to_string())
    # impute median for modeling, but keep explicit missing flag count
    Xfull = Xdf.fillna(Xdf.median(numeric_only=True))
    Xfull = Xfull.fillna(0.0)
    y = fdf["class"].values
    Xs = StandardScaler().fit_transform(Xfull.values)

    # ===================== (1) SEPARABILITY =====================
    print("\n================ (1) SEPARABILITY ================")
    # PCA
    pca = PCA(n_components=2, random_state=0)
    Xp = pca.fit_transform(Xs)
    print("PCA explained variance (2 comp):", pca.explained_variance_ratio_.round(3),
          " sum=", round(pca.explained_variance_ratio_.sum(), 3))
    plot_proj(Xp, y, classes, "PCA — sessions colored by class",
              os.path.join(FIG_DIR, "pca_by_class.png"),
              f"PC1 ({pca.explained_variance_ratio_[0]*100:.0f}%)",
              f"PC2 ({pca.explained_variance_ratio_[1]*100:.0f}%)")

    # UMAP
    if HAVE_UMAP:
        try:
            Xu = umap.UMAP(n_neighbors=20, min_dist=0.1, random_state=0).fit_transform(Xs)
            plot_proj(Xu, y, classes, "UMAP — sessions colored by class",
                      os.path.join(FIG_DIR, "umap_by_class.png"), "UMAP-1", "UMAP-2")
        except Exception as e:
            print("UMAP failed:", e)

    # Random forest with stratified CV predictions
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
    rf = RandomForestClassifier(n_estimators=400, class_weight="balanced", random_state=0, n_jobs=-1)
    y_pred = cross_val_predict(rf, Xs, y, cv=skf, n_jobs=-1)
    print("\nRandom-forest 5-fold CV classification report (class labels):")
    print(classification_report(y, y_pred, digits=3))
    cm = confusion_matrix(y, y_pred, labels=classes)
    print("Confusion matrix (rows=true, cols=pred):\n", pd.DataFrame(cm, index=classes, columns=classes).to_string())
    plot_confusion(cm, classes, os.path.join(FIG_DIR, "confusion_matrix.png"))

    # fit on all for feature importance
    rf.fit(Xs, y)
    imp = pd.Series(rf.feature_importances_, index=feat_names).sort_values(ascending=False)
    print("\nTop 15 discriminative features:")
    print(imp.head(15).round(4).to_string())
    plot_importance(imp.head(20), os.path.join(FIG_DIR, "feature_importance.png"))

    # overall silhouette (class separation in feature space)
    sil = silhouette_score(Xs, y)
    print(f"\nGlobal silhouette (5 classes, standardized features): {sil:.3f}")

    # ===================== (2) WITHIN-CLASS SPREAD =====================
    print("\n================ (2) WITHIN-CLASS SPREAD ================")
    # centroid distances
    cents = {c: Xs[y == c].mean(axis=0) for c in classes}
    intra = {}
    for c in classes:
        d = np.linalg.norm(Xs[y == c] - cents[c], axis=1)
        intra[c] = d.mean()
    inter = {}
    for c in classes:
        others = [np.linalg.norm(cents[c] - cents[o]) for o in classes if o != c]
        inter[c] = np.mean(others)
    spread = pd.DataFrame({"intra_mean_dist": intra, "nearest_class_dist": {c: min(np.linalg.norm(cents[c]-cents[o]) for o in classes if o!=c) for c in classes}, "mean_inter_dist": inter})
    spread["spread_ratio_intra_over_inter"] = spread["intra_mean_dist"] / spread["mean_inter_dist"]
    print("\nIntra- vs inter-class distances (standardized feature space):")
    print(spread.round(2).to_string())

    # per-class feature variance (avg normalized std)
    print("\nPer-class mean feature dispersion (avg std of standardized features, higher=more internal variation):")
    Xs_df = pd.DataFrame(Xs, columns=feat_names)
    Xs_df["class"] = y
    disp = Xs_df.groupby("class").std(numeric_only=True).mean(axis=1).sort_values(ascending=False)
    print(disp.round(3).to_string())

    # ---- sub-label sub-clustering within each class
    print("\nSub-label separability WITHIN each class (silhouette of sub-labels; needs >=2 sub-labels):")
    for c in classes:
        mask = y == c
        subs = fdf.loc[mask, "sub_label"].values
        uniq = pd.Series(subs).value_counts()
        if len(uniq) < 2 or (uniq >= 5).sum() < 2:
            print(f"  {c:22s}: single/!enough sub-labels ({dict(uniq)}) -> no sub-structure to test")
            continue
        keep = pd.Series(subs).isin(uniq[uniq >= 5].index).values
        Xc = Xs[mask][keep]
        sc = subs[keep]
        try:
            s = silhouette_score(Xc, sc)
        except Exception:
            s = float("nan")
        # mini-RF separability among sub-labels
        rfc = RandomForestClassifier(n_estimators=300, class_weight="balanced", random_state=0, n_jobs=-1)
        try:
            pred = cross_val_predict(rfc, Xc, sc, cv=min(5, pd.Series(sc).value_counts().min()), n_jobs=-1)
            acc = (pred == sc).mean()
        except Exception:
            acc = float("nan")
        print(f"  {c:22s}: sub-labels={dict(uniq)}  silhouette={s:.3f}  subRF_acc={acc:.3f}")
        # PCA of just this class colored by sub-label
        pcc = PCA(n_components=2, random_state=0).fit_transform(Xs[mask])
        plot_proj(pcc, subs, list(uniq.index), f"{c}: sessions by sub-label",
                  os.path.join(FIG_DIR, f"subcluster_{c}.png"), "PC1", "PC2")

    # ===================== (3) DATA ISSUES =====================
    print("\n================ (3) DATA ISSUES ================")
    print(f"Class imbalance ratio (max/min): {fdf['class'].value_counts().max()}/{fdf['class'].value_counts().min()} = {fdf['class'].value_counts().max()/fdf['class'].value_counts().min():.1f}x")
    # outliers via isolation forest
    iso = IsolationForest(random_state=0, contamination=0.03)
    out = iso.fit_predict(Xs)
    n_out = (out == -1).sum()
    print(f"Outlier sessions (IsolationForest, ~3% contamination): {n_out}")
    odf = fdf.loc[out == -1, ["session", "class", "sub_label", "duration_sec", "avg_humans", "n_events_total"]]
    print(odf.head(20).to_string(index=False))
    # duration extremes
    print("\nShortest / longest sessions:")
    print(fdf.nsmallest(3, "duration_sec")[["session", "class", "duration_sec"]].to_string(index=False))
    print(fdf.nlargest(3, "duration_sec")[["session", "class", "duration_sec"]].to_string(index=False))

    print("\nDONE. Figures in", FIG_DIR)


# ---------- plotting helpers ----------
def plot_proj(X2, labels, order, title, path, xl, yl):
    plt.figure(figsize=(7, 6))
    labels = np.asarray(labels)
    cmap = plt.get_cmap("tab10")
    for i, c in enumerate(order):
        m = labels == c
        plt.scatter(X2[m, 0], X2[m, 1], s=14, alpha=0.6, color=cmap(i % 10), label=str(c))
    plt.title(title); plt.xlabel(xl); plt.ylabel(yl)
    plt.legend(fontsize=8, markerscale=1.5)
    plt.tight_layout(); plt.savefig(path, dpi=130); plt.close()


def plot_confusion(cm, classes, path):
    cmn = cm.astype(float) / cm.sum(axis=1, keepdims=True)
    plt.figure(figsize=(6.5, 5.5))
    plt.imshow(cmn, cmap="Blues", vmin=0, vmax=1)
    plt.colorbar(fraction=0.046)
    plt.xticks(range(len(classes)), classes, rotation=45, ha="right", fontsize=8)
    plt.yticks(range(len(classes)), classes, fontsize=8)
    for i in range(len(classes)):
        for j in range(len(classes)):
            plt.text(j, i, f"{cmn[i,j]:.2f}", ha="center", va="center",
                     color="white" if cmn[i, j] > 0.5 else "black", fontsize=8)
    plt.title("Confusion matrix (row-normalized)")
    plt.ylabel("true"); plt.xlabel("predicted")
    plt.tight_layout(); plt.savefig(path, dpi=130); plt.close()


def plot_importance(imp, path):
    plt.figure(figsize=(7, 6))
    imp[::-1].plot.barh()
    plt.title("Top discriminative features (RF importance)")
    plt.tight_layout(); plt.savefig(path, dpi=130); plt.close()


if __name__ == "__main__":
    main()

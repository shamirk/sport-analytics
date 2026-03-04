from datetime import timedelta

import numpy as np
import pandas as pd
from scipy import stats


class AnalyticsEngine:
    def compute_classifier_stats(self, scores: list[dict]) -> dict:
        if not scores:
            return {}

        df = pd.DataFrame(scores)
        df["percentage"] = pd.to_numeric(df.get("percentage", pd.Series(dtype=float)), errors="coerce")
        df["match_date"] = pd.to_datetime(df.get("match_date", pd.Series(dtype=str)), errors="coerce")
        df = df.dropna(subset=["percentage"])
        df = df.sort_values("match_date")

        pct = df["percentage"]
        result: dict = {
            "mean": float(pct.mean()),
            "median": float(pct.median()),
            "std": float(pct.std()) if len(pct) > 1 else 0.0,
        }

        # Rolling averages
        for window in (5, 10, 20):
            rolled = pct.rolling(window, min_periods=1).mean()
            result[f"rolling_{window}"] = float(rolled.iloc[-1]) if not rolled.empty else None

        # Linear regression trend slope
        if len(pct) >= 2:
            x = np.arange(len(pct))
            slope, _, _, _, _ = stats.linregress(x, pct.values)
            result["trend_slope"] = float(slope)
        else:
            result["trend_slope"] = None

        # Volatility index
        mean_val = result["mean"]
        result["volatility_index"] = float(result["std"] / mean_val * 100) if mean_val else 0.0

        # Year-over-year improvement
        now = df["match_date"].max()
        if pd.notna(now):
            cutoff = now - timedelta(days=365)
            last_12 = df[df["match_date"] > cutoff]["percentage"]
            prior_12 = df[df["match_date"] <= cutoff]["percentage"]
            if not last_12.empty and not prior_12.empty:
                result["yoy_improvement"] = float(last_12.mean() - prior_12.mean())
            else:
                result["yoy_improvement"] = None
        else:
            result["yoy_improvement"] = None

        # Personal bests per classifier
        if "classifier_number" in df.columns:
            bests = (
                df.groupby("classifier_number")["percentage"]
                .max()
                .reset_index()
                .rename(columns={"percentage": "best_percentage"})
            )
            result["personal_bests"] = bests.to_dict(orient="records")
        else:
            result["personal_bests"] = []

        return result

    def compute_division_stats(self, scores: list[dict]) -> dict:
        if not scores:
            return {}

        df = pd.DataFrame(scores)
        df["percentage"] = pd.to_numeric(df.get("percentage", pd.Series(dtype=float)), errors="coerce")
        df = df.dropna(subset=["percentage"])

        if "division" not in df.columns:
            return {}

        avg_by_division = (
            df.groupby("division")["percentage"]
            .mean()
            .reset_index()
            .rename(columns={"percentage": "avg_percentage"})
            .sort_values("avg_percentage", ascending=False)
        )
        avg_by_division["rank"] = range(1, len(avg_by_division) + 1)

        # Radar chart data: normalize 0-100
        max_pct = avg_by_division["avg_percentage"].max()
        min_pct = avg_by_division["avg_percentage"].min()
        denom = max_pct - min_pct if max_pct != min_pct else 1.0
        avg_by_division["normalized"] = ((avg_by_division["avg_percentage"] - min_pct) / denom * 100).round(2)

        return {
            "division_averages": avg_by_division.to_dict(orient="records"),
            "radar_chart_data": avg_by_division[["division", "normalized"]].to_dict(orient="records"),
            "division_ranking": avg_by_division[["rank", "division", "avg_percentage"]].to_dict(orient="records"),
        }

    def compute_match_stats(self, matches: list[dict]) -> dict:
        if not matches:
            return {}

        df = pd.DataFrame(matches)
        df["percent_finish"] = pd.to_numeric(df.get("percent_finish", pd.Series(dtype=float)), errors="coerce")
        df["match_date"] = pd.to_datetime(df.get("match_date", pd.Series(dtype=str)), errors="coerce")
        df = df.dropna(subset=["percent_finish"])
        df = df.sort_values("match_date")

        pct = df["percent_finish"]
        result: dict = {
            "avg_percent_finish": float(pct.mean()),
        }

        if "placement" in df.columns:
            placements = df["placement"].dropna()
            if not placements.empty:
                result["best_placement"] = int(placements.min())
                result["worst_placement"] = int(placements.max())

        # Trend over time: list of {date, percent_finish}
        trend_df = df[["match_date", "percent_finish"]].copy()
        trend_df["match_date"] = trend_df["match_date"].dt.strftime("%Y-%m-%d")
        result["trend"] = trend_df.to_dict(orient="records")

        return result

    def prepare_time_series(self, scores: list[dict], division: str | None = None) -> list[dict]:
        if not scores:
            return []

        df = pd.DataFrame(scores)
        df["percentage"] = pd.to_numeric(df.get("percentage", pd.Series(dtype=float)), errors="coerce")
        df["match_date"] = pd.to_datetime(df.get("match_date", pd.Series(dtype=str)), errors="coerce")
        df = df.dropna(subset=["percentage", "match_date"])

        if division and "division" in df.columns:
            df = df[df["division"] == division]

        df = df.sort_values("match_date")

        for window in (5, 10, 20):
            df[f"rolling_{window}"] = df["percentage"].rolling(window, min_periods=1).mean().round(4)

        df["match_date"] = df["match_date"].dt.strftime("%Y-%m-%d")

        cols = ["match_date", "percentage"]
        for window in (5, 10, 20):
            cols.append(f"rolling_{window}")
        if "division" in df.columns:
            cols.append("division")

        return df[cols].to_dict(orient="records")

    def prepare_classifier_breakdown(self, scores: list[dict]) -> dict:
        if not scores:
            return {}

        df = pd.DataFrame(scores)
        df["percentage"] = pd.to_numeric(df.get("percentage", pd.Series(dtype=float)), errors="coerce")
        df = df.dropna(subset=["percentage"])

        sorted_desc = df.sort_values("percentage", ascending=False)
        sorted_asc = df.sort_values("percentage", ascending=True)

        top10 = sorted_desc.head(10).to_dict(orient="records")
        bottom10 = sorted_asc.head(10).to_dict(orient="records")

        result: dict = {
            "top_10": top10,
            "bottom_10": bottom10,
        }

        if "classifier_number" in df.columns:
            freq = (
                df["classifier_number"]
                .value_counts()
                .reset_index()
                .rename(columns={"classifier_number": "classifier_number", "count": "count"})
                .head(10)
            )
            result["most_frequent"] = freq.to_dict(orient="records")

            # Improvement rate: slope of percentage over attempts per classifier
            improvement_rows = []
            for clf, group in df.groupby("classifier_number"):
                group = group.sort_values("percentage")
                if len(group) >= 2:
                    x = np.arange(len(group))
                    slope, _, _, _, _ = stats.linregress(x, group["percentage"].values)
                    improvement_rows.append({"classifier_number": clf, "improvement_rate": float(slope)})
            result["improvement_rate_per_classifier"] = improvement_rows
        else:
            result["most_frequent"] = []
            result["improvement_rate_per_classifier"] = []

        return result

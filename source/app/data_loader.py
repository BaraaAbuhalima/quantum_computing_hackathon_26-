from __future__ import annotations

import os
from typing import Dict, Any, Tuple
import pandas as pd
from dateutil import parser as dateparser


TRUE_VALUES = {"TRUE", "true", "True", True}


def _read_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"CSV not found: {path}")
    df = pd.read_csv(path)
    # Drop completely empty rows
    df = df.dropna(how="all")
    return df


def load_all(water_data_dir: str) -> Dict[str, pd.DataFrame]:
    files = {
        "water_demand": os.path.join(water_data_dir, "water_demand.csv"),
        "water_supply": os.path.join(water_data_dir, "water_supply_per_locality.csv"),
        "water_storage": os.path.join(water_data_dir, "water_storage.csv"),
        "interruptions": os.path.join(water_data_dir, "water_interruptions.csv"),
        "locality_connections": os.path.join(water_data_dir, "locality-to-locality_water_connection.csv"),
        "source_locality": os.path.join(water_data_dir, "water_source_locallity_connection.csv"),
        "water_sources": os.path.join(water_data_dir, "water_sources.csv"),
    }
    return {name: _read_csv(path) for name, path in files.items()}


def parse_date_safe(s: Any) -> Any:
    if pd.isna(s) or s in ("", None):
        return None
    try:
        return dateparser.parse(str(s), dayfirst=False)
    except Exception:
        return None


def aggregate_per_locality(dfs: Dict[str, pd.DataFrame], now_ts) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Returns a dataframe keyed by locality_id with summarized metrics and a metadata dict.
    """
    demand = dfs["water_demand"].copy()
    demand["locality_id"] = demand["locality_id"].astype(int)
    demand = demand.set_index("locality_id")

    supply = dfs["water_supply"].copy()
    supply["locality_id"] = supply["locality_id"].astype(int)
    supply["actual_m3_day"] = pd.to_numeric(supply["actual_m3_day"], errors="coerce").fillna(0)
    supply_group = supply.groupby("locality_id")["actual_m3_day"].sum().to_frame("actual_supply_m3_day")

    storage = dfs["water_storage"].copy()
    storage["locality_id"] = storage["locality_id"].astype(int)
    storage["capacity_m3"] = pd.to_numeric(storage["capacity_m3"], errors="coerce").fillna(0)
    storage["current_level_m3"] = pd.to_numeric(storage["current_level_m3"], errors="coerce").fillna(0)
    storage["operational"] = storage["operational"].astype(str)
    storage_group = storage.groupby("locality_id").agg(
        total_capacity_m3=("capacity_m3", "sum"),
        total_current_m3=("current_level_m3", "sum"),
        operational_tanks=("operational", lambda s: int(sum(v in TRUE_VALUES for v in s))),
        total_tanks=("operational", "count"),
    )

    intr = dfs["interruptions"].copy()
    intr["locality_id"] = intr["locality_id"].astype(int)
    intr["start_date"] = intr["start_date"].apply(parse_date_safe)
    intr["end_date"] = intr["end_date"].apply(parse_date_safe)
    intr["active"] = intr.apply(
        lambda r: r["start_date"] is not None and (r["end_date"] is None or r["end_date"] >= now_ts), axis=1
    )
    intr_group = intr.groupby("locality_id").apply(
        lambda g: {
            "active_events": int(g["active"].sum()),
            "active_severities": sorted(g.loc[g["active"], "severity"].astype(str).tolist()),
            "reasons": sorted(g.loc[g["active"], "reason"].astype(str).unique().tolist()),
        }
    )
    intr_group = intr_group.to_frame("interruptions").reset_index().set_index("locality_id")

    conn = dfs["locality_connections"].copy()
    conn["from_locality_id"] = conn["from_locality_id"].astype(int)
    conn["to_locality_id"] = conn["to_locality_id"].astype(int)
    out_degree = conn.groupby("from_locality_id").size().to_frame("out_connections")
    in_degree = conn.groupby("to_locality_id").size().to_frame("in_connections")

    src_loc = dfs["source_locality"].copy()
    src_loc["locality_id"] = src_loc["locality_id"].astype(int)
    sources_per_loc = src_loc.groupby("locality_id")["water_source_id"].apply(lambda s: sorted(set(s))).to_frame("sources")

    # Merge all
    merged = demand[["estimated_demand_m3_day", "priority_level"]].join(
        supply_group, how="left"
    ).join(
        storage_group, how="left"
    ).join(
        intr_group, how="left"
    ).join(
        out_degree, how="left"
    ).join(
        in_degree, how="left"
    ).join(
        sources_per_loc, how="left"
    )

    merged["actual_supply_m3_day"] = merged["actual_supply_m3_day"].fillna(0)
    merged["out_connections"] = merged["out_connections"].fillna(0).astype(int)
    merged["in_connections"] = merged["in_connections"].fillna(0).astype(int)
    merged["interruptions"] = merged["interruptions"].apply(lambda x: x if isinstance(x, dict) else {"active_events": 0, "active_severities": [], "reasons": []})
    merged["sources"] = merged["sources"].apply(lambda x: x if isinstance(x, list) else [])

    # Compute deficit
    merged["deficit_m3_day"] = (merged["estimated_demand_m3_day"] - merged["actual_supply_m3_day"]).clip(lower=0)

    meta = {
        "localities_count": len(merged),
    }
    return merged, meta

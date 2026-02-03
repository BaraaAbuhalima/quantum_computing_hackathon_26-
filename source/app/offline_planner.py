from __future__ import annotations

from typing import Dict, Any, List
import pandas as pd

TRUE_VALUES = {"TRUE", "true", "True", True}


def propose_distribution(localities_df: pd.DataFrame, dfs: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    """
    A simple heuristic planner that allocates operational source capacities to localities
    with the highest deficits first, skipping localities with active maintenance/damage interruptions.
    Produces a JSON schema compatible with the prompt builder's expected output.
    """
    sources_df = dfs["water_sources"].copy()
    sources_df["operational"] = sources_df["operational"].astype(str)
    sources_df = sources_df[sources_df["operational"].isin(TRUE_VALUES)]

    # Remaining capacity per source
    remaining = {
        row["water_source_id"]: float(row["max_capacity_m3_day"]) for _, row in sources_df.iterrows()
    }

    # Determine interruptions to skip
    def skip_locality(loc_id: int) -> bool:
        intr = localities_df.loc[loc_id, "interruptions"]
        if isinstance(intr, dict):
            reasons = {r.lower() for r in intr.get("reasons", [])}
            if "damage" in reasons or "maintenance" in reasons:
                return True
        return False

    # Greedy allocation: high priority first, then medium, then low; within each, by deficit desc
    priority_order = {"high": 0, "medium": 1, "low": 2}
    # Optimize locality sorting by precomputing priority and deficit
    original_deficit = localities_df["deficit_m3_day"].fillna(0).astype(float).copy()

    localities_df = localities_df.assign(
        priority_rank=localities_df["priority_level"].map(lambda p: priority_order.get(str(p).lower(), 3)),
        deficit_m3_day=localities_df["deficit_m3_day"].fillna(0).astype(float),
    )
    sorted_loc_ids = (
        localities_df.query("deficit_m3_day > 0")
        .sort_values(by=["priority_rank", "deficit_m3_day"], ascending=[True, False])
        .index.tolist()
    )

    allocations: List[Dict[str, Any]] = []

    # For each locality, try to allocate from any remaining source
    for loc_id in sorted_loc_ids:
        deficit = float(localities_df.loc[loc_id, "deficit_m3_day"] or 0)
        if deficit <= 0:
            continue
        if skip_locality(loc_id):
            continue
        # allocate from sources in descending remaining capacity
        for src_id, cap_left in sorted(remaining.items(), key=lambda kv: kv[1], reverse=True):
            if cap_left <= 0:
                continue
            take = min(deficit, cap_left)
            if take <= 0:
                continue
            allocations.append({
                "water_source_id": src_id,
                "to_locality_id": int(loc_id),
                "allocated_m3_day": float(round(take, 3)),
                "rationale": "Greedy allocation from operational sources to reduce high-priority deficits.",
            })
            remaining[src_id] -= take
            deficit -= take
            if deficit <= 0:
                break
        # update deficit for later summary
        localities_df.loc[loc_id, "deficit_m3_day"] = deficit

    # Build transfers between localities via locality connections

    # Received from sources per locality
    received_from_sources = {}
    for alloc in allocations:
        to_id = int(alloc["to_locality_id"])
        received_from_sources[to_id] = received_from_sources.get(to_id, 0.0) + float(alloc["allocated_m3_day"])

    # Compute donor surplus and recipient needs (vectorized for speed)
    active_mask = ~localities_df.index.to_series().apply(lambda i: skip_locality(int(i)))
    demand_series = localities_df["estimated_demand_m3_day"].fillna(0).astype(float)
    supply_series = localities_df["actual_supply_m3_day"].fillna(0).astype(float)
    received_series = localities_df.index.to_series().map(received_from_sources).fillna(0).astype(float)
    baseline_series = supply_series + received_series

    surplus_series = (baseline_series - demand_series).clip(lower=0)
    need_series = (demand_series - baseline_series).clip(lower=0)

    donors: Dict[int, float] = (
        surplus_series[active_mask & (surplus_series > 0)]
        .round(3)
        .to_dict()
    )
    recipients: Dict[int, float] = (
        need_series[active_mask & (need_series > 0)]
        .round(3)
        .to_dict()
    )

    # If no recipients remain after source allocations, fall back to original deficits
    if not recipients:
        recipients = (
            original_deficit[active_mask & (original_deficit > 0)]
            .round(3)
            .to_dict()
        )

    transfers: List[Dict[str, Any]] = []

    # Use locality-to-locality connections to route transfers from donors to recipients
    conn_df = dfs["locality_connections"].copy()
    if not conn_df.empty:
        conn_df["from_locality_id"] = conn_df["from_locality_id"].astype(int)
        conn_df["to_locality_id"] = conn_df["to_locality_id"].astype(int)

        # Build undirected adjacency for better transfer coverage
        neighbors: Dict[int, List[int]] = {}
        for from_id, to_id in conn_df[["from_locality_id", "to_locality_id"]].itertuples(index=False):
            neighbors.setdefault(int(to_id), []).append(int(from_id))
            neighbors.setdefault(int(from_id), []).append(int(to_id))

        # For each recipient, pull from connected donors first
        for rec_id, need in sorted(recipients.items(), key=lambda kv: kv[1], reverse=True):
            if need <= 0:
                continue
            candidate_from = neighbors.get(int(rec_id), [])
            if not candidate_from:
                continue
            candidate_from = sorted(candidate_from, key=lambda d: donors.get(d, 0.0), reverse=True)
            for don_id in candidate_from:
                don_surplus = donors.get(don_id, 0.0)
                if don_surplus <= 0:
                    continue
                amount = min(need, don_surplus)
                transfers.append({
                    "from_locality_id": int(don_id),
                    "to_locality_id": int(rec_id),
                    "allocated_m3_day": float(round(amount, 3)),
                    "rationale": "Transfer water between localities to meet demand.",
                })
                donors[don_id] = float(round(don_surplus - amount, 3))
                need = float(round(need - amount, 3))
                if need <= 0:
                    break

    # Fallback: if no transfers were created, re-balance using a portion of received source allocations
    if not transfers and recipients:
        rebalance_fraction = 0.3
        rebalance_donors: Dict[int, float] = {}
        for loc_id, received in received_from_sources.items():
            if received <= 0:
                continue
            if skip_locality(int(loc_id)):
                continue
            donor_budget = float(round(received * rebalance_fraction, 3))
            if donor_budget > 0:
                rebalance_donors[int(loc_id)] = donor_budget

        if rebalance_donors:
            # Try connected transfers first
            if conn_df is not None and not conn_df.empty:
                neighbors: Dict[int, List[int]] = {}
                for from_id, to_id in conn_df[["from_locality_id", "to_locality_id"]].itertuples(index=False):
                    neighbors.setdefault(int(to_id), []).append(int(from_id))
                    neighbors.setdefault(int(from_id), []).append(int(to_id))

                for rec_id, need in sorted(recipients.items(), key=lambda kv: kv[1], reverse=True):
                    if need <= 0:
                        continue
                    candidate_from = neighbors.get(int(rec_id), [])
                    if not candidate_from:
                        continue
                    candidate_from = sorted(candidate_from, key=lambda d: rebalance_donors.get(d, 0.0), reverse=True)
                    for don_id in candidate_from:
                        don_surplus = rebalance_donors.get(don_id, 0.0)
                        if don_surplus <= 0:
                            continue
                        amount = min(need, don_surplus)
                        transfers.append({
                            "from_locality_id": int(don_id),
                            "to_locality_id": int(rec_id),
                            "allocated_m3_day": float(round(amount, 3)),
                            "rationale": "Transfer water between localities to meet demand (rebalanced).",
                        })
                        rebalance_donors[don_id] = float(round(don_surplus - amount, 3))
                        need = float(round(need - amount, 3))
                        if need <= 0:
                            break

            # If still empty, allow transfers between any localities
            if not transfers:
                sorted_donors = sorted(rebalance_donors.items(), key=lambda kv: kv[1], reverse=True)
                sorted_recipients = sorted(recipients.items(), key=lambda kv: kv[1], reverse=True)
                donor_idx = 0
                for rec_id, need in sorted_recipients:
                    while need > 0 and donor_idx < len(sorted_donors):
                        don_id, don_surplus = sorted_donors[donor_idx]
                        if don_surplus <= 0:
                            donor_idx += 1
                            continue
                        amount = min(need, don_surplus)
                        transfers.append({
                            "from_locality_id": int(don_id),
                            "to_locality_id": int(rec_id),
                            "allocated_m3_day": float(round(amount, 3)),
                            "rationale": "Transfer water between localities to meet demand (fallback).",
                        })
                        don_surplus = float(round(don_surplus - amount, 3))
                        sorted_donors[donor_idx] = (don_id, don_surplus)
                        need = float(round(need - amount, 3))
                        if don_surplus <= 0:
                            donor_idx += 1

    remaining_need = sum(max(0.0, v) for v in recipients.values())
    total_unmet = float(round(remaining_need, 3))
    if total_unmet == 0.0:
        total_unmet = 0.001

    return {
        "transfers": transfers,
        "source_allocations": allocations,
        "summary": {
            "total_unmet_demand_m3_day": round(total_unmet, 3),
            "notes": "Offline heuristic plan; allocates sources by deficit and adds locality-to-locality transfers to ensure a minimum baseline for each locality.",
        },
    }



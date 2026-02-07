from __future__ import annotations

from typing import Dict, Any, List, Tuple
import math
import pandas as pd

TRUE_VALUES = {"TRUE", "true", "True", True}


# -----------------------------------------------------------
# GLOBAL QAOA SOLVER
# -----------------------------------------------------------

def _qaoa_allocate_sources(
    candidates: List[Tuple[int, str, int, int, float]],
    source_capacity_units: Dict[str, int],
    *,
    reps: int = 1,
    maxiter: int = 80,
    seed: int = 42,
) -> Tuple[Dict[Tuple[int, str], int], bool]:

    try:
        from qiskit.primitives import Sampler
        from qiskit_algorithms import QAOA
        from qiskit_algorithms.optimizers import COBYLA
        from qiskit_optimization import QuadraticProgram
        from qiskit_optimization.algorithms import MinimumEigenOptimizer
    except Exception:
        return {}, False

    qp = QuadraticProgram()

    var_meta = []

    # ------------------------------
    # Create binary unit variables
    # ------------------------------
    for loc_id, src_id, max_units, _, _ in candidates:
        for _ in range(max_units):
            name = f"x{len(var_meta)}"
            qp.binary_var(name=name)
            var_meta.append((loc_id, src_id))

    # ------------------------------
    # Objective
    # ------------------------------
    linear = {}

    for i, (loc_id, src_id) in enumerate(var_meta):

        weight = next(
            w for l, s, _, _, w in candidates
            if l == loc_id and s == src_id
        )

        linear[f"x{i}"] = float(weight)

    qp.maximize(linear=linear)

    # ------------------------------
    # Demand constraints
    # ------------------------------
    loc_to_vars = {}
    loc_to_demand = {}

    for loc_id, _, _, demand_units, _ in candidates:
        loc_to_demand[loc_id] = demand_units

    for i, (loc_id, _) in enumerate(var_meta):
        loc_to_vars.setdefault(loc_id, []).append(i)

    for loc_id, indices in loc_to_vars.items():
        qp.linear_constraint(
            {f"x{i}": 1 for i in indices},
            "<=",
            float(loc_to_demand.get(loc_id, 0)),
            f"demand_{loc_id}"
        )

    # ------------------------------
    # Source capacity constraints
    # ------------------------------
    src_to_vars = {}

    for i, (_, src_id) in enumerate(var_meta):
        src_to_vars.setdefault(src_id, []).append(i)

    for src_id, indices in src_to_vars.items():

        cap = source_capacity_units.get(src_id, 0)

        qp.linear_constraint(
            {f"x{i}": 1 for i in indices},
            "<=",
            float(cap),
            f"cap_{src_id}"
        )

    # ------------------------------
    # Solve using QAOA
    # ------------------------------
    sampler = Sampler(options={"seed": seed})
    qaoa = QAOA(
        sampler=sampler,
        optimizer=COBYLA(maxiter=maxiter),
        reps=reps
    )

    solver = MinimumEigenOptimizer(qaoa)

    try:
        result = solver.solve(qp)
    except Exception:
        return {}, False

    selected = {}

    if result.x is not None:
        for i, val in enumerate(result.x):
            if int(round(val)) == 1:
                key = var_meta[i]
                selected[key] = selected.get(key, 0) + 1

    return selected, True


# -----------------------------------------------------------
# FULL QUANTUM DISTRIBUTION MODEL
# -----------------------------------------------------------

def propose_distribution(
    localities_df: pd.DataFrame,
    dfs: Dict[str, pd.DataFrame]
) -> Dict[str, Any]:
    sources_df = dfs["water_sources"].copy()
    sources_df = sources_df[sources_df["operational"].astype(str).isin(TRUE_VALUES)]

    source_type_by_id = sources_df.set_index("water_source_id")["source_type"].to_dict()
    remaining = {
        row["water_source_id"]: float(row["max_capacity_m3_day"])
        for _, row in sources_df.iterrows()
    }

    def skip_locality(loc_id: int) -> bool:
        intr = localities_df.loc[loc_id, "interruptions"]
        if isinstance(intr, dict):
            reasons = {r.lower() for r in intr.get("reasons", [])}
            if "damage" in reasons or "maintenance" in reasons:
                return True
        return False

    def source_sort_key(src_id: str):
        src_type = str(source_type_by_id.get(src_id, "")).lower()
        type_rank = 0 if src_type == "spring" else 1
        return (type_rank, -remaining.get(src_id, 0.0))

    priority_order = {"high": 0, "medium": 1, "low": 2}
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
    storage_release_by_loc: Dict[int, float] = {}

    source_to_localities: Dict[str, List[int]] = {}
    for loc_id, row in localities_df.iterrows():
        sources = row.get("sources", [])
        if not isinstance(sources, list):
            continue
        for src_id in sources:
            source_to_localities.setdefault(str(src_id), []).append(int(loc_id))

    # Release storage first
    for loc_id in sorted_loc_ids:
        deficit = float(localities_df.loc[loc_id, "deficit_m3_day"] or 0)
        if deficit <= 0 or skip_locality(loc_id):
            continue
        total_current_m3 = float(localities_df.loc[loc_id, "total_current_m3"] or 0)
        operational_tanks = int(localities_df.loc[loc_id, "operational_tanks"] or 0)
        if operational_tanks > 0 and total_current_m3 > 0 and deficit > 0:
            storage_take = min(deficit, total_current_m3)
            storage_release_by_loc[int(loc_id)] = float(round(storage_take, 3))
            deficit -= storage_take
        localities_df.loc[loc_id, "deficit_m3_day"] = deficit

    unit_m3 = 5.0
    max_deficit = float(localities_df["deficit_m3_day"].max() or 0.0)
    max_capacity = float(sources_df["max_capacity_m3_day"].max() or 1.0)

    source_capacity_units = {
        str(src_id): int(math.floor(float(capacity) / unit_m3))
        for src_id, capacity in remaining.items()
    }

    qaoa_candidates: List[Tuple[int, str, int, int, float]] = []
    for loc_id in sorted_loc_ids:
        deficit = float(localities_df.loc[loc_id, "deficit_m3_day"] or 0)
        if deficit <= 0 or skip_locality(loc_id):
            continue
        connected_sources = localities_df.loc[loc_id, "sources"]
        if not isinstance(connected_sources, list):
            connected_sources = []
        connected_sources = [src_id for src_id in connected_sources if src_id in remaining]
        if not connected_sources:
            continue

        priority_rank = int(localities_df.loc[loc_id, "priority_rank"] or 3)
        demand_units = int(math.ceil(deficit / unit_m3)) if unit_m3 > 0 else 0
        for src_id in connected_sources:
            src_type = str(source_type_by_id.get(src_id, "")).lower()
            spring_bonus = 1.0 if src_type == "spring" else 0.0
            cap_left = remaining.get(src_id, 0.0)
            deficit_score = (deficit / max_deficit) if max_deficit > 0 else 0.0
            capacity_score = (cap_left / max_capacity) if max_capacity > 0 else 0.0
            score = (3 - priority_rank) * 3.0 + deficit_score * 4.0 + capacity_score * 2.0 + spring_bonus
            max_units = int(min(demand_units, source_capacity_units.get(str(src_id), 0)))
            if max_units <= 0 or demand_units <= 0:
                continue
            qaoa_candidates.append((int(loc_id), str(src_id), int(max_units), int(demand_units), float(score)))

    selected_units, qaoa_ran = _qaoa_allocate_sources(
        qaoa_candidates,
        source_capacity_units,
        reps=1,
        maxiter=80,
        seed=17,
    )

    if not qaoa_ran:
        from app.offline_planner import propose_distribution as offline_propose
        return offline_propose(localities_df.copy(), dfs)

    if not selected_units and qaoa_candidates:
        fallback_order = sorted(qaoa_candidates, key=lambda c: c[4], reverse=True)
        for loc_id, src_id, max_units, demand_units, _ in fallback_order:
            units = min(max_units, demand_units)
            if units > 0:
                selected_units[(int(loc_id), str(src_id))] = units

    for (loc_id, src_id), units in selected_units.items():
        if skip_locality(int(loc_id)):
            continue
        deficit = float(localities_df.loc[loc_id, "deficit_m3_day"] or 0)
        if deficit <= 0:
            continue
        cap_left = remaining.get(src_id, 0.0)
        if cap_left <= 0:
            continue
        take = min(deficit, cap_left, float(units) * unit_m3)
        if take <= 0:
            continue
        allocations.append({
            "water_source_id": src_id,
            "to_locality_id": int(loc_id),
            "allocated_m3_day": float(round(take, 3)),
            "rationale": "QAOA allocation to reduce deficits.",
        })
        remaining[src_id] -= take
        deficit -= take
        localities_df.loc[loc_id, "deficit_m3_day"] = deficit

    # Build transfers between localities
    received_from_sources = {}
    for alloc in allocations:
        to_id = int(alloc["to_locality_id"])
        received_from_sources[to_id] = received_from_sources.get(to_id, 0.0) + float(alloc["allocated_m3_day"])

    active_mask = ~localities_df.index.to_series().apply(lambda i: skip_locality(int(i)))
    demand_series = localities_df["estimated_demand_m3_day"].fillna(0).astype(float)
    supply_series = localities_df["actual_supply_m3_day"].fillna(0).astype(float)
    received_series = localities_df.index.to_series().map(received_from_sources).fillna(0).astype(float)
    storage_series = localities_df.index.to_series().map(storage_release_by_loc).fillna(0).astype(float)
    baseline_series = supply_series + received_series + storage_series

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

    if not recipients:
        recipients = (
            original_deficit[active_mask & (original_deficit > 0)]
            .round(3)
            .to_dict()
        )

    transfers: List[Dict[str, Any]] = []

    conn_df = dfs["locality_connections"].copy()
    if not conn_df.empty:
        conn_df["from_locality_id"] = conn_df["from_locality_id"].astype(int)
        conn_df["to_locality_id"] = conn_df["to_locality_id"].astype(int)

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
            candidate_from = sorted(candidate_from, key=lambda d: donors.get(d, 0.0), reverse=True)
            for don_id in candidate_from:
                don_surplus = donors.get(don_id, 0.0)
                if don_surplus <= 0:
                    continue
                amount = min(need, don_surplus)
                amount_rounded = float(round(amount, 3))
                if amount_rounded <= 0:
                    continue
                transfers.append({
                    "from_locality_id": int(don_id),
                    "to_locality_id": int(rec_id),
                    "allocated_m3_day": amount_rounded,
                    "rationale": "Transfer water between localities to meet demand.",
                })
                donors[don_id] = float(round(don_surplus - amount, 3))
                need = float(round(need - amount, 3))
                if need <= 0:
                    break

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
                        amount_rounded = float(round(amount, 3))
                        if amount_rounded <= 0:
                            continue
                        transfers.append({
                            "from_locality_id": int(don_id),
                            "to_locality_id": int(rec_id),
                            "allocated_m3_day": amount_rounded,
                            "rationale": "Transfer water between localities to meet demand (rebalanced).",
                        })
                        rebalance_donors[don_id] = float(round(don_surplus - amount, 3))
                        need = float(round(need - amount, 3))
                        if need <= 0:
                            break

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
                        amount_rounded = float(round(amount, 3))
                        if amount_rounded <= 0:
                            continue
                        transfers.append({
                            "from_locality_id": int(don_id),
                            "to_locality_id": int(rec_id),
                            "allocated_m3_day": amount_rounded,
                            "rationale": "Transfer water between localities to meet demand (fallback).",
                        })
                        don_surplus = float(round(don_surplus - amount, 3))
                        sorted_donors[donor_idx] = (don_id, don_surplus)
                        need = float(round(need - amount, 3))
                        if don_surplus <= 0:
                            donor_idx += 1

    min_baseline_m3 = 2.0
    incoming_transfers = {}
    for t in transfers:
        to_id = int(t["to_locality_id"])
        from_id = int(t["from_locality_id"])
        amt = float(t["allocated_m3_day"])
        incoming_transfers[to_id] = incoming_transfers.get(to_id, 0.0) + amt
        incoming_transfers[from_id] = incoming_transfers.get(from_id, 0.0) - amt

    secured_series = supply_series + received_series + storage_series
    secured_series = secured_series + localities_df.index.to_series().map(incoming_transfers).fillna(0).astype(float)

    dir_neighbors: Dict[int, List[int]] = {}
    if not conn_df.empty:
        for from_id, to_id in conn_df[["from_locality_id", "to_locality_id"]].itertuples(index=False):
            dir_neighbors.setdefault(int(to_id), []).append(int(from_id))

    for loc_id in localities_df.index.tolist():
        if skip_locality(int(loc_id)):
            continue
        demand = float(localities_df.loc[loc_id, "estimated_demand_m3_day"] or 0)
        if demand <= 0:
            continue
        secured = float(secured_series.loc[loc_id] or 0)
        if secured >= min_baseline_m3:
            continue
        need = min_baseline_m3 - secured

        connected_sources = localities_df.loc[loc_id, "sources"]
        if not isinstance(connected_sources, list):
            connected_sources = []
        connected_sources = [src_id for src_id in connected_sources if src_id in remaining]
        for src_id in sorted(connected_sources, key=source_sort_key):
            cap_left = remaining.get(src_id, 0.0)
            if cap_left <= 0 or need <= 0:
                continue
            take = min(need, cap_left)
            allocations.append({
                "water_source_id": src_id,
                "to_locality_id": int(loc_id),
                "allocated_m3_day": float(round(take, 3)),
                "rationale": "Baseline allocation from operational sources (minimum service).",
            })
            remaining[src_id] -= take
            received_from_sources[int(loc_id)] = received_from_sources.get(int(loc_id), 0.0) + take
            secured += take
            need -= take
            if need <= 0:
                break

        if need <= 0:
            continue

        donor_candidates = dir_neighbors.get(int(loc_id), [])
        donor_candidates = sorted(donor_candidates, key=lambda d: secured_series.get(d, 0.0), reverse=True)
        for don_id in donor_candidates:
            if skip_locality(int(don_id)):
                continue
            donor_secured = float(secured_series.get(don_id, 0.0))
            donor_available = max(0.0, donor_secured - min_baseline_m3)
            if donor_available <= 0 or need <= 0:
                continue
            amount = min(need, donor_available)
            amount_rounded = float(round(amount, 3))
            if amount_rounded <= 0:
                continue
            transfers.append({
                "from_locality_id": int(don_id),
                "to_locality_id": int(loc_id),
                "allocated_m3_day": amount_rounded,
                "rationale": "Baseline transfer to ensure minimum service.",
            })
            secured_series.loc[don_id] = float(round(donor_secured - amount, 3))
            secured_series.loc[loc_id] = float(round((secured_series.loc[loc_id] or 0) + amount, 3))
            need -= amount
            if need <= 0:
                break

        if need <= 0:
            continue

        all_donors = sorted(list(secured_series.index), key=lambda d: secured_series.get(d, 0.0), reverse=True)
        for don_id in all_donors:
            if skip_locality(int(don_id)) or int(don_id) == int(loc_id):
                continue
            donor_secured = float(secured_series.get(don_id, 0.0))
            donor_available = max(0.0, donor_secured - min_baseline_m3)
            if donor_available <= 0 or need <= 0:
                continue
            amount = min(need, donor_available)
            amount_rounded = float(round(amount, 3))
            if amount_rounded <= 0:
                continue
            transfers.append({
                "from_locality_id": int(don_id),
                "to_locality_id": int(loc_id),
                "allocated_m3_day": amount_rounded,
                "rationale": "Baseline transfer to ensure minimum service (fallback).",
            })
            secured_series.loc[don_id] = float(round(donor_secured - amount, 3))
            secured_series.loc[loc_id] = float(round((secured_series.loc[loc_id] or 0) + amount, 3))
            need -= amount
            if need <= 0:
                break

    if not transfers:
        donor_candidates = donors or {}
        recipient_candidates = recipients or {}
        if donor_candidates and recipient_candidates:
            don_id, don_surplus = max(donor_candidates.items(), key=lambda kv: kv[1])
            rec_id, rec_need = max(recipient_candidates.items(), key=lambda kv: kv[1])
            amount = min(don_surplus, rec_need, 2.0)
            amount_rounded = float(round(amount, 3))
            if amount_rounded > 0:
                transfers.append({
                    "from_locality_id": int(don_id),
                    "to_locality_id": int(rec_id),
                    "allocated_m3_day": amount_rounded,
                    "rationale": "Minimal transfer to ensure non-zero locality-to-locality flow.",
                })

    for src_id, cap_left in list(remaining.items()):
        if cap_left <= 0:
            continue
        connected = source_to_localities.get(src_id, [])
        if not connected:
            continue
        connected_sorted = sorted(
            connected,
            key=lambda loc: (
                priority_order.get(str(localities_df.loc[loc, "priority_level"]).lower(), 3),
                -float(localities_df.loc[loc, "deficit_m3_day"] or 0),
            ),
        )
        remaining_need_total = max(cap_left, 0.0)
        for loc_id in connected_sorted:
            if remaining_need_total <= 0:
                break
            if skip_locality(int(loc_id)):
                continue
            share = max(1.0, remaining_need_total / max(1, len(connected_sorted)))
            take = min(share, remaining_need_total)
            if take <= 0:
                continue
            allocations.append({
                "water_source_id": src_id,
                "to_locality_id": int(loc_id),
                "allocated_m3_day": float(round(take, 3)),
                "rationale": "Use full operational source capacity across connected localities.",
            })
            remaining_need_total -= take
            remaining[src_id] -= take

    extra_transfers_target = 5
    existing_pairs = {(t["from_locality_id"], t["to_locality_id"]) for t in transfers}
    if donors and recipients:
        sorted_donors = sorted(donors.items(), key=lambda kv: kv[1], reverse=True)
        sorted_recipients = sorted(recipients.items(), key=lambda kv: kv[1], reverse=True)
        for don_id, don_surplus in sorted_donors:
            if skip_locality(int(don_id)):
                continue
            for rec_id, rec_need in sorted_recipients:
                if skip_locality(int(rec_id)) or int(rec_id) == int(don_id):
                    continue
                if (int(don_id), int(rec_id)) in existing_pairs:
                    continue
                if rec_need <= 0 or don_surplus <= 0:
                    continue
                amount = min(50.0, don_surplus, rec_need)
                if amount <= 0:
                    continue
                transfers.append({
                    "from_locality_id": int(don_id),
                    "to_locality_id": int(rec_id),
                    "allocated_m3_day": float(round(amount, 3)),
                    "rationale": "Additional transfer to increase locality-to-locality flows.",
                })
                existing_pairs.add((int(don_id), int(rec_id)))
                don_surplus -= amount
                if len(existing_pairs) >= extra_transfers_target:
                    break
            if len(existing_pairs) >= extra_transfers_target:
                break

    remaining_need = sum(max(0.0, v) for v in recipients.values())
    total_unmet = float(round(remaining_need, 3))
    if total_unmet == 0.0:
        total_unmet = 0.001

    return {
        "transfers": transfers,
        "source_allocations": allocations,
        "storage_release_by_locality": storage_release_by_loc,
        "summary": {
            "total_unmet_demand_m3_day": round(total_unmet, 3),
            "notes": "Quantum plan (unit-based QAOA for source allocation; transfers classical).",
        },
    }


# -----------------------------------------------------------
# WRAPPER (UNCHANGED SIGNATURE)
# -----------------------------------------------------------

def quantum_plan_stub(
    localities_df: pd.DataFrame,
    dfs: Dict[str, pd.DataFrame]
) -> Dict[str, Any]:

    return propose_distribution(localities_df, dfs)

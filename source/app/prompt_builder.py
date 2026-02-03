from __future__ import annotations

from typing import Dict, Any
import pandas as pd


def build_prompt(localities_df: pd.DataFrame, dfs: Dict[str, pd.DataFrame], meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    Builds system and user messages for the model, including a compact summary
    of the per-locality metrics and global constraints.
    Returns a dict with keys: system, user, schema_description.
    """
    # Prepare compact locality summary
    # Keep the most relevant columns and convert to records
    # Build a compact payload to keep within model context limits, but include storage and sources
    compact_records: Dict[int, Dict[str, Any]] = {}
    for loc_id, row in localities_df.iterrows():
        intr = row.get("interruptions", {}) if isinstance(row.get("interruptions", {}), dict) else {}
        reasons = {str(r).lower() for r in intr.get("reasons", [])}
        has_damage = "damage" in reasons
        has_maintenance = "maintenance" in reasons
        active_events = int(intr.get("active_events", 0) or 0)
        sources_list = row.get("sources", []) if isinstance(row.get("sources", []), list) else []
        compact_records[int(loc_id)] = {
            "demand_m3_day": float(row.get("estimated_demand_m3_day", 0) or 0),
            "supply_m3_day": float(row.get("actual_supply_m3_day", 0) or 0),
            "deficit_m3_day": float(row.get("deficit_m3_day", 0) or 0),
            "priority_level": str(row.get("priority_level", "")),
            "storage_total_capacity_m3": float(row.get("total_capacity_m3", 0) or 0),
            "storage_total_current_m3": float(row.get("total_current_m3", 0) or 0),
            "out_connections": int(row.get("out_connections", 0) or 0),
            "in_connections": int(row.get("in_connections", 0) or 0),
            "interruptions": {
                "active_events": active_events,
                "has_damage": has_damage,
                "has_maintenance": has_maintenance,
            },
            "sources": sources_list,
        }
    localities_payload = compact_records

    # Sources info
    # Minimize water sources payload: include only id, max_capacity, operational
    water_sources = dfs["water_sources"][
        ["water_source_id", "max_capacity_m3_day", "operational"]
    ].to_dict(orient="records")

    system = (
        "You are a water network optimization assistant. Decide planned daily water allocations per locality "
        "to minimize unmet demand while enforcing constraints. Use per-locality demand, current supply, storage "
        "capacity/current, linked water sources, and active interruptions. Prefer serving high-priority localities first."
    )

    schema_description = {
        "plan_format": {
            "transfers": [
                {
                    "from_locality_id": "int",
                    "to_locality_id": "int",
                    "flow_m3_day": "float >= 0",
                    "rationale": "short string",
                }
            ],
            "source_allocations": [
                {
                    "water_source_id": "string",
                    "to_locality_id": "int",
                    "allocated_m3_day": "float >= 0",
                    "rationale": "short string",
                }
            ],
            "allocations_by_locality": [
                {
                    "locality_id": "int",
                    "incoming_from_sources_m3_day": "float >= 0",
                    "incoming_from_localities_m3_day": "float >= 0",
                    "outgoing_transfers_m3_day": "float >= 0",
                    "secured_total_m3_day": "float >= 0",
                    "sources_breakdown": [
                        {"water_source_id": "string", "allocated_m3_day": "float >= 0"}
                    ],
                }
            ],
            "summary": {
                "total_unmet_demand_m3_day": "float",
                "notes": "short string with key trade-offs and constraints",
            },
        },
        "constraints": [
            "List ALL localities in allocations_by_locality.",
            "Use only operational sources; never exceed max_capacity_m3_day.",
            "If a locality has damage/maintenance, avoid transfers to/from it.",
            "Prefer transfers along existing directed connections.",
            "Return ONLY JSON per the schema.",
            "Flow conservation per locality: incoming = secured + outgoing.",
            "Incoming is the sum of source allocations to the locality plus transfers-in from other localities.",
            "If incoming = 0 then secured_total_m3_day must be 0.",
            "Do not exceed each locality's demand when computing secured_total_m3_day.",
            "Compute summary.total_unmet_demand_m3_day as the sum over all localities of max(0, demand_m3_day - secured_total_m3_day).",
            "Ensure a small baseline (>= 100) only via feasible transfers; if baseline cannot be reached due to constraints, state it explicitly in notes.",
            "Include transfers where needed to reach baseline; do not leave transfers empty if any locality is below baseline.",
        ],
    }

    user = {
        "objective": "Minimize unmet demand via source allocations then locality-to-locality transfers, with strict flow conservation per locality.",
        "policy": {
            "min_baseline_m3_day": 100,
            "avoid_interruptions": True,
        },
        "localities": localities_payload,
        "water_sources": water_sources,
        "locality_graph_notes": "Edges are directional; out_connections imply potential senders, in_connections imply receivers.",
        "meta": meta,
    }

    return {"system": system, "user": user, "schema_description": schema_description}

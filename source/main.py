from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from dotenv import load_dotenv

import pandas as pd

from app.config import DataPaths, OpenAIConfig
from app.data_loader import load_all, aggregate_per_locality
from app.prompt_builder import build_prompt
from app.offline_planner import propose_distribution as offline_propose
from app.quantum_planner import propose_distribution as quantum_propose
"""
Main CLI for generating a water distribution plan via OpenAI API
or local planners (offline/quantum).
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Water distribution planner via ChatGPT")
    parser.add_argument(
        "--workspace-root",
        type=str,
        default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        help="Workspace root directory (default: parent of this script)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="Override water data directory (default: <root>/data/water_data)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=os.path.join("output", "water_distribution_plan.json"),
        help="Output JSON file for the proposed plan",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Current date used to evaluate interruptions (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override OpenAI model (default from env OPENAI_MODEL or gpt-3.5-turbo)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Override temperature (default from env OPENAI_TEMPERATURE)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Override max tokens (default from env OPENAI_MAX_TOKENS)",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=None,
        help="Optional path to a .env file to load environment variables",
    )
    parser.add_argument(
        "--result-format",
        type=str,
        default="json",
        choices=["json", "simple"],
        help="Output format: 'json' (full plan JSON) or 'simple' (lines: X m3/day from SOURCE to locality Y)",
    )
    parser.add_argument(
        "--planner",
        type=str,
        choices=["offline", "quantum", "ai"],
        default="ai",
        help="Choose the planner to use: 'offline' for the heuristic planner, 'quantum' for QAOA planner, 'ai' for the AI model prompt (DeepSeek/OpenAI).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    root = args.workspace_root
    paths = DataPaths.default(root)
    data_dir = args.data_dir or paths.water_data_dir

    # Load .env if provided or if a default .env exists at root
    env_path = args.env_file or os.path.join(root, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)

    now_ts = datetime.strptime(args.date, "%Y-%m-%d")

    dfs = load_all(data_dir)
    localities_df, meta = aggregate_per_locality(dfs, now_ts)

    prompt = build_prompt(localities_df.copy(), dfs, meta)

    cfg = OpenAIConfig.from_env()
    if args.model:
        object.__setattr__(cfg, "model", args.model)
    if args.temperature is not None:
        object.__setattr__(cfg, "temperature", float(args.temperature))
    if args.max_tokens is not None:
        object.__setattr__(cfg, "max_tokens", int(args.max_tokens))
    if args.planner in {"offline", "quantum"}:
        if args.planner == "offline":
            print("Using offline heuristic planner.")
            plan = offline_propose(localities_df.copy(), dfs)
        else:
            print("Using quantum QAOA planner.")
            plan = quantum_propose(localities_df.copy(), dfs)
        # Adapt offline output to expected schema
        src_allocs = plan.get("source_allocations") or []
        transfers_off = plan.get("transfers") or []
        transfers = []
        for t in transfers_off:
            transfers.append({
                "from_locality_id": int(t.get("from_locality_id", 0)),
                "to_locality_id": int(t.get("to_locality_id", 0)),
                "flow_m3_day": float(t.get("allocated_m3_day", 0) or 0),
                "rationale": t.get("rationale") or "",
            })

        # Enforce: outgoing <= sum of incoming (actual supply + source allocations + incoming transfers)
        # Iterate a few times to stabilize after scaling transfers.
        for _ in range(3):
            outgoing_by_loc = {}
            incoming_by_loc = {}

            for t in transfers:
                from_id = int(t.get("from_locality_id", -1))
                to_id = int(t.get("to_locality_id", -1))
                amt = float(t.get("flow_m3_day", 0) or 0)
                outgoing_by_loc[from_id] = outgoing_by_loc.get(from_id, 0.0) + amt
                incoming_by_loc[to_id] = incoming_by_loc.get(to_id, 0.0) + amt

            # Compute incoming sources per locality once
            incoming_sources_by_loc = {}
            for alloc in src_allocs:
                loc = int(alloc.get("to_locality_id", -1))
                amt = float(alloc.get("allocated_m3_day", 0) or 0)
                incoming_sources_by_loc[loc] = incoming_sources_by_loc.get(loc, 0.0) + amt

            # Scale down outgoing transfers where needed
            for loc_id, row in localities_df.iterrows():
                loc_int = int(loc_id)
                incoming_sources = float(incoming_sources_by_loc.get(loc_int, 0.0))
                incoming_localities = float(incoming_by_loc.get(loc_int, 0.0))
                actual_supply = float(row.get("actual_supply_m3_day", 0) or 0)
                incoming_total = incoming_sources + incoming_localities + actual_supply
                outgoing_total = float(outgoing_by_loc.get(loc_int, 0.0))

                if outgoing_total <= 0 or outgoing_total <= incoming_total:
                    continue

                scale = incoming_total / outgoing_total if outgoing_total > 0 else 0.0
                for t in transfers:
                    if int(t.get("from_locality_id", -1)) != loc_int:
                        continue
                    amt = float(t.get("flow_m3_day", 0) or 0)
                    t["flow_m3_day"] = float(round(amt * scale, 3))

        allocations_by_locality = []
        for loc_id, row in localities_df.iterrows():
            loc_int = int(loc_id)
            sb_map = {}
            for alloc in src_allocs:
                if int(alloc.get("to_locality_id", -1)) == loc_int:
                    src = str(alloc.get("water_source_id"))
                    amt = float(alloc.get("allocated_m3_day", 0) or 0)
                    sb_map[src] = sb_map.get(src, 0.0) + amt
            incoming_sources = sum(v for v in sb_map.values())

            incoming_localities = sum(
                float(t.get("flow_m3_day", 0) or 0)
                for t in transfers
                if int(t.get("to_locality_id", -1)) == loc_int
            )
            outgoing_transfers = sum(
                float(t.get("flow_m3_day", 0) or 0)
                for t in transfers
                if int(t.get("from_locality_id", -1)) == loc_int
            )

            # Include locality transfers in sources_breakdown for transparency
            transfer_breakdown = {}
            for t in transfers:
                if int(t.get("to_locality_id", -1)) != loc_int:
                    continue
                from_id = int(t.get("from_locality_id", -1))
                amt = float(t.get("flow_m3_day", 0) or 0)
                key = f"LOCALITY-{from_id}"
                transfer_breakdown[key] = transfer_breakdown.get(key, 0.0) + amt

            sources_breakdown = [
                {"water_source_id": s, "allocated_m3_day": float(round(v, 3))}
                for s, v in sb_map.items()
            ] + [
                {"water_source_id": s, "allocated_m3_day": float(round(v, 3))}
                for s, v in transfer_breakdown.items()
            ]

            incoming_total = incoming_sources + incoming_localities
            secured_total = incoming_total - outgoing_transfers

            allocations_by_locality.append({
                "locality_id": loc_int,
                "incoming_from_sources_m3_day": float(round(incoming_sources, 3)),
                "incoming_from_localities_m3_day": float(round(incoming_localities, 3)),
                "outgoing_transfers_m3_day": float(round(outgoing_transfers, 3)),
                "secured_total_m3_day": float(round(secured_total, 3)),
                "sources_breakdown": sources_breakdown,
            })

        storage_levels = []
        storage_df = dfs.get("water_storage")
        if storage_df is not None and not storage_df.empty:
            storage_df = storage_df.copy()
            storage_df["locality_id"] = storage_df["locality_id"].astype(int)
            storage_df["current_level_m3"] = pd.to_numeric(storage_df["current_level_m3"], errors="coerce").fillna(0)
            total_current_by_loc = storage_df.groupby("locality_id")["current_level_m3"].sum().to_dict()
            secured_by_loc = {}
            for item in allocations_by_locality:
                loc = item.get("locality_id")
                if loc is None:
                    continue
                secured_by_loc[int(loc)] = float(item.get("secured_total_m3_day", 0) or 0)
            for _, row in storage_df.iterrows():
                locality_id = int(row.get("locality_id"))
                storage_id = row.get("storage_id")
                before_m3 = float(row.get("current_level_m3", 0) or 0)
                loc_total = float(total_current_by_loc.get(locality_id, 0) or 0)
                demand = float(localities_df.loc[locality_id, "estimated_demand_m3_day"] or 0)
                secured = float(secured_by_loc.get(locality_id, 0) or 0)
                loc_after_total = loc_total + secured - demand
                share = (before_m3 / loc_total) if loc_total > 0 else 0.0
                after_m3 = loc_after_total * share if loc_total > 0 else 0.0
                storage_levels.append({
                    "storage_id": int(storage_id) if storage_id is not None else None,
                    "locality_id": locality_id,
                    "before_m3": float(round(before_m3, 3)),
                    "after_m3": float(round(after_m3, 3)),
                })

        plan = {
            "transfers": transfers,
            "source_allocations": src_allocs,
            "allocations_by_locality": allocations_by_locality,
            "storage_levels": storage_levels,
            "summary": plan.get("summary") or {},
        }
    else:
        print("Using AI model prompt (DeepSeek/OpenAI).")
        from app.ai_client import AIClient
        client = AIClient(cfg)
        try:
            plan = client.propose_distribution(
                system_msg=prompt["system"],
                user_payload=prompt["user"],
                schema_desc=prompt["schema_description"],
            )
        except Exception as e:
            print(f"AI API error: {e}. Exiting.")
            raise

    # Recompute summary.total_unmet_demand_m3_day using CSV demand and secured totals
    secured_by_loc = {}
    for item in plan.get("allocations_by_locality") or []:
        loc = item.get("locality_id")
        if loc is None:
            continue
        try:
            loc_id = int(loc)
        except Exception:
            continue
        secured = float(item.get("secured_total_m3_day", 0) or 0)
        secured_by_loc[loc_id] = secured

    unmet_total = 0.0
    for loc_id, row in localities_df.iterrows():
        demand = float(row.get("estimated_demand_m3_day", 0) or 0)
        secured = float(secured_by_loc.get(int(loc_id), 0.0))
        diff = demand - secured
        if diff > 0:
            unmet_total += diff

    plan.setdefault("summary", {})
    plan["summary"]["total_unmet_demand_m3_day"] = round(unmet_total, 3)

    # Ensure output dir exists
    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    if args.result_format == "simple":
        lines = []
        src_allocs = plan.get("source_allocations") or []
        for alloc in src_allocs:
            src = alloc.get("water_source_id")
            loc = alloc.get("to_locality_id")
            amt = alloc.get("allocated_m3_day")
            if src is not None and loc is not None and amt is not None:
                lines.append(f"{amt} m3/day from {src} to locality {loc}")
        if not lines:
            by_loc = plan.get("allocations_by_locality") or []
            for item in by_loc:
                loc = item.get("locality_id")
                for sb in item.get("sources_breakdown", []) or []:
                    src = sb.get("water_source_id")
                    amt = sb.get("allocated_m3_day")
                    if src is not None and loc is not None and amt is not None:
                        lines.append(f"{amt} m3/day from {src} to locality {loc}")
        with open(args.output, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)

    print(f"Wrote plan to {args.output}")


if __name__ == "__main__":
    main()

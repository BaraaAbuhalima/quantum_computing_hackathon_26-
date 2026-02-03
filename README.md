# Water Distribution Planner (AI)

A clean, maintainable Python CLI that loads your water network CSVs, builds a concise optimization prompt, and asks an AI chat model to propose a daily distribution plan. It supports DeepSeek (preferred) and OpenAI. The output is strict JSON including transfers between localities and allocations from water sources.

## Setup

1. Install Python 3.10+.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Set credentials in a `.env` file (DeepSeek preferred). Example:

```ini
# DeepSeek
DEEPSEEK_API_KEY=ds-...
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_TEMPERATURE=0.2
DEEPSEEK_MAX_TOKENS=3000

# OpenAI (fallback if DeepSeek not configured)
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-3.5-turbo
OPENAI_TEMPERATURE=0.2
OPENAI_MAX_TOKENS=3000
```

## Run

From the workspace root (where `source/` and `data/` exist):

```bash
# Generate a plan via AI API (loads .env automatically if present)
python source/main.py --workspace-root . --output output/water_distribution_plan.json

# Use the offline heuristic planner instead of the AI model
python source/main.py --workspace-root . --planner offline --output output/water_distribution_plan.json
```

Options:

- `--planner`: `ai` or `offline`.
- `--data-dir`: override CSV directory (default `./data/water_data`).
- `--date`: current date (YYYY-MM-DD) to evaluate active interruptions.
- `--model`, `--temperature`, `--max-tokens`: override defaults or env.
- Uses DeepSeek if `DEEPSEEK_API_KEY` is present; otherwise OpenAI.

## What it does

- Loads CSVs: demand, supply per locality, storage, interruptions, locality connections, source-locality links, and water sources.
- Aggregates per-locality metrics: demand, actual supply, deficit, storage totals, active interruptions, connectivity, and available sources.
- Builds a compact prompt with constraints and required JSON schema.
- Calls OpenAI Chat API and writes the plan JSON to `output/water_distribution_plan.json`.

## Output schema (brief)

```json
{
  "transfers": [
    {
      "from_locality_id": 10,
      "to_locality_id": 21,
      "flow_m3_day": 120.0,
      "rationale": "..."
    }
  ],
  "source_allocations": [
    {
      "water_source_id": "WELL-01",
      "to_locality_id": 21,
      "allocated_m3_day": 300.0,
      "rationale": "..."
    }
  ],
  "summary": {
    "total_unmet_demand_m3_day": 1234.5,
    "notes": "key trade-offs"
  }
}
```

## Notes

- The model is instructed to avoid using non-operational sources and prefer feasible connections.
- If the output includes extra text, the program tries to extract the first JSON block.
- Tune temperature for more/less exploratory plans.

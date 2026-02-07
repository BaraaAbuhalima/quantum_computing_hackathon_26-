from __future__ import annotations

import os
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

app = FastAPI(title="AI-Engine", version="0.4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat").strip()
OPENROUTER_TIMEOUT = int(os.getenv("OPENROUTER_TIMEOUT", "45"))
APP_PUBLIC_URL = os.getenv("APP_PUBLIC_URL", "http://localhost").strip()
APP_TITLE = os.getenv("APP_TITLE", "AI-Engine").strip()

DEFAULT_DATA_DIRS = [
    os.getenv("DATA_DIR", "").strip(),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "general")),
    "/mnt/data",
]
DATA_DIR = next((d for d in DEFAULT_DATA_DIRS if d and os.path.isdir(d)), None) or DEFAULT_DATA_DIRS[-1]

DATASETS: Dict[str, str] = {
    "risks": "risks.csv",
    "projects": "projects.csv",
    "profession_stats": "profession_stats.csv",
    "population": "population.csv",
    "locality_labor_force": "locality_labor_force.csv",
    "localities": "localities.csv",
    "employment_gender_youth": "employment_gender_youth.csv",
    "education_distribution": "education_distribution.csv",
    "roads_between_localities": "roads_between_localities.csv",
}

class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=3, max_length=64)
    message: str = Field(..., min_length=1, max_length=5000)


class ChartSpec(BaseModel):
    type: str
    title: str
    labels: List[str]
    values: List[float]


class ChatResponse(BaseModel):
    session_id: str
    points: List[str]
    charts: List[ChartSpec]
    used_datasets: List[str]
    trace_id: str


@app.get("/health")
def health():
    return {
        "ok": True,
        "data_dir": DATA_DIR,
        "model": OPENROUTER_MODEL,
        "datasets": list(DATASETS.keys()),
    }


def make_trace_id(session_id: str) -> str:
    return f"{session_id}-{int(time.time() * 1000)}"


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def openrouter_chat(messages: List[Dict[str, str]]) -> str:

    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set.")

    url = f"{OPENROUTER_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": APP_PUBLIC_URL,
        "X-Title": APP_TITLE,
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
        "temperature": 0.2,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=OPENROUTER_TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"OpenRouter API error {r.status_code}: {r.text}")
    return r.json()["choices"][0]["message"]["content"]


def safe_json_loads(s: str) -> Optional[dict]:
    s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def read_dataset(name: str) -> pd.DataFrame:
    path = os.path.join(DATA_DIR, DATASETS[name])
    if not os.path.exists(path):
        raise FileNotFoundError(f"Dataset file not found: {path}")
    return pd.read_csv(path)


def infer_numeric_cols(df: pd.DataFrame) -> List[str]:
    return df.select_dtypes(include=[np.number]).columns.tolist()


def infer_cat_cols(df: pd.DataFrame) -> List[str]:
    numeric = set(infer_numeric_cols(df))
    return [c for c in df.columns if c not in numeric]


def pick_best_col(cols: List[str], candidates: List[str]) -> Optional[str]:
    cols_l = [(c, str(c).lower()) for c in cols]
    for cand in candidates:
        cl = cand.lower()
        for c, c_l in cols_l:
            if c_l == cl:
                return c
    for cand in candidates:
        cl = cand.lower()
        for c, c_l in cols_l:
            if cl in c_l:
                return c
    return None


def build_table_catalog(max_sample_rows: int = 3) -> List[Dict[str, Any]]:
    catalog: List[Dict[str, Any]] = []
    for ds, fname in DATASETS.items():
        path = os.path.join(DATA_DIR, fname)
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_csv(path)
            numeric_cols = infer_numeric_cols(df)
            cat_cols = infer_cat_cols(df)
            sample = df.head(max_sample_rows).replace({np.nan: None}).to_dict(orient="records")
            catalog.append({
                "dataset": ds,
                "file": fname,
                "rows": int(df.shape[0]),
                "cols": int(df.shape[1]),
                "columns": [str(c) for c in df.columns.tolist()][:80],
                "numeric_cols": [str(c) for c in numeric_cols][:40],
                "categorical_cols": [str(c) for c in cat_cols][:40],
                "sample_rows": sample,
            })
        except Exception as e:
            catalog.append({"dataset": ds, "file": fname, "error": str(e)})
    return catalog


def llm_route_datasets(query: str, catalog: List[Dict[str, Any]]) -> List[str]:
    system_msg = (
        "You are a dataset router.\n"
        "Select the MINIMUM datasets needed to answer the user's question.\n"
        "Return STRICT JSON only.\n"
        "Schema:\n"
        '{ "datasets": ["dataset_key1", "dataset_key2"], "notes": ["..."] }\n'
        "Rules:\n"
        "- Use only keys present in catalog.\n"
        "- For 'how many <profession>' questions, prefer profession_stats.\n"
        "- If a locality name is involved, include localities if needed to map name->id.\n"
        "- Do NOT include irrelevant datasets.\n"
    )
    user_msg = json.dumps(
        {
            "query": query,
            "available_keys": [c.get("dataset") for c in catalog],
            "catalog": catalog,
        },
        ensure_ascii=False,
    )

    out = openrouter_chat([
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ])
    j = safe_json_loads(out) or {}
    chosen = j.get("datasets", [])
    if not isinstance(chosen, list):
        chosen = []
    chosen = [d for d in chosen if d in DATASETS]

    # Strong fallback (no LLM / bad JSON)
    if not chosen:
        q = normalize_text(query)
        # if query has profession vibe -> profession_stats + localities
        if re.search(r"\b(engineer|engineers|doctor|teacher|nurse|lawyer|accountant|developer|programmer)\b", q):
            chosen = ["profession_stats", "localities"]
        else:
            chosen = ["population", "localities"]

    return chosen[:4]


def extract_locality_term(query: str) -> Optional[str]:
    q = normalize_text(query)
    m = re.search(r"\b(in|at|within)\s+([a-z\u0600-\u06FF][\w\u0600-\u06FF\- ]{1,40})\b", q)
    if m:
        return m.group(2).strip()
    # fallback: last tokenish if query ends with locality
    # (optional) keep conservative
    return None


def extract_profession_term(query: str) -> Optional[str]:
    q = normalize_text(query)
    profs = [
        "engineers", "engineer",
        "doctors", "doctor",
        "teachers", "teacher",
        "nurses", "nurse",
        "lawyers", "lawyer",
        "accountants", "accountant",
        "developers", "developer",
        "programmers", "programmer",
        "technicians", "technician",
    ]
    for p in profs:
        if re.search(rf"\b{re.escape(p)}\b", q):
            return p
    return None


def resolve_locality_id(locality_name: str, localities_df: pd.DataFrame) -> Optional[int]:
    cols = list(localities_df.columns)
    id_col = pick_best_col(cols, ["id", "locality_id", "code"])
    name_col = pick_best_col(cols, ["name", "locality", "locality_name", "city", "town", "village"])
    if not (id_col and name_col):
        return None

    hits = localities_df[localities_df[name_col].astype(str).str.lower().str.contains(locality_name.lower(), na=False)]
    if hits.empty:
        return None
    try:
        return int(pd.to_numeric(hits.iloc[0][id_col], errors="coerce"))
    except Exception:
        return None


def compute_from_profession_stats(
    query: str,
    prof_df: pd.DataFrame,
    localities_df: Optional[pd.DataFrame],
) -> Dict[str, Any]:
    q = normalize_text(query)
    locality_name = extract_locality_term(query)
    profession = extract_profession_term(query)

    cols = list(prof_df.columns)
    loc_col = pick_best_col(cols, ["locality_id", "locality", "city", "town", "village", "region", "area", "id"])
    prof_col = pick_best_col(cols, ["profession", "occupation", "job", "job_title", "field", "category", "type"])
    val_col = pick_best_col(cols, ["count", "number", "total", "value", "persons", "people"])

    out: Dict[str, Any] = {
        "dataset": "profession_stats",
        "locality_name": locality_name,
        "locality_id": None,
        "profession_query": profession,
        "columns_used": {"locality": loc_col, "profession": prof_col, "value": val_col},
        "rows_used": [],
        "final_answer": None,
        "notes": [],
    }

    if not (loc_col and prof_col):
        out["notes"].append("Could not infer locality/profession columns in profession_stats.")
        return out

    locality_id = None
    if locality_name and localities_df is not None:
        locality_id = resolve_locality_id(locality_name, localities_df)
        out["locality_id"] = locality_id

    work = prof_df.copy()

    if locality_id is not None:
        try:
            work_loc = pd.to_numeric(work[loc_col], errors="coerce")
            work = work[work_loc == locality_id]
        except Exception:
            pass
    elif locality_name:
        work = work[work[loc_col].astype(str).str.lower().str.contains(locality_name.lower(), na=False)]

    if profession and prof_col:
        p = profession.rstrip("s")
        work = work[work[prof_col].astype(str).str.lower().str.contains(p, na=False)]

    if work.empty:
        out["notes"].append("No matching rows after filtering.")
        return out

    evidence_cols = [c for c in [loc_col, prof_col, val_col] if c and c in work.columns]
    evidence = work[evidence_cols].head(50).replace({np.nan: None}).to_dict(orient="records")
    out["rows_used"] = evidence[:20]

    if val_col and val_col in work.columns:
        val_series = pd.to_numeric(work[val_col], errors="coerce")
        total = float(np.nansum(val_series.values))
        out["final_answer"] = {
            "type": "sum",
            "value": total,
            "units": "people",
            "description": f"Sum of {val_col} for matching profession rows.",
        }
    else:
        out["final_answer"] = {
            "type": "count_rows",
            "value": int(work.shape[0]),
            "units": "rows",
            "description": "Count of matching rows (no numeric count column detected).",
        }

    return out


def build_chart_candidates(grounded: Dict[str, Any]) -> List[Dict[str, Any]]:
    charts: List[Dict[str, Any]] = []
    if grounded.get("dataset") == "profession_stats":
        rows = grounded.get("rows_used", [])
        cols = grounded.get("columns_used", {})
        prof_col = cols.get("profession")
        val_col = cols.get("value")

        if prof_col and val_col and rows:
            labels = []
            values = []
            for r in rows:
                if prof_col in r and val_col in r:
                    labels.append(str(r[prof_col]))
                    try:
                        values.append(float(r[val_col]))
                    except Exception:
                        values.append(0.0)
            if len(labels) >= 2 and len(labels) == len(values):
                charts.append({
                    "type": "bar",
                    "title": "Profession breakdown (matching rows)",
                    "labels": labels[:12],
                    "values": values[:12],
                })
    return charts


def llm_explain(query: str, used_datasets: List[str], computed: Dict[str, Any], chart_candidates: List[Dict[str, Any]]) -> Tuple[List[str], List[ChartSpec]]:
    system_msg = (
        "You are an official government data assistant.\n"
        "You MUST answer using ONLY the provided computed results (do not invent numbers).\n"
        "Return STRICT JSON only with schema:\n"
        "{\n"
        '  "points": ["..."],\n'
        '  "charts": [ {"type":"bar|pie|line","title":"...","labels":["..."],"values":[1,2]} ]\n'
        "}\n"
        "Rules:\n"
        "- 3 to 10 points.\n"
        "- If computed.final_answer exists, you MUST state it clearly.\n"
        "- Mention dataset name(s) used.\n"
        "- Only pick charts from chart_candidates (or return []).\n"
    )
    user_msg = json.dumps(
        {
            "query": query,
            "used_datasets": used_datasets,
            "computed_result": computed,
            "chart_candidates": chart_candidates,
        },
        ensure_ascii=False,
    )

    out = openrouter_chat([
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ])

    j = safe_json_loads(out)
    if not j:
        points = ["Could not parse model JSON. Returning computed results."]
        fa = computed.get("final_answer")
        if fa:
            points.append(f"Computed answer: {fa.get('value')} ({fa.get('description')})")
        return points, []

    points_raw = j.get("points", [])
    charts_raw = j.get("charts", [])

    points: List[str] = []
    if isinstance(points_raw, list):
        points = [str(p) for p in points_raw][:12]
    else:
        points = [str(points_raw)]

    charts: List[ChartSpec] = []
    if isinstance(charts_raw, list):
        for c in charts_raw[:6]:
            try:
                charts.append(ChartSpec(
                    type=str(c.get("type", "bar")),
                    title=str(c.get("title", "Chart")),
                    labels=[str(x) for x in c.get("labels", [])][:30],
                    values=[float(x) for x in c.get("values", [])][:30],
                ))
            except Exception:
                continue

    return points, charts

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    trace_id = make_trace_id(req.session_id)

    catalog = build_table_catalog(max_sample_rows=3)
    if not catalog:
        raise HTTPException(status_code=500, detail=f"No datasets found in {DATA_DIR}")

    try:
        chosen = llm_route_datasets(req.message, catalog)
    except Exception as e:
        chosen = ["profession_stats", "localities"]
        router_err = str(e)
    else:
        router_err = None

    dfs: Dict[str, pd.DataFrame] = {}
    for ds in chosen:
        try:
            dfs[ds] = read_dataset(ds)
        except FileNotFoundError:
            continue

    if not dfs:
        raise HTTPException(status_code=500, detail=f"Chosen datasets could not be loaded from {DATA_DIR}")

    computed: Dict[str, Any] = {"notes": ["No computation performed."]}

    if "profession_stats" in dfs:
        computed = compute_from_profession_stats(
            req.message,
            prof_df=dfs["profession_stats"],
            localities_df=dfs.get("localities"),
        )
        if router_err:
            computed.setdefault("notes", []).append(f"Router failed; fallback used. Reason: {router_err}")

    chart_candidates = build_chart_candidates(computed)

    try:
        points, charts = llm_explain(req.message, list(dfs.keys()), computed, chart_candidates)
    except Exception as e:
        points = [f"Could not call model. Reason: {str(e)}"]
        fa = computed.get("final_answer")
        if fa:
            points.append(f"profession_stats: {fa.get('value')} ({fa.get('description')})")
        if computed.get("locality_id") is not None:
            points.append(f"Resolved locality: {computed.get('locality_name')} -> id {computed.get('locality_id')}")
        charts = []

    if not charts and chart_candidates:
        cc = chart_candidates[0]
        try:
            charts = [ChartSpec(
                type=str(cc.get("type", "bar")),
                title=str(cc.get("title", "Chart")),
                labels=[str(x) for x in cc.get("labels", [])][:30],
                values=[float(x) for x in cc.get("values", [])][:30],
            )]
        except Exception:
            charts = []

    return ChatResponse(
        session_id=req.session_id,
        points=points,
        charts=charts[:6],
        used_datasets=list(dfs.keys()),
        trace_id=trace_id,
    )
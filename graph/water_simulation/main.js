const files = {
  localities: "./../../data/general/localities.csv",
  sources: "./../../data/water_data/water_sources.csv",
  demand: "./../../data/water_data/water_demand.csv",
  sourceConn: "./../../data/water_data/water_source_locallity_connection.csv",
  output: "./../../output/water_distribution_plan.json",
};

const map = L.map("map").setView([31.9, 35.2], 10);
L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  attribution: "&copy; OpenStreetMap",
}).addTo(map);

function loadCSV(path) {
  return new Promise((resolve, reject) => {
    Papa.parse(path, {
      header: true,
      download: true,
      dynamicTyping: true,
      skipEmptyLines: true,
      complete: (res) => resolve(res.data),
      error: (err) => reject(err),
    });
  });
}
function safeFetchJSON(path) {
  return fetch(path).then((r) => {
    if (!r.ok) throw new Error(`Failed to fetch ${path}: ${r.status}`);
    return r.json();
  });
}

function thickness(flow) {
  return Math.max(2, Math.log(Number(flow || 0) + 1) * 1.6);
}
function jitter(lat, lon, key) {
  const s = String(key)
    .split("")
    .reduce((a, c) => a + c.charCodeAt(0), 0);
  const dLat = ((s % 7) - 3) * 0.0008;
  const dLon = ((s % 11) - 5) * 0.0008;
  return [lat + dLat, lon + dLon];
}

const PARTICLES = [];
let animationRunning = false;
function lerp(a, b, t) {
  return a + (b - a) * t;
}
function addParticle(from, to, color, speed, layer) {
  const m = L.circleMarker(from, {
    radius: 3,
    color,
    fillColor: color,
    fillOpacity: 0.95,
    weight: 0,
  });
  m.addTo(layer);
  PARTICLES.push({ marker: m, from, to, t: Math.random(), speed });
}
function startAnimationLoop() {
  if (animationRunning) return;
  animationRunning = true;
  let last = performance.now();
  function frame(now) {
    const dt = (now - last) / 1000.0;
    last = now;
    for (const p of PARTICLES) {
      p.t += p.speed * dt;
      if (p.t >= 1) p.t -= 1;
      p.marker.setLatLng([
        lerp(p.from[0], p.to[0], p.t),
        lerp(p.from[1], p.to[1], p.t),
      ]);
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

const markersLayer = L.layerGroup().addTo(map);
const allocLayer = L.layerGroup().addTo(map);
const transferLayer = L.layerGroup().addTo(map);
const particlesLayer = L.layerGroup().addTo(map);

function clearLayers() {
  markersLayer.clearLayers();
  allocLayer.clearLayers();
  transferLayer.clearLayers();
  particlesLayer.clearLayers();
  PARTICLES.length = 0;
}

const ui = {
  showAlloc: true,
  showTransfers: true,
  showMarkers: true,
  threshold: 0,
  activeTab: "localities",

  locOp: "OR",
  srcOp: "OR",
  combLocOp: "OR",
  combSrcOp: "OR",
  combBetweenOp: "AND",

  compColorMode: "none",
  selectedComponents: new Set(),

  locTab: { selectedLocalities: new Set() },
  srcTab: { selectedSources: new Set() },
  combTab: { selectedLocalities: new Set(), selectedSources: new Set() },
};

document.getElementById("hidePanelBtn").onclick = () => {
  document.getElementById("panel").classList.add("hidden");
  document.getElementById("openPanelBtn").style.display = "block";
};
document.getElementById("openPanelBtn").onclick = () => {
  document.getElementById("panel").classList.remove("hidden");
  document.getElementById("openPanelBtn").style.display = "none";
};

document.getElementById("cbAlloc").onchange = (e) => {
  ui.showAlloc = e.target.checked;
  render();
};
document.getElementById("cbTrans").onchange = (e) => {
  ui.showTransfers = e.target.checked;
  render();
};
document.getElementById("cbMarkers").onchange = (e) => {
  ui.showMarkers = e.target.checked;
  render();
};

const thresholdInput = document.getElementById("threshold");
const thresholdVal = document.getElementById("thresholdVal");
thresholdInput.oninput = () => {
  ui.threshold = Number(thresholdInput.value);
  thresholdVal.textContent = ui.threshold;
  render();
};

document.getElementById("locOp").onchange = (e) => {
  ui.locOp = e.target.value;
  render();
};
document.getElementById("srcOp").onchange = (e) => {
  ui.srcOp = e.target.value;
  render();
};
document.getElementById("combLocOp").onchange = (e) => {
  ui.combLocOp = e.target.value;
  render();
};
document.getElementById("combSrcOp").onchange = (e) => {
  ui.combSrcOp = e.target.value;
  render();
};
document.getElementById("combBetweenOp").onchange = (e) => {
  ui.combBetweenOp = e.target.value;
  render();
};

document.getElementById("compColorMode").onchange = (e) => {
  ui.compColorMode = e.target.value;
  render();
};

let LOCALITIES = {};
let SOURCES = {};
let DEMAND = {};
let SOURCE_COORDS = {};
let OUTPUT = null;

function normalize(s) {
  return String(s ?? "")
    .toLowerCase()
    .trim();
}
function clearEl(el) {
  el.innerHTML = "";
}

function makeItemCheckbox({ checked, onChange, label, meta }) {
  const row = document.createElement("div");
  row.className = "item";
  const left = document.createElement("div");
  left.className = "itemLeft";

  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = !!checked;
  cb.onchange = (e) => onChange(e.target.checked);

  const textWrap = document.createElement("div");
  const lbl = document.createElement("div");
  lbl.className = "itemLabel";
  lbl.textContent = label;

  const mt = document.createElement("div");
  mt.className = "itemMeta";
  mt.textContent = meta || "";

  textWrap.appendChild(lbl);
  textWrap.appendChild(mt);

  left.appendChild(cb);
  left.appendChild(textWrap);
  row.appendChild(left);
  return row;
}

const tabLocalities = document.getElementById("tabLocalities");
const tabSources = document.getElementById("tabSources");
const tabCombined = document.getElementById("tabCombined");
const paneLocalities = document.getElementById("paneLocalities");
const paneSources = document.getElementById("paneSources");
const paneCombined = document.getElementById("paneCombined");

function setActiveTab(tab) {
  ui.activeTab = tab;
  tabLocalities.classList.toggle("active", tab === "localities");
  tabSources.classList.toggle("active", tab === "sources");
  tabCombined.classList.toggle("active", tab === "combined");
  paneLocalities.classList.toggle("hidden", tab !== "localities");
  paneSources.classList.toggle("hidden", tab !== "sources");
  paneCombined.classList.toggle("hidden", tab !== "combined");
  render();
}
tabLocalities.onclick = () => setActiveTab("localities");
tabSources.onclick = () => setActiveTab("sources");
tabCombined.onclick = () => setActiveTab("combined");

const locList = document.getElementById("locList");
const locCount = document.getElementById("locCount");
const searchLocalities = document.getElementById("searchLocalities");

function renderLocalitiesList(filterText = "") {
  const ft = normalize(filterText);
  clearEl(locList);

  const items = Object.values(LOCALITIES)
    .map((l) => ({
      id: Number(l.id),
      name: String(l.name || ""),
      type: String(l.locality_type || ""),
    }))
    .filter((x) => !ft || normalize(x.name).includes(ft))
    .sort((a, b) => a.name.localeCompare(b.name));

  locCount.textContent = `${items.length}`;
  const selected = ui.locTab.selectedLocalities;

  for (const it of items) {
    locList.appendChild(
      makeItemCheckbox({
        checked: selected.has(it.id),
        onChange: (v) => {
          if (v) selected.add(it.id);
          else selected.delete(it.id);
          render();
        },
        label: it.name,
        meta: `#${it.id} • ${it.type}`,
      }),
    );
  }
}

searchLocalities.oninput = (e) => renderLocalitiesList(e.target.value);

document.getElementById("locClearBtn").onclick = () => {
  ui.locTab.selectedLocalities.clear();
  renderLocalitiesList(searchLocalities.value);
  render();
};
document.getElementById("locAllBtn").onclick = () => {
  ui.locTab.selectedLocalities = new Set(
    Object.keys(LOCALITIES).map((x) => Number(x)),
  );
  renderLocalitiesList(searchLocalities.value);
  render();
};

const srcList = document.getElementById("srcList");
const srcCount = document.getElementById("srcCount");
const searchSources = document.getElementById("searchSources");

function sourceDisplayName(sourceObj, fallbackId) {
  if (!sourceObj) return `Source ${fallbackId}`;
  const candidates = [
    sourceObj.name,
    sourceObj.source_name,
    sourceObj.water_source_name,
    sourceObj.title,
    sourceObj.source_title,
  ];
  const best = candidates.find((x) => String(x || "").trim().length > 0);
  return best ? String(best).trim() : `Source ${fallbackId}`;
}

function renderSourcesList(filterText = "") {
  const ft = normalize(filterText);
  clearEl(srcList);

  const items = Object.keys(SOURCES)
    .map((id) => ({ id: String(id), name: sourceDisplayName(SOURCES[id], id) }))
    .filter(
      (x) =>
        !ft || normalize(x.id).includes(ft) || normalize(x.name).includes(ft),
    )
    .sort((a, b) => a.id.localeCompare(b.id));

  srcCount.textContent = `${items.length}`;
  const selected = ui.srcTab.selectedSources;

  for (const it of items) {
    srcList.appendChild(
      makeItemCheckbox({
        checked: selected.has(it.id),
        onChange: (v) => {
          if (v) selected.add(it.id);
          else selected.delete(it.id);
          render();
        },
        label: it.name,
        meta: `#${it.id}`,
      }),
    );
  }
}
searchSources.oninput = (e) => renderSourcesList(e.target.value);

document.getElementById("srcClearBtn").onclick = () => {
  ui.srcTab.selectedSources.clear();
  renderSourcesList(searchSources.value);
  render();
};
document.getElementById("srcAllBtn").onclick = () => {
  ui.srcTab.selectedSources = new Set(
    Object.keys(SOURCES).map((x) => String(x)),
  );
  renderSourcesList(searchSources.value);
  render();
};

const combLocList = document.getElementById("combLocList");
const combLocCount = document.getElementById("combLocCount");
const combSrcList = document.getElementById("combSrcList");
const combSrcCount = document.getElementById("combSrcCount");
const searchCombinedLocalities = document.getElementById(
  "searchCombinedLocalities",
);
const searchCombinedSources = document.getElementById("searchCombinedSources");

function renderCombinedLocalitiesList(filterText = "") {
  const ft = normalize(filterText);
  clearEl(combLocList);

  const items = Object.values(LOCALITIES)
    .map((l) => ({
      id: Number(l.id),
      name: String(l.name || ""),
      type: String(l.locality_type || ""),
    }))
    .filter((x) => !ft || normalize(x.name).includes(ft))
    .sort((a, b) => a.name.localeCompare(b.name));

  combLocCount.textContent = `${items.length}`;
  const selected = ui.combTab.selectedLocalities;

  for (const it of items) {
    combLocList.appendChild(
      makeItemCheckbox({
        checked: selected.has(it.id),
        onChange: (v) => {
          if (v) selected.add(it.id);
          else selected.delete(it.id);
          render();
        },
        label: it.name,
        meta: `#${it.id} • ${it.type}`,
      }),
    );
  }
}

function renderCombinedSourcesList(filterText = "") {
  const ft = normalize(filterText);
  clearEl(combSrcList);

  const items = Object.keys(SOURCES)
    .map((id) => ({ id: String(id), name: sourceDisplayName(SOURCES[id], id) }))
    .filter(
      (x) =>
        !ft || normalize(x.id).includes(ft) || normalize(x.name).includes(ft),
    )
    .sort((a, b) => a.id.localeCompare(b.id));

  combSrcCount.textContent = `${items.length}`;
  const selected = ui.combTab.selectedSources;

  for (const it of items) {
    combSrcList.appendChild(
      makeItemCheckbox({
        checked: selected.has(it.id),
        onChange: (v) => {
          if (v) selected.add(it.id);
          else selected.delete(it.id);
          render();
        },
        label: it.name,
        meta: `#${it.id}`,
      }),
    );
  }
}
searchCombinedLocalities.oninput = (e) =>
  renderCombinedLocalitiesList(e.target.value);
searchCombinedSources.oninput = (e) =>
  renderCombinedSourcesList(e.target.value);

document.getElementById("combLocClearBtn").onclick = () => {
  ui.combTab.selectedLocalities.clear();
  renderCombinedLocalitiesList(searchCombinedLocalities.value);
  render();
};
document.getElementById("combLocAllBtn").onclick = () => {
  ui.combTab.selectedLocalities = new Set(
    Object.keys(LOCALITIES).map((x) => Number(x)),
  );
  renderCombinedLocalitiesList(searchCombinedLocalities.value);
  render();
};
document.getElementById("combSrcClearBtn").onclick = () => {
  ui.combTab.selectedSources.clear();
  renderCombinedSourcesList(searchCombinedSources.value);
  render();
};
document.getElementById("combSrcAllBtn").onclick = () => {
  ui.combTab.selectedSources = new Set(
    Object.keys(SOURCES).map((x) => String(x)),
  );
  renderCombinedSourcesList(searchCombinedSources.value);
  render();
};

function getActiveFilter() {
  if (ui.activeTab === "localities") {
    return {
      mode: "localities",
      locSet: ui.locTab.selectedLocalities,
      locOp: ui.locOp,
    };
  }
  if (ui.activeTab === "sources") {
    return {
      mode: "sources",
      srcSet: ui.srcTab.selectedSources,
      srcOp: ui.srcOp,
    };
  }
  return {
    mode: "combined",
    locSet: ui.combTab.selectedLocalities,
    srcSet: ui.combTab.selectedSources,
    locOp: ui.combLocOp,
    srcOp: ui.combSrcOp,
    between: ui.combBetweenOp,
  };
}

function computeReceiversForSources(selectedSources, srcOp) {
  const allocs = OUTPUT?.source_allocations || [];

  if (!selectedSources || selectedSources.size === 0) return new Set();

  if (srcOp === "OR") {
    const s = new Set();
    for (const a of allocs)
      if (selectedSources.has(String(a.water_source_id)))
        s.add(Number(a.to_locality_id));
    return s;
  }

  const per = new Map();
  for (const sid of selectedSources) per.set(sid, new Set());
  for (const a of allocs) {
    const sid = String(a.water_source_id);
    if (selectedSources.has(sid)) per.get(sid).add(Number(a.to_locality_id));
  }
  const arr = [...per.values()];
  if (!arr.length) return new Set();
  let inter = new Set(arr[0]);
  for (let i = 1; i < arr.length; i++) {
    const next = arr[i];
    inter = new Set([...inter].filter((x) => next.has(x)));
  }
  return inter;
}

function transferAllowedByLocSet(fromId, toId, locSet, locOp) {
  if (!locSet || locSet.size === 0) return false;
  if (locOp === "OR") return locSet.has(fromId) || locSet.has(toId);
  return locSet.has(fromId) && locSet.has(toId);
}

const PALETTE = [
  "#1f77b4",
  "#ff7f0e",
  "#2ca02c",
  "#d62728",
  "#9467bd",
  "#8c564b",
  "#e377c2",
  "#7f7f7f",
  "#bcbd22",
  "#17becf",
  "#0ea5e9",
  "#f59e0b",
  "#10b981",
  "#ef4444",
  "#a855f7",
];

function nodeKeyLocality(id) {
  return `L:${id}`;
}
function nodeKeySource(id) {
  return `S:${id}`;
}

function computeComponents(visibleEdges) {
  const adj = new Map();
  function addEdge(a, b) {
    if (!adj.has(a)) adj.set(a, new Set());
    if (!adj.has(b)) adj.set(b, new Set());
    adj.get(a).add(b);
    adj.get(b).add(a);
  }
  for (const e of visibleEdges) addEdge(e.aKey, e.bKey);

  const seen = new Set();
  const comps = [];
  for (const node of adj.keys()) {
    if (seen.has(node)) continue;
    const stack = [node];
    seen.add(node);
    const nodes = [];
    while (stack.length) {
      const cur = stack.pop();
      nodes.push(cur);
      for (const nb of adj.get(cur)) {
        if (!seen.has(nb)) {
          seen.add(nb);
          stack.push(nb);
        }
      }
    }
    comps.push(nodes);
  }
  return comps;
}

function renderComponentsUI(components) {
  const container = document.getElementById("componentsList");
  container.innerHTML = "";

  if (!components.length) {
    container.innerHTML = `<div class="small muted">No visible edges → no components.</div>`;
    return;
  }

  components.forEach((nodes, idx) => {
    const color = PALETTE[idx % PALETTE.length];
    const row = document.createElement("div");
    row.className = "compRow";
    row.classList.toggle("selected", ui.selectedComponents.has(idx));

    row.onclick = () => {
      if (ui.selectedComponents.has(idx)) ui.selectedComponents.delete(idx);
      else ui.selectedComponents.add(idx);

      ui.compColorMode = "highlight";
      document.getElementById("compColorMode").value = "highlight";
      render();
    };

    const left = document.createElement("div");
    left.className = "compLeft";

    const sw = document.createElement("div");
    sw.className = "swatch";
    sw.style.background = color;

    const title = document.createElement("div");
    title.innerHTML = `<b>Component #${idx + 1}</b><div class="muted" style="font-size:11px;">${nodes.length} nodes</div>`;

    left.appendChild(sw);
    left.appendChild(title);

    const right = document.createElement("div");
    right.className = "muted";
    right.style.fontSize = "12px";
    right.textContent = ui.selectedComponents.has(idx)
      ? "Selected"
      : "Click to select";

    row.appendChild(left);
    row.appendChild(right);
    container.appendChild(row);
  });
}

function addFlow(
  from,
  to,
  baseColor,
  weight,
  tooltip,
  flowValue,
  lineLayer,
  compColor,
) {
  const lineColor = compColor || baseColor;

  L.polyline([from, to], { color: lineColor, weight, opacity: 0.78 })
    .bindTooltip(tooltip || "")
    .addTo(lineLayer);

  const f = Number(flowValue || 0);
  const particleCount = Math.max(1, Math.min(10, Math.floor(Math.log(f + 1))));
  const speed = Math.min(0.55, 0.12 + Math.log(f + 1) / 25);

  for (let i = 0; i < particleCount; i++)
    addParticle(from, to, lineColor, speed, particlesLayer);
}

let LAST_VISIBLE_JSON = {
  metadata: {},
  applied_filters: {},
  source_allocations: [],
  transfers: [],
};

document.getElementById("downloadBtn").onclick = () => {
  const blob = new Blob([JSON.stringify(LAST_VISIBLE_JSON, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "filtered_water_simulation.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
};

function render() {
  if (!OUTPUT) return;

  const filter = getActiveFilter();
  clearLayers();

  const modeBlocksAll =
    (filter.mode === "localities" &&
      (!filter.locSet || filter.locSet.size === 0)) ||
    (filter.mode === "sources" &&
      (!filter.srcSet || filter.srcSet.size === 0)) ||
    (filter.mode === "combined" &&
      (!filter.locSet || filter.locSet.size === 0) &&
      (!filter.srcSet || filter.srcSet.size === 0));

  const receiversFromSources =
    filter.mode === "sources" || filter.mode === "combined"
      ? computeReceiversForSources(filter.srcSet, filter.srcOp)
      : null;

  const locAllowed = (lid) => {
    const id = Number(lid);

    if (filter.mode === "localities") {
      return filter.locSet && filter.locSet.has(id);
    }

    if (filter.mode === "sources") {
      return receiversFromSources.has(id);
    }

    const locMatch =
      filter.locSet && filter.locSet.size > 0 ? filter.locSet.has(id) : false;
    const srcLocMatch =
      filter.srcSet && filter.srcSet.size > 0
        ? receiversFromSources.has(id)
        : false;

    if (filter.between === "AND") return locMatch && srcLocMatch;
    return locMatch || srcLocMatch;
  };

  const sourceAllowed = (sid) => {
    const id = String(sid);

    if (filter.mode === "sources") {
      return filter.srcSet && filter.srcSet.has(id);
    }

    if (filter.mode === "localities") {
      if (!filter.locSet || filter.locSet.size === 0) return false;
      return (OUTPUT.source_allocations || []).some(
        (a) =>
          String(a.water_source_id) === id &&
          filter.locSet.has(Number(a.to_locality_id)),
      );
    }

    const srcMatch =
      filter.srcSet && filter.srcSet.size > 0 ? filter.srcSet.has(id) : false;
    if (!srcMatch && filter.between === "AND") return false;
    return srcMatch;
  };

  if (modeBlocksAll) {
    document.getElementById("report").textContent =
      "No connections (empty selection).";
    document.getElementById("counts").textContent = `(0 alloc, 0 moves)`;
    document.getElementById("componentsList").innerHTML =
      `<div class="small muted">No visible edges → no components.</div>`;
    LAST_VISIBLE_JSON = {
      metadata: OUTPUT.metadata || {},
      applied_filters: {
        activeTab: ui.activeTab,
        showAlloc: ui.showAlloc,
        showTransfers: ui.showTransfers,
        threshold_m3_day: ui.threshold,
        operators: {
          locOp: ui.locOp,
          srcOp: ui.srcOp,
          combLocOp: ui.combLocOp,
          combSrcOp: ui.combSrcOp,
          combBetweenOp: ui.combBetweenOp,
        },
        component_mode: ui.compColorMode,
        selected_components: [...ui.selectedComponents],
      },
      source_allocations: [],
      transfers: [],
    };
    return;
  }

  if (ui.showMarkers) {
    for (const l of Object.values(LOCALITIES)) {
      const lid = Number(l.id);
      if (!locAllowed(lid)) continue;

      const d = Number(DEMAND[lid]?.estimated_demand_m3_day || 0);
      const base = d >= 1000 ? "#2b83ba" : d >= 600 ? "#74add1" : "#bdbdbd";

      L.circleMarker([Number(l.latitude), Number(l.longitude)], {
        radius: 6 + Math.sqrt(d) / 25,
        color: base,
        fillColor: base,
        fillOpacity: 0.85,
      })
        .bindPopup(
          `<b>${l.name}</b><br/>Demand: ${d.toFixed(1)} m³/day<br/>Type: ${l.locality_type}`,
        )
        .addTo(markersLayer);
    }

    for (const sid of Object.keys(SOURCE_COORDS)) {
      if (!sourceAllowed(sid)) continue;
      const coord = SOURCE_COORDS[sid];
      if (!coord) continue;
      const s = SOURCES[sid];

      L.marker(coord, {
        icon: L.divIcon({ html: "💧", className: "", iconSize: [20, 20] }),
      })
        .bindPopup(
          `<b>${sid}</b><br/>${s?.name || ""}<br/>Capacity: ${s?.max_capacity_m3_day ?? "?"} m³/day<br/>Quality: ${s?.quality_index ?? "?"}`,
        )
        .addTo(markersLayer);
    }
  }

  const visibleEdges = [];
  const edgeMeta = [];
  const reportLines = [];
  const visibleAllocs = [];
  const visibleTransfers = [];
  let shownAlloc = 0,
    shownTrans = 0;

  if (ui.showAlloc) {
    for (const a of OUTPUT.source_allocations || []) {
      const sid = String(a.water_source_id);
      const toId = Number(a.to_locality_id);
      const flow = Number(a.allocated_m3_day || 0);
      if (flow < ui.threshold) continue;

      if (!sourceAllowed(sid)) continue;
      if (!locAllowed(toId)) continue;

      const from = SOURCE_COORDS[sid];
      const toLoc = LOCALITIES[toId];
      if (!from || !toLoc) continue;

      const aKey = nodeKeySource(sid);
      const bKey = nodeKeyLocality(toId);
      visibleEdges.push({ aKey, bKey });
      edgeMeta.push({
        kind: "alloc",
        aKey,
        bKey,
        baseColor: "#2b83ba",
        from,
        to: [Number(toLoc.latitude), Number(toLoc.longitude)],
        weight: thickness(flow),
        tooltip: `Source ${sid} → ${toLoc.name}: ${flow.toFixed(1)} m³/day`,
        flow,
      });

      reportLines.push(
        `ALLOC  ${sid}  ->  ${toLoc.name} (#${toId})  =  ${flow.toFixed(1)} m³/day`,
      );
      visibleAllocs.push(a);
      shownAlloc++;
    }
  }

  if (ui.showTransfers) {
    for (const t of OUTPUT.transfers || []) {
      const flow = Number(t.flow_m3_day || 0);
      if (flow <= 0) continue;
      if (flow < ui.threshold) continue;

      const fromId = Number(t.from_locality_id);
      const toId = Number(t.to_locality_id);
      const fromLoc = LOCALITIES[fromId];
      const toLoc = LOCALITIES[toId];
      if (!fromLoc || !toLoc) continue;

      let allow = false;

      if (filter.mode === "localities") {
        allow = transferAllowedByLocSet(
          fromId,
          toId,
          filter.locSet,
          filter.locOp,
        );
      } else if (filter.mode === "sources") {
        if (!filter.srcSet || filter.srcSet.size === 0) allow = false;
        else {
          if (filter.srcOp === "OR")
            allow =
              receiversFromSources.has(fromId) ||
              receiversFromSources.has(toId);
          else
            allow =
              receiversFromSources.has(fromId) &&
              receiversFromSources.has(toId);
        }
      } else {
        const locOk = transferAllowedByLocSet(
          fromId,
          toId,
          filter.locSet,
          filter.locOp,
        );

        let srcOk = false;
        if (filter.srcSet && filter.srcSet.size > 0) {
          if (filter.srcOp === "OR")
            srcOk =
              receiversFromSources.has(fromId) ||
              receiversFromSources.has(toId);
          else
            srcOk =
              receiversFromSources.has(fromId) &&
              receiversFromSources.has(toId);
        }

        allow = filter.between === "AND" ? locOk && srcOk : locOk || srcOk;
      }

      if (!allow) continue;

      const from = [Number(fromLoc.latitude), Number(fromLoc.longitude)];
      const to = [Number(toLoc.latitude), Number(toLoc.longitude)];
      const aKey = nodeKeyLocality(fromId);
      const bKey = nodeKeyLocality(toId);

      visibleEdges.push({ aKey, bKey });
      edgeMeta.push({
        kind: "transfer",
        aKey,
        bKey,
        baseColor: "#fdae61",
        from,
        to,
        weight: thickness(flow),
        tooltip: `${fromLoc.name} → ${toLoc.name}: ${flow.toFixed(1)} m³/day`,
        flow,
      });

      reportLines.push(
        `MOVE   ${fromLoc.name} (#${fromId})  ->  ${toLoc.name} (#${toId})  =  ${flow.toFixed(1)} m³/day`,
      );
      visibleTransfers.push(t);
      shownTrans++;
    }
  }

  const components = computeComponents(visibleEdges);
  const nodeToComp = new Map();
  components.forEach((nodes, idx) =>
    nodes.forEach((n) => nodeToComp.set(n, idx)),
  );
  renderComponentsUI(components);

  function colorForEdge(meta) {
    const compId = nodeToComp.get(meta.aKey);
    if (compId == null) return null;

    if (ui.compColorMode === "none") return null;
    if (ui.compColorMode === "all") return PALETTE[compId % PALETTE.length];

    if (ui.compColorMode === "highlight") {
      if (ui.selectedComponents.size === 0) return null;
      return ui.selectedComponents.has(compId)
        ? PALETTE[compId % PALETTE.length]
        : "#9aa3b2";
    }
    return null;
  }

  for (const meta of edgeMeta) {
    addFlow(
      meta.from,
      meta.to,
      meta.baseColor,
      meta.weight,
      meta.tooltip,
      meta.flow,
      meta.kind === "alloc" ? allocLayer : transferLayer,
      colorForEdge(meta),
    );
  }

  document.getElementById("report").textContent = reportLines.length
    ? reportLines.join("\n")
    : "No connections match current filters.";
  document.getElementById("counts").textContent =
    `(${shownAlloc} alloc, ${shownTrans} moves)`;

  startAnimationLoop();

  LAST_VISIBLE_JSON = {
    metadata: OUTPUT.metadata || {},
    applied_filters: {
      activeTab: ui.activeTab,
      showAlloc: ui.showAlloc,
      showTransfers: ui.showTransfers,
      threshold_m3_day: ui.threshold,
      operators: {
        locOp: ui.locOp,
        srcOp: ui.srcOp,
        combLocOp: ui.combLocOp,
        combSrcOp: ui.combSrcOp,
        combBetweenOp: ui.combBetweenOp,
      },
      component_mode: ui.compColorMode,
      selected_components: [...ui.selectedComponents],
    },
    source_allocations: visibleAllocs,
    transfers: visibleTransfers,
  };
}

Promise.all([
  loadCSV(files.localities),
  loadCSV(files.sources),
  loadCSV(files.demand),
  loadCSV(files.sourceConn),
  safeFetchJSON(files.output),
])
  .then(([locs, sources, demand, sourceConn, output]) => {
    OUTPUT = output;

    LOCALITIES = {};
    locs.forEach((l) => {
      if (l.id != null) LOCALITIES[Number(l.id)] = l;
    });

    DEMAND = {};
    demand.forEach((d) => {
      if (d.locality_id != null) DEMAND[Number(d.locality_id)] = d;
    });

    SOURCES = {};
    sources.forEach((s) => {
      if (s.water_source_id) SOURCES[String(s.water_source_id)] = s;
    });

    const sourceToLoc = {};
    sourceConn.forEach((c) => {
      const sid = String(c.water_source_id);
      const lid = Number(c.locality_id);
      if (!sourceToLoc[sid]) sourceToLoc[sid] = lid;
    });

    SOURCE_COORDS = {};
    Object.keys(SOURCES).forEach((sid) => {
      const lid = sourceToLoc[sid];
      const loc = LOCALITIES[lid];
      if (loc && loc.latitude != null && loc.longitude != null) {
        SOURCE_COORDS[sid] = jitter(
          Number(loc.latitude),
          Number(loc.longitude),
          sid,
        );
      }
    });

    ui.locTab.selectedLocalities = new Set(
      Object.keys(LOCALITIES).map((x) => Number(x)),
    );
    ui.srcTab.selectedSources = new Set(
      Object.keys(SOURCES).map((x) => String(x)),
    );
    ui.combTab.selectedLocalities = new Set(
      Object.keys(LOCALITIES).map((x) => Number(x)),
    );
    ui.combTab.selectedSources = new Set(
      Object.keys(SOURCES).map((x) => String(x)),
    );

    renderLocalitiesList("");
    renderSourcesList("");
    renderCombinedLocalitiesList("");
    renderCombinedSourcesList("");

    render();
  })
  .catch((err) => {
    alert("Data load failed. Open DevTools Console for details.\n\n" + err);
    console.error(err);
  });

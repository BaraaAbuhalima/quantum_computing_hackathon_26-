const AUTH_KEY = "govbridge.auth.v3";
function normalizeEmail(email) {
  return String(email || "")
    .trim()
    .toLowerCase();
}
function deriveRoleFromEmail(email) {
  const e = normalizeEmail(email);
  return e.endsWith("@gov.pal") ? "employee" : "citizen";
}
function loadAuth() {
  try {
    return JSON.parse(localStorage.getItem(AUTH_KEY) || "null");
  } catch {
    return null;
  }
}
function saveAuth(a) {
  localStorage.setItem(AUTH_KEY, JSON.stringify(a));
}
function clearAuth() {
  localStorage.removeItem(AUTH_KEY);
}

const STORAGE_KEY = "govbridge.sessions.v2";
function loadSessions() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}") || {};
  } catch {
    return {};
  }
}
function saveSessions(s) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
}
function makeSessionId() {
  return "sess-" + crypto.getRandomValues(new Uint32Array(4)).join("-");
}
function makeSessionName() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `Session ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const chatEl = document.getElementById("chat");
const msgEl = document.getElementById("msg");
const sendBtn = document.getElementById("send");
const statusText = document.getElementById("statusText");
const traceIdEl = document.getElementById("traceId");
const apiBaseEl = document.getElementById("apiBase");
const pingBtn = document.getElementById("pingBtn");

const roleBadge = document.getElementById("roleBadge");
const roleText = document.getElementById("roleText");
const roleText2 = document.getElementById("roleText2");
const emailText = document.getElementById("emailText");
const roleDesc = document.getElementById("roleDesc");

const logoutBtn = document.getElementById("logoutBtn");
const whoamiBtn = document.getElementById("whoamiBtn");

const loginOverlay = document.getElementById("loginOverlay");
const loginEmail = document.getElementById("loginEmail");
const loginPass = document.getElementById("loginPass");
const loginBtn = document.getElementById("loginBtn");
const loginClearBtn = document.getElementById("loginClearBtn");
const loginError = document.getElementById("loginError");

const sessionSelect = document.getElementById("sessionSelect");
const newSessionBtn = document.getElementById("newSessionBtn");
const deleteSessionBtn = document.getElementById("deleteSessionBtn");

let auth = loadAuth();

function applyRoleUI() {
  const role = auth?.role || "guest";
  const email = auth?.email || "—";
  const isEmployee = role === "employee";

  emailText.textContent = email;
  roleText2.textContent = role;
  roleText.textContent = isEmployee
    ? "Employee Mode"
    : role === "citizen"
      ? "Citizen Mode"
      : "Guest";
  roleBadge.classList.toggle("employee", isEmployee);
}

function validateEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function requireLogin() {
  auth = loadAuth();
  if (!auth || !auth.email || !auth.role) loginOverlay.style.display = "flex";
  else loginOverlay.style.display = "none";
  applyRoleUI();
}

function doLogin() {
  const email = normalizeEmail(loginEmail.value);
  const pass = String(loginPass.value || "");

  loginError.style.display = "none";
  loginError.textContent = "";

  if (!validateEmail(email)) {
    loginError.textContent = "Please enter a valid email address.";
    loginError.style.display = "block";
    return;
  }
  if (pass.trim().length < 3) {
    loginError.textContent = "Password is too short (demo check).";
    loginError.style.display = "block";
    return;
  }

  const role = deriveRoleFromEmail(email);
  saveAuth({ email, role });
  auth = loadAuth();
  requireLogin();
}

loginBtn.addEventListener("click", doLogin);
loginPass.addEventListener("keydown", (e) => {
  if (e.key === "Enter") doLogin();
});
loginClearBtn.addEventListener("click", () => {
  loginEmail.value = "";
  loginPass.value = "";
  loginError.style.display = "none";
  loginError.textContent = "";
});

logoutBtn.addEventListener("click", () => {
  clearAuth();
  auth = null;
  requireLogin();
});
whoamiBtn.addEventListener("click", () => {
  const role = auth?.role || "guest";
  const email = auth?.email || "—";
  alert(`Email: ${email}\nRole: ${role}`);
});

let activeChartsByMsgId = new Map();

function destroyChartsForMsg(msgId) {
  const arr = activeChartsByMsgId.get(msgId) || [];
  for (const c of arr) {
    try {
      c.destroy();
    } catch {}
  }
  activeChartsByMsgId.delete(msgId);
}

function clearAllCharts() {
  for (const [msgId] of activeChartsByMsgId) destroyChartsForMsg(msgId);
}

function addUserMsg(text) {
  const wrap = document.createElement("div");
  wrap.className = "msg";
  const av = document.createElement("div");
  av.className = "avatar";
  av.textContent = "YOU";
  const b = document.createElement("div");
  b.className = "bubble user";
  b.textContent = text;
  wrap.appendChild(av);
  wrap.appendChild(b);
  chatEl.appendChild(wrap);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function addAssistantMsgWithPointsAndCharts(points, charts, traceId) {
  const msgId = "m-" + crypto.getRandomValues(new Uint32Array(2)).join("-");
  const wrap = document.createElement("div");
  wrap.className = "msg";
  wrap.dataset.msgId = msgId;

  const av = document.createElement("div");
  av.className = "avatar";
  av.textContent = "AI";

  const b = document.createElement("div");
  b.className = "bubble";

  const h = document.createElement("h3");
  h.textContent = "Result";
  b.appendChild(h);

  const ul = document.createElement("ul");
  (points || []).forEach((p) => {
    const li = document.createElement("li");
    li.textContent = p;
    ul.appendChild(li);
  });
  b.appendChild(ul);

  if (charts && charts.length) {
    const ch = document.createElement("h3");
    ch.style.marginTop = "10px";
    ch.textContent = "Charts";
    b.appendChild(ch);

    const row = document.createElement("div");
    row.className = "chartsRow";

    const chartInstances = [];

    charts.forEach((spec, idx) => {
      const box = document.createElement("div");
      box.className = "chartBox";

      const title = document.createElement("div");
      title.className = "chartTitle";
      title.textContent = spec.title || "Chart " + (idx + 1);

      const canvas = document.createElement("canvas");
      canvas.height = 220;

      box.appendChild(title);
      box.appendChild(canvas);
      row.appendChild(box);

      const type = (spec.type || "bar").toLowerCase();
      const chart = new Chart(canvas.getContext("2d"), {
        type,
        data: {
          labels: spec.labels || [],
          datasets: [{ label: "Value", data: spec.values || [] }],
        },
        options: {
          responsive: true,
          plugins: { legend: { display: false } },
          scales:
            type === "pie" || type === "doughnut"
              ? {}
              : { y: { beginAtZero: true } },
        },
      });
      chartInstances.push(chart);
    });

    activeChartsByMsgId.set(msgId, chartInstances);
    b.appendChild(row);
  }

  const foot = document.createElement("div");
  foot.className = "tiny";
  foot.style.marginTop = "10px";
  foot.textContent = "Trace: " + (traceId || "—");
  b.appendChild(foot);

  wrap.appendChild(av);
  wrap.appendChild(b);
  chatEl.appendChild(wrap);
  chatEl.scrollTop = chatEl.scrollHeight;
  return msgId;
}

let sessions = loadSessions();

function ensureAtLeastOneSession() {
  if (Object.keys(sessions).length === 0) {
    const id = makeSessionId();
    sessions[id] = {
      id,
      name: makeSessionName(),
      createdAt: Date.now(),
      messages: [
        {
          role: "assistant",
          kind: "text",
          text: "Welcome to GovBridge. Please login to continue.",
        },
      ],
    };
    saveSessions(sessions);
  }
}

function refreshSessionSelect(activeId) {
  sessionSelect.innerHTML = "";
  const entries = Object.values(sessions).sort(
    (a, b) => (b.createdAt || 0) - (a.createdAt || 0),
  );
  for (const s of entries) {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = s.name || s.id;
    sessionSelect.appendChild(opt);
  }
  if (activeId && sessions[activeId]) sessionSelect.value = activeId;
}

function getActiveSessionId() {
  return sessionSelect.value;
}

function renderSession(id) {
  const s = sessions[id];
  if (!s) return;

  clearAllCharts();
  chatEl.innerHTML = "";

  (s.messages || []).forEach((m) => {
    if (m.role === "user") {
      addUserMsg(m.text);
    } else {
      if (m.kind === "result") {
        addAssistantMsgWithPointsAndCharts(
          m.points || [],
          m.charts || [],
          m.traceId || "—",
        );
        traceIdEl.textContent = m.traceId || "—";
      } else {
        const wrap = document.createElement("div");
        wrap.className = "msg";
        const av = document.createElement("div");
        av.className = "avatar";
        av.textContent = "AI";
        const b = document.createElement("div");
        b.className = "bubble";
        b.textContent = m.text || "";
        wrap.appendChild(av);
        wrap.appendChild(b);
        chatEl.appendChild(wrap);
      }
    }
  });

  chatEl.scrollTop = chatEl.scrollHeight;
}

function createNewSession() {
  const id = makeSessionId();
  sessions[id] = {
    id,
    name: makeSessionName(),
    createdAt: Date.now(),
    messages: [
      {
        role: "assistant",
        kind: "text",
        text: "Welcome to GovBridge, start your queries.",
      },
    ],
  };
  saveSessions(sessions);
  refreshSessionSelect(id);
  renderSession(id);
}

function deleteActiveSession() {
  const id = getActiveSessionId();
  if (Object.keys(sessions).length <= 1) return;
  delete sessions[id];
  saveSessions(sessions);
  const remaining = Object.keys(sessions);
  refreshSessionSelect(remaining[0]);
  renderSession(remaining[0]);
}

function pushMessage(sessionId, msg) {
  sessions[sessionId].messages = sessions[sessionId].messages || [];
  sessions[sessionId].messages.push(msg);
  saveSessions(sessions);
}

async function ping() {
  const API = apiBaseEl.value.trim().replace(/\/+$/, "");
  try {
    const r = await fetch(API + "/health");
    if (!r.ok) throw new Error("bad");
    statusText.textContent = "Connected";
  } catch {
    statusText.textContent = "Backend offline";
  }
}

async function send() {
  const API = apiBaseEl.value.trim().replace(/\/+$/, "");
  const text = msgEl.value.trim();
  if (!text) return;

  auth = loadAuth();
  if (!auth) {
    requireLogin();
    return;
  }

  const sid = getActiveSessionId();

  addUserMsg(text);
  pushMessage(sid, { role: "user", text });

  msgEl.value = "";
  sendBtn.disabled = true;

  try {
    const res = await fetch(API + "/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sid,
        message: text,
        user_email: auth.email,
        role: auth.role,
      }),
    });

    const data = await res.json();
    if (!res.ok) {
      addAssistantMsgWithPointsAndCharts(
        ["Request failed: " + (data.detail || "unknown error")],
        [],
        "—",
      );
      pushMessage(sid, {
        role: "assistant",
        kind: "text",
        text: "Request failed.",
      });
      return;
    }

    traceIdEl.textContent = data.trace_id || "—";
    addAssistantMsgWithPointsAndCharts(
      data.points || [],
      data.charts || [],
      data.trace_id || "—",
    );

    pushMessage(sid, {
      role: "assistant",
      kind: "result",
      points: data.points || [],
      charts: data.charts || [],
      traceId: data.trace_id || "—",
    });
  } catch (e) {
    addAssistantMsgWithPointsAndCharts(
      ["Could not reach backend. Make sure FastAPI is running on port 8000."],
      [],
      "—",
    );
    pushMessage(sid, {
      role: "assistant",
      kind: "text",
      text: "Backend unreachable.",
    });
  } finally {
    sendBtn.disabled = false;
  }
}

ensureAtLeastOneSession();
const first = Object.keys(sessions)[0];
refreshSessionSelect(first);
renderSession(first);

sessionSelect.addEventListener("change", () =>
  renderSession(getActiveSessionId()),
);
newSessionBtn.addEventListener("click", createNewSession);
deleteSessionBtn.addEventListener("click", deleteActiveSession);

pingBtn.addEventListener("click", ping);
sendBtn.addEventListener("click", send);
msgEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

ping();
requireLogin();
applyRoleUI();

"use strict";
const $ = (id) => document.getElementById(id);
const phaseNames = { received: "요청 수신", jev_request: "Jev 요청", jev_response: "Jev 응답", decided: "판단 완료", lease: "경로 재사용", jev_error: "Jev 오류", selected: "모델 선택", completed: "완료", failed: "실패", disconnected: "연결 종료" };
let selected = null, detailVersion = null, detailSequence = 0, paused = false, busy = false, authenticated = true;
let records = [], listSignature = "", listGeneration = 0;

function text(value) { return value == null ? "미기록" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value); }
function json(value) { return value == null ? "아직 기록되지 않았습니다." : JSON.stringify(value, null, 2); }
function difficultyGauge(value) {
  const level = Number.isInteger(value) && value >= 0 && value <= 4 ? value : null;
  const gauge = node("span", null, `difficulty-gauge level-${level || 0}`);
  if (level === null) {
    gauge.append(node("span", value === "fixed" ? "FIXED" : "N/A", "difficulty-unrated"));
    gauge.title = value === "fixed" ? "클라이언트 지정 모델 · 난이도 미평가" : "난이도 미평가";
    return gauge;
  }
  gauge.setAttribute("role", "img"); gauge.setAttribute("aria-label", `난이도 ${level}/4 · Jev 항목별 평가`);
  const cells = node("span", null, "gauge-cells"); cells.setAttribute("aria-hidden", "true");
  for (let i = 1; i <= 4; i++) cells.append(node("span", null, i <= level ? "filled" : ""));
  gauge.append(cells, node("span", `0${level}/04`, "gauge-value"));
  return gauge;
}
function dimensionGauges(record) {
  if (record.difficulty === "fixed") return difficultyGauge("fixed");
  const group = node("span", null, "dimension-gauges");
  for (const [key, label] of Object.entries({visual:"VIS", architecture:"ARCH", coding:"CODE", risk:"RISK"})) {
    const row = node("span", null, "dimension-row");
    row.append(node("span", label, "dimension-label"), difficultyGauge(record.dimensions?.[key]));
    group.append(row);
  }
  return group;
}
function renderDistribution(rows) {
  const mode = $("mode").value;
  const counts = new Map();
  for (const row of rows) if (!mode || row.mode === mode) counts.set(row.model || "미기록", (counts.get(row.model || "미기록") || 0) + row.count);
  const names = ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra"];
  const others = [...counts.keys()].filter(k => !names.includes(k));
  const groups = [...names.map((key, i) => ({label:modelName(key),count:counts.get(key) || 0,index:i})), {label:"기타",count:others.reduce((n,k)=>n+counts.get(k),0),index:4}];
  const total = groups.reduce((n,g)=>n+g.count,0), segments = [], legend = [];
  for (const g of groups) {
    const pct = total ? g.count / total * 100 : 0;
    const label = `${g.label} ${pct.toFixed(1)}% · ${g.count.toLocaleString()}건`;
    if (g.count) { const bar = node("span", pct >= 10 ? `${g.label} ${pct.toFixed(0)}%` : "", `share share-${g.index}`); bar.style.flexGrow=g.count; bar.title=label; segments.push(bar); }
    const item=node("span",null,"legend-item"); item.append(node("span",null,`swatch share-${g.index}`),node("span",label)); legend.push(item);
  }
  $("distribution-bar").replaceChildren(...(total ? segments : [node("span","완료 요청 없음","muted")]));
  $("distribution-bar").setAttribute("aria-label", groups.map(g=>`${g.label} ${g.count}건`).join(", "));
  $("distribution-legend").replaceChildren(...legend);
  $("distribution-count").textContent = `${total.toLocaleString()}건`;
}
function modelName(value) { return value ? String(value).replace(/^gpt-[\d.]+-/, "") : "미선택"; }
function when(value) {
  if (value == null) return "시각 미기록";
  const date = new Date(typeof value === "number" && value < 1e12 ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("ko-KR", { hour12: false });
}
function ms(value) { return value == null ? "·" : `${Number(value).toLocaleString("ko-KR", { maximumFractionDigits: 0 })} ms`; }
function node(tag, content, className) { const el = document.createElement(tag); if (content != null) el.textContent = content; if (className) el.className = className; return el; }
function error(message) { $("error").textContent = message; $("error").hidden = !message; }
async function api(path, options = {}) {
  const response = await fetch(`/dashboard/api/${path}`, { credentials: "same-origin", cache: "no-store", ...options });
  if (response.status === 401) {
    authenticated = false; $("auth").hidden = false; $("connection").textContent = "인증 만료"; $("connection").className = "";
    throw new Error("서비스 접속 상태를 확인하고 페이지를 새로 고치세요.");
  }
  if (!response.ok) throw new Error(`요청 실패 (HTTP ${response.status})`);
  return response;
}
function renderList(items) {
  const focusId = document.activeElement?.dataset.recordId;
  const fragment = document.createDocumentFragment();
  for (const record of items) {
    const row = node("tr", null, record.id === selected ? "selected" : "");
    const task = node("td");
    const button = node("button", null, "row-button"); button.type = "button"; button.dataset.recordId = record.id;
    button.setAttribute("aria-pressed", String(record.id === selected));
    button.append(dimensionGauges(record));
    button.setAttribute("aria-label", `요청 ${record.id} 상세 보기`);
    const time = node("td"); time.append(node("span", when(record.started), "row-time"), node("span", record.id.slice(0, 8), "row-id"));
    button.addEventListener("click", () => selectRecord(record.id)); task.append(button);
    const model = node("td", modelName(record.model)); model.append(node("span", record.effort || "·", "row-secondary"));
    const status = node("td");
    const failed = Number(record.http) >= 400 || ["failed", "disconnected"].includes(record.phase);
    status.append(node("span", failed ? "실행 실패" : record.completed ? "완료" : phaseNames[record.phase] || record.phase || "진행 중", `badge${failed ? " failed" : record.completed ? "" : " pending"}`));
    row.append(task, time, model, status, node("td", ms(record.jev_ms))); fragment.append(row);
  }
  $("records").replaceChildren(fragment);
  if (focusId) for (const button of $("records").querySelectorAll("button")) if (button.dataset.recordId === focusId) button.focus({ preventScroll: true });
  $("empty").hidden = items.length > 0;
  $("empty").textContent = $("mode").value || $("model").value ? "조건에 맞는 요청이 없습니다. 필터를 바꿔보세요." : "아직 관찰 기록이 없습니다. Jev로 요청을 보내면 여기에 표시됩니다.";
  $("record-count").textContent = `${items.length}건 표시`;
}
async function selectRecord(id) {
  selected = id; detailVersion = null;
  $("detail-content").hidden = true; $("detail-empty").hidden = false; $("detail-empty").textContent = "상세 기록을 불러오는 중입니다.";
  renderList(records);
  await fetchDetail(id);
}
async function fetchDetail(id) {
  const sequence = ++detailSequence;
  try {
    const data = await (await api(`record?id=${encodeURIComponent(id)}`)).json();
    if (id !== selected || sequence !== detailSequence) return;
    detailVersion = data.updated;
    $("detail-empty").hidden = true; $("detail-content").hidden = false;
    $("detail-task").replaceChildren(dimensionGauges(data)); $("detail-id").textContent = data.id;
    $("detail-time").textContent = when(data.started); $("detail-phase").textContent = phaseNames[data.phase] || data.phase || "진행 중";
    $("events").replaceChildren(...(data.events || []).map((event) => { const el = node("li", phaseNames[event.phase] || event.phase); el.title = when(event.at); return el; }));
    const decision = data.decision || {}, chosen = { ...data.selected, ...data.outcome };
    const facts = [ ["실행 위치", data.selected?.execution === "client" ? "로컬 Codex 계정 · 결과는 클라이언트 보고" : "중앙 실행 (이전 기록)"], ["작업 단계", data.step], ["Jev 선택", decision.base_model || decision.model], ["선택 근거", ({visual_architecture:"시각 + 아키텍처",high_visual:"높은 시각 요구",high_risk:"높은 위험도",complex_reasoning:"복잡한 추론",bounded_implementation:"범위가 정해진 구현",mechanical:"기계적 작업",uncertain_requirements:"판단 정보 부족"})[decision.reason] || "이전 정책 / 미기록"], ["최종 실행", chosen.model], ["추론 강도", chosen.effort || decision.effort], ["적용 경로", chosen.source], ["Gate", chosen.gate || decision.gate], ["경로 재사용", decision.lease], ["정책 버전", data.policy_version], ["Jev / 전체 시간", `${ms(data.outcome?.jev_ms)} / ${ms(data.outcome?.total_ms)}`] ];
    $("decision").replaceChildren(...facts.flatMap(([label, value]) => [node("dt", label), node("dd", text(value))]));
    const reused = chosen.source === "lease" || data.outcome?.lease_hit === true;
    $("route-note").textContent = ["manual", "client_model"].includes(chosen.source) ? "클라이언트가 특정 모델을 지정한 요청입니다. 프로젝트 설정·세션·에이전트 지정 등에서 올 수 있으며, 사용자의 직접 선택 여부는 알 수 없습니다. Jev 난이도 평가는 수행하지 않았습니다." : reused ? "이번 호출은 Jev를 다시 호출하지 않고 이전 모델·추론 선택을 재사용했습니다. 아래 확률도 이전 판단의 값입니다." : "시각·아키텍처·코딩·위험도를 독립 평가한 뒤 조합으로 모델을 선택합니다. 점수는 Jev의 추정치입니다. 메시지 원문과 Jev 입력은 제공하지 않습니다.";
    $("response").textContent = json({ confidence: decision.confidence ?? null, probabilities: decision.probabilities ?? null });
    $("policy").textContent = json({ policy_version: data.policy_version, policy_hash: data.policy_hash, decision: data.decision, selected: data.selected });
    $("outcome").textContent = json(data.outcome);
  } catch (exception) { if (id === selected && sequence === detailSequence) { error(exception.message); $("detail-empty").textContent = "상세 기록을 불러오지 못했습니다. 연결을 확인하면 자동으로 다시 시도합니다."; } }
}
let observationSignature = "";
async function refreshObservations() {
  const data = await (await api("observations")).json();
  const signature = JSON.stringify(data.sessions);
  if (signature === observationSignature) return;
  observationSignature = signature;
  const expanded = new Set([...$("observations").querySelectorAll("details[open]")].map(el => el.dataset.id));
  const items = (data.sessions || []).map(session => {
    const detail = node("details"); detail.dataset.id = session.id; detail.open = expanded.has(session.id);
    const state = session.verified ? "검증 통과" : session.files.length ? "검증 근거 없음" : "코드 변경 미관측";
    detail.append(node("summary", `${session.project} / ${session.client} · ${state} · ${session.eventCount} events`));
    detail.append(node("p", `관측 ${when(session.updated)} / 수신 ${when(session.received)} / 세션 ${session.session}`, "hint"));
    detail.append(node("p", `검증 판정: ${session.verdict} (관찰만, 차단하지 않음)`, "hint"));
    detail.append(node("pre", json({files: session.files, events: session.events})));
    return detail;
  });
  $("observations").replaceChildren(...(items.length ? items : [node("p", "아직 수집된 세션이 없습니다. /hooks에서 관찰 훅을 활성화하세요.", "empty")]));
}
async function refresh() {
  if (paused || busy || !authenticated) return;
  busy = true; const generation = listGeneration;
  try {
    const query = new URLSearchParams({ mode: $("mode").value, model: $("model").value, limit: "100" });
    const data = await (await api(`records?${query}`)).json();
    if (generation !== listGeneration || paused) return;
    if (!$("observation-panel").hidden) await refreshObservations();
    records = data.records || [];
    const signature = JSON.stringify(records);
    if (signature !== listSignature) { listSignature = signature; renderList(records); }
    renderDistribution(data.distribution || []);
    const stats = data.stats || {};
    for (const key of ["total", "completed", "failed"]) $(`stat-${key}`).textContent = Number(stats[key] || 0).toLocaleString("ko-KR");
    error(data.storage_error ? `기록 저장 오류: ${text(data.storage_error)}` : "");
    $("connection").textContent = "실시간 연결"; $("connection").className = "live";
    if (!selected && records.length) await selectRecord(records[0].id);
    else if (selected) { const summary = records.find((record) => record.id === selected); if (detailVersion == null || (summary && summary.updated !== detailVersion)) await fetchDetail(selected); }
  } catch (exception) { error(exception.message); if (authenticated) { $("connection").textContent = "연결 재시도 중"; $("connection").className = ""; } }
  finally { busy = false; }
}
$("pause").addEventListener("click", () => { paused = !paused; $("pause").textContent = paused ? "실시간 재개" : "일시 정지"; $("pause").setAttribute("aria-pressed", String(paused)); $("connection").textContent = paused ? "갱신 일시 정지" : "연결 중"; $("connection").className = ""; if (!paused) refresh(); });
$("filters").addEventListener("submit", (event) => { event.preventDefault(); refresh(); });
$("filters").addEventListener("change", () => { listGeneration++; listSignature = ""; refresh(); });
refresh();
setInterval(refresh, 1000);

const serviceOrigin = location.origin.replaceAll("'", "''");
$("client-command").value = `& ([scriptblock]::Create((Invoke-RestMethod '${serviceOrigin}/dashboard/install-client.ps1'))) -ServiceUrl '${serviceOrigin}'`;
$("uninstall-command").value = `& ([scriptblock]::Create((Invoke-RestMethod '${serviceOrigin}/dashboard/uninstall-client.ps1')))`;

for (const [button, input, status] of [["copy-client", "client-command", "client-copy-status"], ["copy-uninstall", "uninstall-command", "uninstall-copy-status"], ["copy-recovery", "recovery-command", "recovery-copy-status"]]) {
  $(button).addEventListener("click", async () => {
    const field = $(input);
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(field.value);
      else { field.focus(); field.select(); if (!document.execCommand("copy")) throw new Error(); }
      $(status).textContent = "복사됨";
    } catch { field.focus(); field.select(); $(status).textContent = "선택된 명령어를 Ctrl+C로 복사하세요."; }
  });
}

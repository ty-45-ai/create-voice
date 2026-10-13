const $ = (id) => document.getElementById(id);

// Build DOM nodes without innerHTML so participant data is never parsed as HTML.
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

const STATUS_LABEL = { done: "完了", queued: "待機中", processing: "生成中", failed: "失敗" };
const LANG_LABEL = { ja: "日本語", en: "英語" };
const CONDITION_LABEL = { ai: "AI自己音声", native: "ネイティブ" };
const PHASE_LABEL = { record: "録音", listen: "聴取", closed: "停止" };

async function api(path, opts = {}) {
  const init = { credentials: "same-origin", ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const res = await fetch(path, init);
  if (res.status === 401) {
    showLogin();
    throw new Error("login");
  }
  if (!res.ok) {
    let msg = `エラー (${res.status})`;
    try {
      const d = (await res.json()).detail;
      msg = typeof d === "string" ? d : JSON.stringify(d);
    } catch (_) {
      /* keep default */
    }
    alert(msg);
    throw new Error(msg);
  }
  return res.json();
}

function showLogin() {
  $("login").classList.remove("hidden");
  $("app").classList.add("hidden");
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  const res = await fetch("/api/admin/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: $("password").value }),
  });
  if (!res.ok) {
    $("login-error").textContent = (await res.json()).detail || "ログインできませんでした";
    return;
  }
  $("password").value = "";
  start();
});

// ---- tabs ----

let activeTab = "progress";
$("tabs").addEventListener("click", (e) => {
  const tab = e.target.dataset.tab;
  if (!tab) return;
  activeTab = tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== `tab-${tab}`));
  refreshTab();
});

function refreshTab() {
  if (activeTab === "progress") loadProgress();
  if (activeTab === "participants") loadParticipants();
  if (activeTab === "sentences") loadSentences();
  if (activeTab === "clips") loadClips();
  if (activeTab === "settings") loadSettings();
}

const adminAudio = $("admin-audio");
function preview(url) {
  adminAudio.classList.remove("hidden");
  adminAudio.src = url;
  adminAudio.play();
}

function downloadCsv(name, rows) {
  const csv = "﻿" + rows.map((r) => r.map((v) => `"${String(v).replaceAll('"', '""')}"`).join(",")).join("\r\n");
  const a = h("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: name });
  document.body.append(a);
  a.click();
  a.remove();
}

function emptyRow(table, cols, text) {
  table.append(h("tr", {}, h("td", { colspan: cols }, text)));
}

// ---- status bar ----

let status = null;
async function loadStatus() {
  status = await api("/api/admin/status");
  const q = status.queue;
  $("stats").replaceChildren(
    h("div", { class: "stat" }, "AI音声 完了", h("b", {}, q.done)),
    h("div", { class: "stat" }, "待機中", h("b", {}, q.queued)),
    h("div", { class: "stat" }, "生成中", h("b", {}, q.processing)),
    h("div", { class: "stat" }, "失敗", h("b", {}, q.failed)),
  );
  let state = `エンジン: ${status.engine}`;
  if (status.engine_loading) state += "（モデル読み込み中…）";
  if (status.engine_error) state += `（エラー: ${status.engine_error}）`;
  $("engine-state").textContent = state;
  $("engine-state").style.color = status.engine_error ? "var(--bad)" : "";
}

// ---- progress & phase ----

function phaseSelect(value, onchange) {
  const sel = h("select", { onchange: (e) => onchange(e.target.value) },
    Object.entries(PHASE_LABEL).map(([v, label]) => h("option", { value: v }, label)));
  sel.value = value;
  return sel;
}

async function loadProgress() {
  await loadStatus();
  $("default-phase").value = status.default_phase;
  const rows = await api("/api/admin/progress");
  const table = $("progress-table");
  table.replaceChildren(
    h("tr", {}, ["クラス", "段階", "人数（AI / ネイティブ）", "提出済み", "AI音声 準備完了", "未提出の生徒", "AI音声 生成待ち"].map((t) => h("th", {}, t))),
    ...rows.map((c) =>
      h("tr", {},
        h("td", {}, c.class_name || "（未設定）"),
        h("td", {}, phaseSelect(c.phase, async (phase) => {
          await api("/api/admin/phase", { method: "PUT", json: { class_name: c.class_name, phase } });
          loadProgress();
        })),
        h("td", { class: "num" }, `${c.total}（${c.ai} / ${c.native}）`),
        h("td", { class: "num" }, `${c.submitted} / ${c.total}`),
        h("td", { class: "num" }, `${c.ai_ready} / ${c.ai}`,
          c.ai && c.ai_ready === c.ai ? h("span", { class: "badge done" }, " 全員分そろっています") : ""),
        h("td", { class: "mono small" }, c.not_submitted.join(", ") || "—"),
        h("td", { class: "mono small" }, c.ai_waiting.join(", ") || "—"),
      ),
    ),
  );
  if (!rows.length) emptyRow(table, 7, "まだ参加者がいません。「参加者」タブで発行してください。");
}

$("default-phase").addEventListener("change", async (e) => {
  await api("/api/admin/phase", { method: "PUT", json: { class_name: null, phase: e.target.value } });
  loadProgress();
});

// ---- participants ----

let lastCreated = [];

function showCreated(rows) {
  lastCreated = rows;
  $("created").classList.remove("hidden");
  $("created-table").replaceChildren(
    h("tr", {}, ["ID", "あいことば", "クラス", "条件"].map((t) => h("th", {}, t))),
    ...rows.map((p) =>
      h("tr", {},
        h("td", { class: "mono" }, p.id), h("td", { class: "mono" }, p.passcode),
        h("td", {}, p.class_name), h("td", {}, CONDITION_LABEL[p.condition])),
    ),
  );
  loadParticipants();
}

$("create-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  showCreated(await api("/api/admin/participants", {
    json: {
      count: Number($("create-count").value),
      prefix: $("create-prefix").value.trim(),
      class_name: $("create-class").value.trim(),
      condition: $("create-condition").value,
    },
  }));
});

$("import-button").addEventListener("click", async () => {
  const csv = $("import-csv").value.trim();
  if (!csv) return;
  showCreated(await api("/api/admin/participants/import", { json: { csv } }));
  $("import-csv").value = "";
});

$("created-csv").addEventListener("click", () => {
  downloadCsv("participants-passcodes.csv", [
    ["id", "passcode", "class", "condition", "url"],
    ...lastCreated.map((p) => [p.id, p.passcode, p.class_name, p.condition, location.origin + "/"]),
  ]);
});

async function loadParticipants() {
  const [rows] = await Promise.all([api("/api/admin/participants"), loadStatus()]);
  const total = status.sentence_count;
  const sel = $("clip-participant");
  const keep = sel.value;
  sel.replaceChildren(h("option", { value: "" }, "すべて"),
    ...rows.filter((p) => p.condition === "ai").map((p) => h("option", { value: p.id }, p.id)));
  sel.value = keep;

  const table = $("participants-table");
  table.replaceChildren(
    h("tr", {}, ["ID", "クラス", "条件", "録音", "提出", "AI音声", "最終利用", "操作"].map((t) => h("th", {}, t))),
    ...rows.map((p) =>
      h("tr", { class: p.active ? "" : "inactive" },
        h("td", { class: "mono" }, p.id, p.active ? "" : h("div", { class: "small" }, "停止中")),
        h("td", {}, p.class_name || "—"),
        h("td", {}, CONDITION_LABEL[p.condition]),
        h("td", { class: "num" }, `${p.recorded} / ${total}`,
          p.recorded ? [" ", h("button", { onclick: () => showRecordings(p) }, "聞く")] : ""),
        h("td", { class: "small" }, p.submitted_at || "未提出"),
        h("td", { class: "num" }, p.condition === "ai" ? `${p.clip_done} / ${p.submitted_at ? total : 0}` : "—",
          p.clip_failed ? h("span", { class: "badge failed" }, ` 失敗 ${p.clip_failed}`) : ""),
        h("td", { class: "small" }, p.last_activity || "—"),
        h("td", {},
          h("div", { class: "actions" },
            h("button", { onclick: () => editParticipant(p) }, "クラス・条件"),
            p.submitted_at ? h("button", { onclick: () => reopen(p) }, "提出を取り消す") : null,
            h("button", { onclick: () => resetPass(p) }, "あいことば再発行"),
            h("button", { onclick: () => toggleActive(p) }, p.active ? "停止" : "再開"),
            h("button", { class: "danger", onclick: () => removeParticipant(p) }, "データ削除"),
          ),
        ),
      ),
    ),
  );
  if (!rows.length) emptyRow(table, 8, "まだ参加者がいません。");
}

async function showRecordings(p) {
  const rows = await api(`/api/admin/participants/${p.id}/recordings`);
  $("recordings-card").classList.remove("hidden");
  $("recordings-title").textContent = `${p.id} の録音`;
  $("recordings-table").replaceChildren(
    h("tr", {}, ["文", "長さ", ""].map((t) => h("th", {}, t))),
    ...rows.map((r) =>
      h("tr", {},
        h("td", {}, r.text),
        h("td", { class: "num" }, r.seconds != null ? `${r.seconds.toFixed(1)}秒` : "未録音"),
        h("td", {}, r.url ? h("button", { onclick: () => preview(r.url) }, "試聴") : ""),
      ),
    ),
  );
  $("recordings-card").scrollIntoView({ behavior: "smooth" });
}

async function editParticipant(p) {
  const cls = prompt(`${p.id} のクラス`, p.class_name);
  if (cls === null) return;
  const cond = prompt(`${p.id} の条件（ai = AI自己音声 / native = ネイティブ）`, p.condition);
  if (cond === null) return;
  if (!["ai", "native"].includes(cond.trim())) return alert("条件は ai か native を入力してください。");
  await api(`/api/admin/participants/${p.id}`, { method: "PATCH", json: { class_name: cls, condition: cond.trim() } });
  loadParticipants();
}

async function reopen(p) {
  if (!confirm(`${p.id} の提出を取り消して、録り直せるようにしますか？（再提出するとAI音声はつくり直されます）`)) return;
  await api(`/api/admin/participants/${p.id}/reopen`, { method: "POST" });
  loadParticipants();
}

async function toggleActive(p) {
  await api(`/api/admin/participants/${p.id}`, { method: "PATCH", json: { active: !p.active } });
  loadParticipants();
}

async function resetPass(p) {
  if (!confirm(`${p.id} のあいことばを再発行しますか？（古いあいことばは使えなくなります）`)) return;
  const r = await api(`/api/admin/participants/${p.id}/passcode`, { method: "POST" });
  prompt(`${r.id} の新しいあいことば（この画面でしか表示されません）`, r.passcode);
}

async function removeParticipant(p) {
  const typed = prompt(
    `${p.id} の録音・AI音声・利用ログをすべて削除します。元に戻せません。\n確認のため ID を入力してください。`,
  );
  if (typed === null) return;
  if (typed.trim().toUpperCase() !== p.id) return alert("ID が一致しないため削除しませんでした。");
  await api(`/api/admin/participants/${p.id}`, { method: "DELETE" });
  $("recordings-card").classList.add("hidden");
  loadParticipants();
}

// ---- sentences ----

$("sentence-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const lines = $("sentence-text").value.split("\n").map((s) => s.trim()).filter(Boolean);
  for (const text of lines) {
    await api("/api/admin/sentences", { json: { text, lang: $("sentence-lang").value } });
  }
  $("sentence-text").value = "";
  loadSentences();
});

let nativeTarget = null;
$("native-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file || nativeTarget == null) return;
  const form = new FormData();
  form.append("file", file);
  await api(`/api/admin/sentences/${nativeTarget}/native`, { method: "POST", body: form });
  loadSentences();
});

function pickNative(sid) {
  nativeTarget = sid;
  $("native-file").click();
}

async function loadSentences() {
  const rows = await api("/api/admin/sentences");
  const table = $("sentences-table");
  table.replaceChildren(
    h("tr", {}, ["順番", "文", "言語", "ネイティブ音声", "公開", "操作"].map((t) => h("th", {}, t))),
    ...rows.map((s) =>
      h("tr", { class: s.active ? "" : "inactive" },
        h("td", { class: "num" }, s.sort_order),
        h("td", { lang: s.lang }, s.text),
        h("td", {}, LANG_LABEL[s.lang] || s.lang),
        h("td", {},
          h("div", { class: "actions" },
            s.has_native
              ? [
                  h("button", { onclick: () => preview(`/api/admin/sentences/${s.id}/native?t=${Date.now()}`) }, "試聴"),
                  h("button", { onclick: () => pickNative(s.id) }, "差し替え"),
                  h("button", { class: "danger", onclick: () => removeNative(s) }, "削除"),
                ]
              : [h("span", { class: "badge failed" }, "未登録"), h("button", { onclick: () => pickNative(s.id) }, "アップロード")],
          ),
        ),
        h("td", {}, s.active ? "公開" : "非公開"),
        h("td", {},
          h("div", { class: "actions" },
            h("button", { onclick: () => editSentence(s) }, "編集"),
            h("button", { onclick: () => moveSentence(s) }, "順番"),
            h("button", { onclick: () => patchSentence(s.id, { active: !s.active }) }, s.active ? "非公開にする" : "公開する"),
            h("button", { class: "danger", onclick: () => deleteSentence(s) }, "削除"),
          ),
        ),
      ),
    ),
  );
  if (!rows.length) emptyRow(table, 6, "まだ教材がありません。");
}

async function patchSentence(id, body) {
  await api(`/api/admin/sentences/${id}`, { method: "PATCH", json: body });
  loadSentences();
}

async function editSentence(s) {
  const text = prompt("文を編集（変更するとその文のAI音声はつくり直されます）", s.text);
  if (text === null || !text.trim() || text.trim() === s.text) return;
  patchSentence(s.id, { text: text.trim(), lang: "auto" });
}

async function moveSentence(s) {
  const n = prompt("順番の数字（小さいほど上に表示）", s.sort_order);
  if (n === null || Number.isNaN(Number(n))) return;
  patchSentence(s.id, { sort_order: Number(n) });
}

async function removeNative(s) {
  if (!confirm("この文のネイティブ音声を削除しますか？")) return;
  await api(`/api/admin/sentences/${s.id}/native`, { method: "DELETE" });
  loadSentences();
}

async function deleteSentence(s) {
  if (!confirm(`「${s.text}」と、その文の生徒の録音・AI音声をすべて削除しますか？`)) return;
  await api(`/api/admin/sentences/${s.id}`, { method: "DELETE" });
  loadSentences();
}

// ---- AI clips ----

$("clip-reload").addEventListener("click", loadClips);
$("clip-participant").addEventListener("change", loadClips);
$("clip-status").addEventListener("change", loadClips);
$("clip-retry-failed").addEventListener("click", async () => {
  const r = await api("/api/admin/clips/regenerate-failed", { method: "POST" });
  alert(`${r.count} 件を再生成の順番待ちに入れました。`);
  loadClips();
});
$("clip-regenerate-all").addEventListener("click", async () => {
  if (!confirm("すべてのAI音声をつくり直します（音声エンジンを切り替えたときなど）。よろしいですか？")) return;
  const r = await api("/api/admin/clips/regenerate-all", { method: "POST" });
  alert(`${r.count} 件をつくり直しの順番待ちに入れました。`);
  loadClips();
});

async function loadClips() {
  await loadStatus();
  if (!$("clip-participant").options.length || $("clip-participant").options.length === 1) {
    loadParticipants().catch(() => {});
  }
  const params = new URLSearchParams();
  if ($("clip-participant").value) params.set("participant_id", $("clip-participant").value);
  if ($("clip-status").value) params.set("status", $("clip-status").value);
  const rows = await api(`/api/admin/clips?${params}`);
  const needApproval = status.require_approval;
  const table = $("clips-table");
  table.replaceChildren(
    h("tr", {}, ["参加者", "クラス", "文", "状態", "長さ", "更新", needApproval ? "承認" : null, "操作"].filter(Boolean).map((t) => h("th", {}, t))),
    ...rows.map((c) =>
      h("tr", {},
        h("td", { class: "mono" }, c.participant_id),
        h("td", {}, c.class_name),
        h("td", { lang: c.lang }, c.text, c.error ? h("div", { class: "err-text" }, c.error.split("\n")[0]) : ""),
        h("td", {}, h("span", { class: `badge ${c.status}` }, STATUS_LABEL[c.status] || c.status)),
        h("td", { class: "num" }, c.seconds ? `${c.seconds.toFixed(1)}秒` : ""),
        h("td", { class: "small" }, c.updated_at),
        needApproval ? h("td", {}, c.status === "done" ? (c.approved ? "承認済" : "未承認") : "") : null,
        h("td", {},
          h("div", { class: "actions" },
            c.status === "done" ? h("button", { onclick: () => preview(`/api/admin/clips/${c.id}/audio?t=${Date.now()}`) }, "試聴") : null,
            needApproval && c.status === "done"
              ? h("button", { onclick: () => approve(c, !c.approved) }, c.approved ? "承認取消" : "承認")
              : null,
            h("button", { onclick: () => regenerate(c) }, "再生成"),
          ),
        ),
      ),
    ),
  );
  if (!rows.length) emptyRow(table, 8, "該当するAI音声はありません（AI自己音声条件の生徒が提出すると生成されます）。");
}

async function approve(c, approved) {
  await api(`/api/admin/clips/${c.id}/approve`, { json: { approved } });
  loadClips();
}

async function regenerate(c) {
  await api(`/api/admin/clips/${c.id}/regenerate`, { method: "POST" });
  loadClips();
}

// ---- settings ----

async function loadSettings() {
  await loadStatus();
  $("allow-controls").checked = status.allow_controls;
  $("require-approval").checked = status.require_approval;
  $("survey-url").value = status.survey_url;
}

$("save-settings").addEventListener("click", async () => {
  await api("/api/admin/settings", {
    method: "PUT",
    json: {
      allow_controls: $("allow-controls").checked,
      require_approval: $("require-approval").checked,
      survey_url: $("survey-url").value,
    },
  });
  await loadStatus();
  $("settings-saved").textContent = "保存しました";
  setTimeout(() => ($("settings-saved").textContent = ""), 2500);
});

// ---- start ----

let statusTimer = null;
async function start() {
  try {
    await loadStatus();
  } catch (_) {
    return;
  }
  $("login").classList.add("hidden");
  $("app").classList.remove("hidden");
  refreshTab();
  if (!statusTimer) {
    statusTimer = setInterval(() => {
      if (activeTab === "progress") loadProgress().catch(() => {});
      else loadStatus().catch(() => {});
    }, 10000);
  }
}

start();

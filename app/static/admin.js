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

const STATUS_LABEL = { done: "完了", queued: "待機中", processing: "生成中", failed: "エラー" };
const LANG_LABEL = { ja: "日本語", en: "英語" };

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
  // Same order and colours as the students' states: done = ready, processing = generating.
  $("stats").replaceChildren(
    ...[["done", "ready"], ["processing", "generating"], ["queued", "not_started"], ["failed", "failed"]].map(
      ([key, tone]) => h("div", { class: `stat ${tone}` }, STATUS_LABEL[key], h("b", {}, `${q[key]}`, h("small", {}, " 件")))),
  );
  let state = `エンジン: ${status.engine}`;
  if (status.engine_loading) state += "（モデル読み込み中…）";
  if (status.engine_error) state += `（エラー: ${status.engine_error}）`;
  $("engine-state").textContent = state;
  $("engine-state").style.color = status.engine_error ? "var(--bad)" : "";
  const failures = status.standard_failures || [];
  $("standard-alert").classList.toggle("hidden", !failures.length);
  $("standard-alert").replaceChildren(
    h("h2", {}, "AI音声（標準）を生成できなかった教材があります"),
    h("p", { class: "small" }, "生徒の画面では「生成中」と表示されたままになります。原因を解消してから「再生成」を押してください。"
      + "声が教材の言語に対応していない場合は、「設定・出力」で声を変更してください。"),
    ...failures.map((f) => h("div", { class: "fail-row" },
      h("div", { class: "pre" }, f.text),
      h("div", { class: "err-text" }, f.error),
      h("button", { onclick: async () => {
        await api(`/api/admin/sentences/${f.sentence_id}/standard/regenerate`, { method: "POST" });
        loadProgress();
      } }, "再生成"))),
  );
}

// ---- progress ----

const STATE_LABEL = {
  not_started: "未録音", recording: "録音中", generating: "音声生成中", ready: "聴取可能", failed: "生成エラー",
};

function stateChips(counts) {
  return Object.entries(STATE_LABEL).map(([k, label]) => h("span", { class: `chip ${k}` }, `${label} ${counts[k] || 0}人`));
}

async function loadProgress() {
  await loadStatus();
  const classes = await api("/api/admin/progress");
  const total = status.sentence_count;
  const sum = {};
  classes.forEach((c) => Object.entries(c.counts).forEach(([k, n]) => (sum[k] = (sum[k] || 0) + n)));
  $("progress-total").replaceChildren(...stateChips(sum));
  const box = $("progress-classes");
  box.replaceChildren(
    ...classes.map((c) =>
      h("section", { class: "class-progress" },
        h("h3", {}, `${c.class_name || "クラス未設定"}（${c.total}人）`),
        h("div", { class: "class-counts" }, stateChips(c.counts)),
        h("div", { class: "students" },
          c.students.map((st) =>
            h("span", {
              class: `chip student ${st.state}`,
              title: `${st.id}：${STATE_LABEL[st.state]}（録音 ${st.recorded} / ${total}`
                + (st.state === "generating" || st.state === "failed" ? `、AI音声 ${st.clip_done} / ${total}` : "") + "）",
            },
            st.id,
            st.state === "recording" ? h("small", {}, ` ${st.recorded}/${total}`) : "",
            st.state === "generating" ? h("small", {}, ` ${st.clip_done}/${total}`) : ""))),
      )),
  );
  if (!classes.length) box.replaceChildren(h("p", { class: "small" }, "まだ参加者がいません。「参加者」タブで発行してください。"));
}

// ---- participants ----

let lastCreated = [];

function showCreated(rows) {
  lastCreated = rows;
  $("created").classList.remove("hidden");
  $("created-table").replaceChildren(
    h("tr", {}, ["ID", "パスワード", "クラス"].map((t) => h("th", {}, t))),
    ...rows.map((p) =>
      h("tr", {},
        h("td", { class: "mono" }, p.id), h("td", { class: "mono" }, p.passcode),
        h("td", {}, p.class_name)),
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
  downloadCsv("id-password-new.csv", passwordRows(lastCreated));
});

// ID, password, class and the address to open, for handing out to students.
function passwordRows(list) {
  return [["ID", "パスワード", "クラス", "アドレス"],
    ...list.map((p) => [p.id, p.passcode || "", p.class_name, location.origin + "/"])];
}

let participantRows = [];
$("show-passwords").addEventListener("change", () => loadParticipants());
$("passwords-csv").addEventListener("click", () => {
  downloadCsv("id-password.csv", passwordRows(participantRows));
});

$("fill-passwords").addEventListener("click", async () => {
  if (!confirm("パスワードが「不明」の参加者全員に、新しいパスワードを発行します。\n"
    + "対象の参加者は、これまでのパスワードではログインできなくなります。実行しますか？")) return;
  showCreated(await api("/api/admin/participants/fill-missing-passwords", { method: "POST" }));
});

function passwordCell(p) {
  if (!p.passcode) return h("span", { class: "small" }, "不明");
  return $("show-passwords").checked ? p.passcode : "••••••";
}

async function loadParticipants() {
  const [rows] = await Promise.all([api("/api/admin/participants"), loadStatus()]);
  participantRows = rows;
  const missing = rows.filter((p) => !p.passcode).length;
  $("missing-passwords").classList.toggle("hidden", !missing);
  $("missing-passwords-text").textContent =
    `パスワードが「不明」の参加者が ${missing} 人います（パスワード管理機能の追加前に発行した参加者）。 `;
  const total = status.sentence_count;
  const sel = $("clip-participant");
  const keep = sel.value;
  sel.replaceChildren(h("option", { value: "" }, "すべて"),
    ...rows.map((p) => h("option", { value: p.id }, p.id)));
  sel.value = keep;

  const table = $("participants-table");
  table.replaceChildren(
    h("tr", {}, ["ID", "パスワード", "クラス", "録音", "提出", "AI音声（クローン）", "最終利用", "操作"].map((t) => h("th", {}, t))),
    ...rows.map((p) =>
      h("tr", { class: p.active ? "" : "inactive" },
        h("td", { class: "mono" }, p.id, p.active ? "" : h("div", { class: "small" }, "停止中")),
        h("td", { class: "mono" }, passwordCell(p)),
        h("td", {}, p.class_name || "—"),
        h("td", { class: "num" }, `${p.recorded} / ${total}`,
          p.recorded ? [" ", h("button", { onclick: () => showRecordings(p) }, "試聴")] : ""),
        h("td", { class: "small" }, p.submitted_at || "未提出"),
        h("td", { class: "num" }, `${p.clip_done} / ${p.submitted_at ? total : 0}`,
          p.clip_failed ? h("span", { class: "badge failed" }, ` エラー ${p.clip_failed}`) : ""),
        h("td", { class: "small" }, p.last_activity || "—"),
        h("td", {},
          h("div", { class: "actions" },
            h("button", { onclick: () => editParticipant(p) }, "クラス変更"),
            p.submitted_at ? h("button", { onclick: () => reopen(p) }, "提出を取り消す") : null,
            h("button", { onclick: () => resetPass(p) }, "パスワード変更"),
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
  await api(`/api/admin/participants/${p.id}`, { method: "PATCH", json: { class_name: cls } });
  loadParticipants();
}

async function reopen(p) {
  if (!confirm(`${p.id} の提出を取り消し、録り直しができる状態に戻しますか？（再提出すると、AI音声は再生成されます）`)) return;
  await api(`/api/admin/participants/${p.id}/reopen`, { method: "POST" });
  loadParticipants();
}

async function toggleActive(p) {
  await api(`/api/admin/participants/${p.id}`, { method: "PATCH", json: { active: !p.active } });
  loadParticipants();
}

async function resetPass(p) {
  const typed = prompt(
    `${p.id} の新しいパスワード（英数字4〜32文字。空欄のままOKを押すと自動で生成します）\n変更前のパスワードは使用できなくなります。`, "");
  if (typed === null) return;
  const r = await api(`/api/admin/participants/${p.id}/passcode`, { json: { password: typed.trim() } });
  alert(`${r.id} の新しいパスワード：${r.passcode}`);
  loadParticipants();
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

// A blank line separates practice items; single line breaks stay inside one item.
const splitItems = (value) =>
  value.replace(/\r/g, "").split(/\n\s*\n/).map((s) => s.replace(/[ \t]+$/gm, "").trim()).filter(Boolean);

let editingSentence = null;

function stopEditing() {
  editingSentence = null;
  $("sentence-text").value = "";
  $("sentence-form-title").textContent = "教材を追加";
  $("sentence-submit").textContent = "追加";
  $("sentence-cancel").classList.add("hidden");
}

$("sentence-cancel").addEventListener("click", stopEditing);

$("sentence-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const items = splitItems($("sentence-text").value);
  if (!items.length) return;
  if (editingSentence) {
    if (items.length > 1) return alert("編集中は空行を入力できません（1つの練習として保存されます）。");
    if (items[0] !== editingSentence.text) {
      await api(`/api/admin/sentences/${editingSentence.id}`, {
        method: "PATCH", json: { text: items[0], lang: $("sentence-lang").value },
      });
    }
    stopEditing();
  } else {
    for (const text of items) {
      await api("/api/admin/sentences", { json: { text, lang: $("sentence-lang").value } });
    }
    $("sentence-text").value = "";
  }
  loadSentences();
});

async function loadSentences() {
  const rows = await api("/api/admin/sentences");
  const table = $("sentences-table");
  table.replaceChildren(
    h("tr", {}, ["順番", "文", "言語", "AI音声（標準）", "公開", "操作"].map((t) => h("th", {}, t))),
    ...rows.map((s) =>
      h("tr", { class: s.active ? "" : "inactive" },
        h("td", { class: "num" }, s.sort_order),
        h("td", { lang: s.lang, class: "pre" }, s.text),
        h("td", {}, LANG_LABEL[s.lang] || s.lang),
        h("td", {},
          h("div", { class: "actions" },
            h("span", { class: `badge ${s.standard_status || ""}` }, STATUS_LABEL[s.standard_status] || "—"),
            s.standard_status === "done"
              ? h("button", { onclick: () => preview(`/api/admin/sentences/${s.id}/standard?t=${Date.now()}`) }, "試聴")
              : null,
            h("button", { onclick: () => regenerateStandard(s) }, "再生成"),
          ),
          s.standard_error ? h("div", { class: "err-text" }, s.standard_error) : "",
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

async function regenerateStandard(s) {
  await api(`/api/admin/sentences/${s.id}/standard/regenerate`, { method: "POST" });
  loadSentences();
}

async function patchSentence(id, body) {
  await api(`/api/admin/sentences/${id}`, { method: "PATCH", json: body });
  loadSentences();
}

function editSentence(s) {
  editingSentence = s;
  $("sentence-text").value = s.text;
  $("sentence-lang").value = s.lang;
  $("sentence-form-title").textContent = `教材を編集（${s.sort_order}番）— 変更すると、この練習のAI音声は再生成されます`;
  $("sentence-submit").textContent = "更新";
  $("sentence-cancel").classList.remove("hidden");
  $("sentence-form").scrollIntoView({ behavior: "smooth" });
  $("sentence-text").focus();
}

async function moveSentence(s) {
  const n = prompt("順番の数字（小さいほど上に表示）", s.sort_order);
  if (n === null || Number.isNaN(Number(n))) return;
  patchSentence(s.id, { sort_order: Number(n) });
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
  alert(`${r.count} 件を再生成の待機列に追加しました。`);
  loadClips();
});
$("clip-regenerate-all").addEventListener("click", async () => {
  if (!confirm("すべてのAI音声を再生成します（音声エンジンを切り替えた場合など）。実行しますか？")) return;
  const r = await api("/api/admin/clips/regenerate-all", { method: "POST" });
  alert(`${r.count} 件を再生成の待機列に追加しました。`);
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
  if (!rows.length) emptyRow(table, 8, "該当するAI音声はありません（生徒の提出後に生成されます）。");
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
  for (const lang of ["en", "ja"]) {
    $(`standard-voice-${lang}`).value = status.standard_voice[lang] || "";
    $(`standard-voice-${lang}`).placeholder = status.standard_voice_default[lang]
      ? `自動（${status.standard_voice_default[lang]}）` : "自動";
  }
  $("standard-voice-list").replaceChildren(...status.standard_voices.map((v) => h("option", { value: v })));
}

$("save-settings").addEventListener("click", async () => {
  await api("/api/admin/settings", {
    method: "PUT",
    json: {
      allow_controls: $("allow-controls").checked,
      require_approval: $("require-approval").checked,
      survey_url: $("survey-url").value,
      standard_voices: { en: $("standard-voice-en").value.trim(), ja: $("standard-voice-ja").value.trim() },
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

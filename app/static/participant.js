import { Recorder, decodeFile, micSupported, prepareVoice } from "./audio.js";

const $ = (id) => document.getElementById(id);
const VIEWS = ["view-login", "view-record", "view-submitted", "view-listen", "view-closed"];
let me = null;
let pollTimer = null;
let currentPlayer = null;
let activeRecorder = null; // only one sentence records at a time

function show(view) {
  VIEWS.forEach((v) => $(v).classList.toggle("hidden", v !== view));
  $("logout").classList.toggle("hidden", view === "view-login");
}

async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  if (res.status === 401) {
    me = null;
    show("view-login");
    throw new Error("login");
  }
  if (!res.ok) {
    let msg = "うまくいきませんでした。もう一度試してください。";
    try {
      const d = (await res.json()).detail;
      if (typeof d === "string") msg = d;
    } catch (_) {
      /* keep default */
    }
    throw new Error(msg);
  }
  return res.json();
}

function logEvent(type, sentence_id = null, detail = "") {
  fetch("/api/events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type, sentence_id, detail }),
  }).catch(() => {});
}

// ---- login ----

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  try {
    await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: $("login-id").value, passcode: $("login-pass").value }),
    });
    $("login-pass").value = "";
    await load();
  } catch (err) {
    $("login-error").textContent = err.message === "login" ? "ID または あいことば が違います" : err.message;
  }
});

$("logout").addEventListener("click", async () => {
  stopAudio();
  if (activeRecorder) activeRecorder.cancel();
  activeRecorder = null;
  await fetch("/api/logout", { method: "POST" });
  me = null;
  $("who").textContent = "";
  clearTimeout(pollTimer);
  show("view-login");
});

// ---- routing by the class's current stage ----

async function load() {
  try {
    me = await api("/api/me");
  } catch (err) {
    if (err.message !== "login") console.error(err);
    return;
  }
  $("who").textContent = me.id;
  render();
}

function render() {
  clearTimeout(pollTimer);
  if (me.phase === "closed") return show("view-closed");
  if (me.phase === "listen") return renderListen();
  if (me.submitted) return show("view-submitted");
  return renderRecord();
}

// ---- home: record each sentence ----

function renderRecord() {
  show("view-record");
  const supported = micSupported();
  $("mic-warning").classList.toggle("hidden", supported);
  $("mic-warning").textContent =
    "この画面ではマイクが使えません。各文の「ファイルを選ぶ」から録音ファイルを送ってください。";
  const list = $("record-items");
  list.innerHTML = "";
  me.items.forEach((item, i) => list.appendChild(recordCard(item, i, supported)));
  updateRecordProgress();
}

function updateRecordProgress() {
  const done = me.items.filter((i) => i.recorded).length;
  const total = me.items.length;
  $("record-progress").textContent = total
    ? `${total} 文中 ${done} 文を保存しました。`
    : "まだ録音する文がありません。先生の準備をお待ちください。";
  $("record-progress").className = `notice${total && done === total ? " good" : ""}`;
  $("submit").disabled = !total || done < total;
}

function recordCard(item, index, supported) {
  const node = $("record-tpl").content.firstElementChild.cloneNode(true);
  const q = (sel) => node.querySelector(sel);
  q(".seq").textContent = `${index + 1}`;
  q(".text").textContent = item.text;
  q(".text").lang = item.lang;
  const status = q(".status");
  const startBtn = q(".rec-start");
  const stopBtn = q(".rec-stop");
  const meter = q(".meter");
  const review = q(".review");
  const saved = q(".saved");
  const error = q(".error");
  let pending = null;
  let timer = null;

  const showSaved = () => {
    status.textContent = item.recorded ? "保存済み ✓" : "未録音";
    status.className = `status ${item.recorded ? "ok" : "wait"}`;
    saved.classList.toggle("hidden", !item.recorded);
    if (item.recorded) saved.querySelector("audio").src = item.own_url;
    startBtn.textContent = item.recorded ? "● 録り直す" : "● 録音";
  };
  showSaved();
  if (!supported) startBtn.classList.add("hidden");

  const reviewVoice = async (raw) => {
    const voice = await prepareVoice(raw, 120);
    pending = voice.blob;
    review.querySelector("audio").src = URL.createObjectURL(voice.blob);
    const check = review.querySelector(".check");
    const save = review.querySelector(".save");
    save.disabled = false;
    if (voice.duration < 0.5) {
      check.textContent = "声が聞き取れませんでした。もう一度録音してください。";
      check.className = "check notice bad";
      save.disabled = true;
    } else if (voice.peak < 0.05 || voice.voiced < 0.2) {
      check.textContent = "声が小さいようです。マイクに近づいて、はっきり読むとよくなります。";
      check.className = "check notice warn";
    } else {
      check.textContent = "よく録音できました。";
      check.className = "check notice good";
    }
    review.classList.remove("hidden");
    saved.classList.add("hidden");
  };

  startBtn.addEventListener("click", async () => {
    if (activeRecorder) return;
    error.textContent = "";
    stopAudio();
    const rec = new Recorder((peak) => {
      meter.firstElementChild.style.width = `${Math.min(100, peak * 140)}%`;
    });
    try {
      await rec.start();
    } catch (err) {
      console.error(err);
      error.textContent = "マイクが使えませんでした。ブラウザのマイクの許可を確認してください。";
      return;
    }
    activeRecorder = rec;
    startBtn.classList.add("hidden");
    stopBtn.classList.remove("hidden");
    meter.classList.remove("hidden");
    review.classList.add("hidden");
    timer = setInterval(() => {
      const t = rec.elapsed();
      q(".rec-time").textContent = `${t.toFixed(1)} 秒`;
      if (t > 110) stopBtn.click();
    }, 100);
  });

  stopBtn.addEventListener("click", async () => {
    clearInterval(timer);
    const rec = activeRecorder;
    activeRecorder = null;
    stopBtn.classList.add("hidden");
    startBtn.classList.remove("hidden");
    meter.classList.add("hidden");
    q(".rec-time").textContent = "";
    if (rec) await reviewVoice(await rec.stop());
  });

  q(".file-pick input").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    error.textContent = "";
    try {
      await reviewVoice(await decodeFile(file));
    } catch (err) {
      console.error(err);
      error.textContent = "このファイルは読み込めませんでした。WAV か MP3 を試してください。";
    }
  });

  review.querySelector(".redo").addEventListener("click", () => {
    pending = null;
    review.classList.add("hidden");
    showSaved();
  });

  review.querySelector(".save").addEventListener("click", async (e) => {
    if (!pending) return;
    e.target.disabled = true;
    error.textContent = "";
    const form = new FormData();
    form.append("file", pending, `sentence-${item.sentence_id}.wav`);
    try {
      await api(`/api/recordings/${item.sentence_id}`, { method: "POST", body: form });
      const fresh = await api("/api/me");
      me = fresh;
      if (fresh.phase !== "record" || fresh.submitted) return render();
      Object.assign(item, fresh.items.find((x) => x.sentence_id === item.sentence_id) || {});
      // Keep the page's other cards in step with the fresh list.
      me.items = me.items.map((x) => (x.sentence_id === item.sentence_id ? item : x));
      pending = null;
      review.classList.add("hidden");
      showSaved();
      updateRecordProgress();
    } catch (err) {
      e.target.disabled = false;
      if (err.message !== "login") error.textContent = err.message;
    }
  });

  return node;
}

$("submit").addEventListener("click", async () => {
  if (!confirm("提出しますか？ 提出すると録り直しはできません。")) return;
  $("submit").disabled = true;
  $("submit-error").textContent = "";
  try {
    await api("/api/submit", { method: "POST" });
    await load();
  } catch (err) {
    $("submit").disabled = false;
    if (err.message !== "login") $("submit-error").textContent = err.message;
  }
});

// ---- school: own recording, then the model ----

function stopAudio() {
  if (currentPlayer) {
    const p = currentPlayer;
    currentPlayer = null;
    p.stop();
  }
}

// Play `url` repeatedly with the card's settings, read live so a change applies while
// playing. settings.repeat = 0 means "until stopped".
// onState(state, round, finished): state "playing" | "gap" | "done"; finished = played to the end.
function playRepeated(url, owner, settings, onState) {
  stopAudio();
  const audio = new Audio(url);
  audio.preservesPitch = true;
  audio.webkitPreservesPitch = true;
  let round = 0;
  let gapTimer = null;
  const player = {
    owner,
    setRate(rate) {
      audio.playbackRate = rate;
    },
    stop() {
      clearTimeout(gapTimer);
      audio.pause();
      onState("done", round, false);
    },
  };
  const start = () => {
    round += 1;
    audio.currentTime = 0;
    audio.playbackRate = settings.rate;
    onState("playing", round);
    audio.play().catch(() => {
      if (currentPlayer === player) currentPlayer = null;
      onState("done", round, false);
    });
  };
  audio.addEventListener("ended", () => {
    if (currentPlayer !== player) return;
    if (settings.repeat === 0 || round < settings.repeat) {
      onState("gap", round, true);
      gapTimer = setTimeout(start, 800); // a short pause to repeat after the voice
    } else {
      currentPlayer = null;
      onState("done", round, true);
    }
  });
  currentPlayer = player;
  start();
  return player;
}

function roundLabel(round, settings) {
  return settings.repeat === 0 ? `${round}回目` : `${round} / ${settings.repeat}`;
}

function setupPlayerSettings(node, settings) {
  const slider = node.querySelector(".rate");
  const value = node.querySelector(".rate-value");
  const apply = (rate) => {
    settings.rate = Math.round(rate * 10) / 10;
    slider.value = String(settings.rate);
    value.textContent = `${settings.rate.toFixed(1)} 倍`;
    if (currentPlayer && currentPlayer.owner === node) currentPlayer.setRate(settings.rate);
  };
  slider.addEventListener("input", () => apply(Number(slider.value)));
  node.querySelector(".rate-reset").addEventListener("click", () => apply(1));
  node.querySelectorAll(".repeat button").forEach((b) => {
    b.addEventListener("click", () => {
      settings.repeat = Number(b.dataset.repeat);
      node.querySelectorAll(".repeat button").forEach((x) => x.classList.toggle("on", x === b));
    });
  });
}

// Sentences whose own recording has been heard to the end, kept across re-renders.
const heardOwn = new Set();

const CIRCLED = ["①", "②", "③", "④"];

function renderListen() {
  show("view-listen");
  const list = $("listen-items");
  list.innerHTML = "";
  me.items.forEach((item, i) => list.appendChild(listenCard(item, i)));
  const waiting = me.items.some((i) => (i.audios || []).some((a) => a.kind !== "own" && !a.url));
  const progress = $("listen-progress");
  progress.classList.toggle("hidden", !waiting);
  progress.textContent = "AIの音声を準備しています。少し待つと聞けるようになります。";
  const kinds = (me.items[0] && me.items[0].audios) || [];
  const hasOwn = kinds.some((a) => a.kind === "own");
  const others = kinds.filter((a) => a.kind !== "own");
  const guide = $("listen-guide");
  if (hasOwn && others.length) {
    guide.innerHTML = "";
    guide.append("それぞれの文で、", Object.assign(document.createElement("b"), { textContent: "① 自分の音読" }),
      " を聞いてから、", others.map((a, i) => `${CIRCLED[i + 1]} ${a.label}`).join("、"), " を聞いてください。");
  } else {
    guide.textContent = "それぞれの文の音声を聞いてください。";
  }
  $("survey").classList.toggle("hidden", !me.survey_url);
  $("survey-link").href = me.survey_url || "#";
  if (waiting) pollTimer = setTimeout(refresh, 10000);
}

function listenCard(item, index) {
  const node = $("listen-tpl").content.firstElementChild.cloneNode(true);
  const q = (sel) => node.querySelector(sel);
  q(".seq").textContent = `${index + 1}`;
  q(".text").textContent = item.text;
  q(".text").lang = item.lang;
  const settings = { rate: 1, repeat: 1 };
  if (me.allow_controls) setupPlayerSettings(node, settings);
  else q(".player-settings").remove();

  const sid = item.sentence_id;
  const hint = q(".hint");
  const box = q(".audio-buttons");
  const audios = item.audios || [];
  const own = audios.find((a) => a.kind === "own");
  // When the own recording is part of the set, the others unlock after hearing it once.
  const locked = () => own && own.url && !heardOwn.has(sid);
  const buttons = [];

  const refreshLocks = () => {
    let waitingFor = "";
    for (const { btn, audio } of buttons) {
      if (audio.kind === "own") continue;
      btn.disabled = !audio.url || locked();
      if (!audio.url && !waitingFor) waitingFor = `${audio.label}は準備中です。`;
    }
    hint.textContent = locked() ? "先に ① 自分の音読 を最後まで聞いてください。" : waitingFor;
  };

  audios.forEach((audio, i) => {
    const label = `${CIRCLED[i] || "・"} ${audio.label}を聞く`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `big ${audio.kind === "own" ? "" : "primary"} play-${audio.kind}`;
    btn.textContent = audio.kind === "own" && !audio.url ? "① 録音がありません" : label;
    btn.disabled = !audio.url;
    box.appendChild(btn);
    buttons.push({ btn, audio });
    if (!audio.url) return;
    btn.addEventListener("click", () => {
      const key = `${audio.kind}-${sid}`;
      if (currentPlayer && currentPlayer.key === key) return stopAudio();
      const player = playRepeated(audio.url, node, settings, (state, round, finished) => {
        if (finished && audio.kind === "own" && !heardOwn.has(sid)) {
          heardOwn.add(sid);
          refreshLocks();
        }
        btn.textContent = state === "done" ? label : `■ 停止（${roundLabel(round, settings)}）`;
      });
      player.key = key;
      logEvent(`play_${audio.kind}`, sid, `rate=${settings.rate};repeat=${settings.repeat}`);
    });
  });
  refreshLocks();
  return node;
}

$("survey-link").addEventListener("click", () => logEvent("survey_open"));

async function refresh() {
  try {
    const next = await api("/api/me");
    const key = (m) =>
      JSON.stringify([m.phase, m.submitted, m.items.map((i) => [i.recorded, (i.audios || []).map((a) => a.url)])]);
    const changed = key(next) !== key(me);
    me = next;
    if (changed && !currentPlayer) render();
    else if (me.phase === "listen") pollTimer = setTimeout(refresh, 10000);
  } catch (_) {
    pollTimer = setTimeout(refresh, 15000);
  }
}

load().then(() => {
  if (!me) show("view-login");
});

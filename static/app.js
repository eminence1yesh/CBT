/* ==========================================================================
   Exit Exam Trainer - front end
   One question in the DOM at a time, so a 100-question set costs the same
   as a single question. State is written to SQLite (and to localStorage as
   a belt-and-braces copy) so a power cut costs at most a few seconds.
   ========================================================================== */

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const SET_SIZE = parseInt(document.body.dataset.setSize, 10) || 100;
  const EXAM_SECONDS = parseInt(document.body.dataset.examSeconds, 10) || 60;
  const LS_KEY = "exitexam.session";

  const S = {
    mode: "practice",
    topic: "", filter: "all", search: "",
    shuffleQ: false, shuffleO: true,
    seed: 1,
    setIndex: 0, sets: 1, matching: 0,
    questions: [],
    pos: 0,
    answers: {},          // id -> {picked, right, revealed, skipped, ms}
    shownAt: 0,
    examEnds: 0,
    timerId: null,
  };

  /* ------------------------------------------------------------------ net */

  async function api(path, opts) {
    const res = await fetch(path, opts);
    let data;
    try { data = await res.json(); }
    catch (e) { throw new Error("The server sent something unreadable."); }
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || "Request failed (" + res.status + ")");
    }
    return data;
  }

  let toastTimer = null;
  function toast(msg, ms) {
    const t = $("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, ms || 2600);
  }

  /* --------------------------------------------------------------- screens */

  const SCREENS = ["setup", "quiz", "result", "stats", "import"];
  function show(name) {
    SCREENS.forEach((s) => { $("screen-" + s).hidden = (s !== name); });
    window.scrollTo(0, 0);
    if (name !== "quiz") stopTimer();
  }

  /* ------------------------------------------------------------ autosave */

  let saveTimer = null;
  function snapshot() {
    return {
      mode: S.mode, topic: S.topic, filter: S.filter, search: S.search,
      shuffleQ: S.shuffleQ, shuffleO: S.shuffleO, seed: S.seed,
      setIndex: S.setIndex, pos: S.pos, answers: S.answers,
      examEnds: S.examEnds, savedAt: Date.now(),
    };
  }
  function saveState() {
    const snap = snapshot();
    try { localStorage.setItem(LS_KEY, JSON.stringify(snap)); } catch (e) {}
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      api("/api/state", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(snap),
      }).catch(() => {});          // a failed autosave must never interrupt
    }, 700);
  }
  window.addEventListener("beforeunload", () => {
    try { localStorage.setItem(LS_KEY, JSON.stringify(snapshot())); } catch (e) {}
  });

  /* ---------------------------------------------------------------- setup */

  async function loadMeta() {
    const m = await api("/api/meta");
    $("bank-count").textContent = m.total.toLocaleString();

    const sel = $("f-topic");
    sel.innerHTML = '<option value="">All subjects</option>';
    m.topics.forEach((t) => {
      const o = document.createElement("option");
      o.value = t.topic;
      o.textContent = t.topic + "  (" + t.count + ")";
      sel.appendChild(o);
    });

    let resume = m.resume;
    if (!resume) {
      try { resume = JSON.parse(localStorage.getItem(LS_KEY) || "null"); }
      catch (e) { resume = null; }
    }
    if (resume && resume.answers && Object.keys(resume.answers).length) {
      $("btn-resume").hidden = false;
      $("btn-resume").dataset.payload = JSON.stringify(resume);
    }
    refreshCount();
  }

  let countTimer = null;
  function refreshCount() {
    clearTimeout(countTimer);
    countTimer = setTimeout(async () => {
      readFilters();
      const qs = new URLSearchParams({
        topic: S.topic, filter: S.filter, search: S.search,
      });
      try {
        const d = await api("/api/count?" + qs);
        S.matching = d.matching;
        S.sets = d.sets;
        $("match-count").textContent =
          d.matching.toLocaleString() + " questions · " +
          d.sets + (d.sets === 1 ? " set" : " sets");
        const sel = $("f-set");
        const keep = sel.value;
        sel.innerHTML = "";
        for (let i = 0; i < Math.max(d.sets, 1); i++) {
          const o = document.createElement("option");
          o.value = i;
          const from = i * SET_SIZE + 1;
          const to = Math.min((i + 1) * SET_SIZE, d.matching);
          o.textContent = "Set " + (i + 1) + "  (" + from + "–" + to + ")";
          sel.appendChild(o);
        }
        if (keep && keep < sel.options.length) sel.value = keep;
        $("btn-start").disabled = d.matching === 0;
      } catch (e) { /* leave the previous count on screen */ }
    }, 220);
  }

  function readFilters() {
    S.topic = $("f-topic").value;
    S.filter = S.mode === "revision" ? "wrong" : $("f-filter").value;
    S.search = $("f-search").value.trim();
    S.shuffleQ = $("f-shuffle-q").checked;
    S.shuffleO = $("f-shuffle-o").checked;
  }

  /* ----------------------------------------------------------------- quiz */

  async function startQuiz(setIndex, restoreAnswers, restorePos) {
    readFilters();
    S.setIndex = setIndex || 0;
    if (!restoreAnswers) { S.answers = {}; S.seed = Date.now() % 100000; }

    const qs = new URLSearchParams({
      set: S.setIndex, topic: S.topic, filter: S.filter, search: S.search,
      shuffle_questions: S.shuffleQ ? "1" : "0",
      shuffle_options: S.shuffleO ? "1" : "0",
      seed: S.seed,
    });

    let d;
    try { d = await api("/api/set?" + qs); }
    catch (e) { toast(e.message); return; }

    if (!d.questions.length) {
      toast("Nothing matches those filters. Try widening them.");
      return;
    }

    S.questions = d.questions;
    S.sets = d.sets;
    S.matching = d.matching;
    S.setIndex = d.set;
    S.pos = Math.min(restorePos || 0, S.questions.length - 1);

    if (S.mode === "exam") {
      S.examEnds = (restoreAnswers && S.examEnds > Date.now())
        ? S.examEnds
        : Date.now() + S.questions.length * EXAM_SECONDS * 1000;
      startTimer();
    } else {
      $("timer").hidden = true;
      stopTimer();
    }

    show("quiz");
    render();
    saveState();
  }

  function current() { return S.questions[S.pos]; }

  function render() {
    const q = current();
    if (!q) return;
    const rec = S.answers[q.id];

    $("q-topic").textContent = q.topic || "General";
    $("q-text").textContent = q.question;
    $("readout").textContent =
      String(S.pos + 1).padStart(3, "0") + " / " + String(S.questions.length).padStart(3, "0");
    $("readout-label").textContent =
      "set " + (S.setIndex + 1) + " of " + S.sets + " · " + S.mode;
    $("progress-fill").style.width =
      ((S.pos + 1) / S.questions.length * 100).toFixed(2) + "%";

    const bm = $("btn-bookmark");
    bm.classList.toggle("is-on", !!q.bookmarked);
    bm.innerHTML = (q.bookmarked ? "&#9733; Bookmarked" : "&#9734; Bookmark");

    const box = $("options");
    box.innerHTML = "";
    const flash = S.mode === "flashcard";

    if (!flash) {
      q.options.forEach((text, i) => {
        const li = document.createElement("li");
        const b = document.createElement("button");
        b.className = "opt";
        b.type = "button";
        b.innerHTML = '<span class="keycap">' + (i + 1) + "</span>";
        const span = document.createElement("span");
        span.textContent = text;
        b.appendChild(span);
        b.addEventListener("click", () => pick(i));

        if (rec && rec.picked !== null && rec.picked !== undefined) {
          b.disabled = S.mode !== "exam";
          if (S.mode === "exam") {
            if (i === rec.picked) b.classList.add("is-picked");
          } else {
            if (i === q.correct) b.classList.add("is-right");
            else if (i === rec.picked) b.classList.add("is-wrong");
          }
        } else if (rec && rec.revealed) {
          if (i === q.correct) b.classList.add("is-right");
          b.disabled = true;
        }
        li.appendChild(b);
        box.appendChild(li);
      });
    }

    renderVerdict(q, rec, flash);
    updateControls();
    S.shownAt = Date.now();
  }

  function renderVerdict(q, rec, flash) {
    const v = $("verdict");
    const answered = rec && rec.picked !== null && rec.picked !== undefined;
    const revealed = rec && rec.revealed;

    if (S.mode === "exam" || (!answered && !revealed)) {
      v.hidden = true;
      if (flash) {
        v.hidden = false;
        v.className = "verdict";
        $("verdict-line").textContent = "Press Space to reveal the answer.";
        $("verdict-answer").textContent = "";
        $("verdict-note").hidden = true;
      }
      return;
    }

    v.hidden = false;
    if (flash || !answered) {
      v.className = "verdict";
      $("verdict-line").textContent = "Answer";
    } else if (rec.right) {
      v.className = "verdict right";
      $("verdict-line").textContent = "Correct";
    } else {
      v.className = "verdict wrong";
      $("verdict-line").textContent = "Wrong";
    }
    $("verdict-answer").textContent = q.options[q.correct];
    const note = $("verdict-note");
    if (q.explanation) { note.textContent = q.explanation; note.hidden = false; }
    else { note.hidden = true; }
  }

  function updateControls() {
    $("btn-prev").disabled = S.pos === 0;
    $("btn-next").textContent =
      S.pos === S.questions.length - 1 ? "Finish" : "Next";
    $("btn-reveal").hidden = S.mode === "exam";
    $("btn-submit").hidden = S.mode !== "exam";
    tally();
  }

  function tally() {
    let right = 0, wrong = 0;
    Object.values(S.answers).forEach((a) => {
      if (a.right === true) right++;
      else if (a.right === false) wrong++;
    });
    if (S.mode === "exam") {
      const done = Object.keys(S.answers).length;
      $("tally-right").textContent = done;
      $("tally-wrong").textContent = S.questions.length - done;
      $("label-right").textContent = "answered";
      $("label-wrong").textContent = "left";
    } else {
      $("tally-right").textContent = right;
      $("tally-wrong").textContent = wrong;
      $("label-right").textContent = "right";
      $("label-wrong").textContent = "wrong";
    }
  }

  function pick(i) {
    const q = current();
    if (!q || S.mode === "flashcard") return;
    const existing = S.answers[q.id];
    if (existing && existing.picked !== null && existing.picked !== undefined
        && S.mode !== "exam") return;      // locked once answered

    const right = i === q.correct;
    const ms = Math.min(Date.now() - S.shownAt, 10 * 60 * 1000);
    S.answers[q.id] = { picked: i, right: right, revealed: false, ms: ms };

    if (S.mode !== "exam") {
      api("/api/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: q.id, result: right ? "correct" : "wrong",
                               time_ms: ms }),
      }).catch(() => {});
    }
    render();
    saveState();
  }

  function reveal() {
    const q = current();
    if (!q || S.mode === "exam") return;
    const rec = S.answers[q.id] || {};
    if (rec.picked === null || rec.picked === undefined) {
      S.answers[q.id] = { picked: null, right: null, revealed: true, ms: 0 };
    } else {
      rec.revealed = true;
    }
    render();
    saveState();
  }

  function skip() {
    const q = current();
    if (!q) return;
    if (!S.answers[q.id]) {
      S.answers[q.id] = { picked: null, right: null, revealed: false, ms: 0,
                          skipped: true };
      if (S.mode !== "exam") {
        api("/api/answer", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: q.id, result: "skipped", time_ms: 0 }),
        }).catch(() => {});
      }
    }
    next();
  }

  function next() {
    if (S.pos >= S.questions.length - 1) { finish(); return; }
    S.pos++;
    render();
    saveState();
  }

  function prev() {
    if (S.pos === 0) return;
    S.pos--;
    render();
    saveState();
  }

  async function bookmark() {
    const q = current();
    if (!q) return;
    try {
      const d = await api("/api/bookmark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: q.id }),
      });
      q.bookmarked = d.bookmarked;
      render();
      toast(d.bookmarked ? "Bookmarked" : "Bookmark removed", 1200);
    } catch (e) { toast(e.message); }
  }

  /* ---------------------------------------------------------------- timer */

  function startTimer() {
    stopTimer();
    $("timer").hidden = false;
    tick();
    S.timerId = setInterval(tick, 1000);
  }
  function stopTimer() {
    if (S.timerId) { clearInterval(S.timerId); S.timerId = null; }
  }
  function tick() {
    const left = Math.max(0, Math.floor((S.examEnds - Date.now()) / 1000));
    const m = String(Math.floor(left / 60)).padStart(2, "0");
    const s = String(left % 60).padStart(2, "0");
    const el = $("timer");
    el.textContent = m + ":" + s;
    el.classList.toggle("low", left <= 300);
    if (left <= 0) { stopTimer(); toast("Time is up - submitting."); finish(); }
  }

  /* -------------------------------------------------------------- finish */

  async function finish() {
    stopTimer();

    if (S.mode === "exam") {
      const items = S.questions.map((q) => {
        const a = S.answers[q.id];
        if (!a || a.picked === null || a.picked === undefined) {
          return { id: q.id, result: "skipped", time_ms: 0 };
        }
        return { id: q.id, result: a.right ? "correct" : "wrong", time_ms: a.ms || 0 };
      });
      try {
        await api("/api/answers", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items: items }),
        });
      } catch (e) { toast("Could not save every answer: " + e.message, 4000); }
    }

    let right = 0, wrong = 0, skipped = 0, ms = 0, counted = 0;
    const missed = [];
    S.questions.forEach((q) => {
      const a = S.answers[q.id];
      if (!a || a.picked === null || a.picked === undefined) { skipped++; return; }
      counted++;
      ms += a.ms || 0;
      if (a.right) right++;
      else { wrong++; missed.push({ q: q, picked: q.options[a.picked] }); }
    });

    $("r-score").textContent = right;
    $("r-total").textContent = S.questions.length;
    $("r-acc").textContent = counted ? Math.round(right / counted * 100) + "%" : "0%";
    $("r-time").textContent = counted ? (ms / 1000 / counted).toFixed(1) + "s" : "0s";
    $("r-skip").textContent = skipped;

    const list = $("r-list");
    list.innerHTML = "";
    missed.slice(0, 100).forEach((m) => {
      const li = document.createElement("li");
      const p1 = document.createElement("p");
      p1.className = "rq";
      p1.textContent = m.q.question;
      const p2 = document.createElement("p");
      p2.className = "ra";
      p2.innerHTML = '<span class="bad"></span> &rarr; <span class="good"></span>';
      p2.querySelector(".bad").textContent = m.picked;
      p2.querySelector(".good").textContent = m.q.options[m.q.correct];
      li.appendChild(p1); li.appendChild(p2);
      list.appendChild(li);
    });
    $("r-list-wrap").hidden = missed.length === 0;
    $("btn-review-wrong").hidden = missed.length === 0;
    $("btn-next-set").hidden = S.setIndex >= S.sets - 1;

    S.answers = {};
    saveState();
    show("result");
  }

  /* ---------------------------------------------------------------- stats */

  async function openStats() {
    let d;
    try { d = await api("/api/stats"); }
    catch (e) { toast(e.message); return; }

    const cells = [
      ["Answered", d.answered.toLocaleString()],
      ["Accuracy", d.accuracy + "%"],
      ["Correct", d.correct.toLocaleString()],
      ["Wrong", d.wrong.toLocaleString()],
      ["Skipped", d.skipped.toLocaleString()],
      ["Avg time", d.avg_seconds + "s"],
      ["Bookmarks", d.bookmarks.toLocaleString()],
      ["Bank covered", d.progress_pct + "%"],
    ];
    const grid = $("stat-grid");
    grid.innerHTML = "";
    cells.forEach(([label, value]) => {
      const div = document.createElement("div");
      div.className = "stat";
      const b = document.createElement("b");
      b.textContent = value;
      const s = document.createElement("span");
      s.textContent = label;
      div.appendChild(b); div.appendChild(s);
      grid.appendChild(div);
    });

    const tb = $("topic-table").querySelector("tbody");
    tb.innerHTML = "";
    d.topics.forEach((t) => {
      const tr = document.createElement("tr");
      const acc = t.accuracy;
      const cls = acc === null ? "" : (acc < 60 ? "weak" : (acc >= 80 ? "strong" : ""));
      [t.topic, t.total, t.attempts, t.correct, t.wrong,
       acc === null ? "—" : acc + "%"].forEach((v, i) => {
        const td = document.createElement("td");
        td.textContent = v;
        if (i === 5 && cls) td.className = cls;
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });

    show("stats");
  }

  /* --------------------------------------------------------------- import */

  async function doImport() {
    const input = $("import-file");
    const note = $("import-note");
    if (!input.files.length) { note.className = "note bad";
      note.textContent = "Choose a file first."; return; }

    const fd = new FormData();
    fd.append("file", input.files[0]);
    note.className = "note";
    note.textContent = "Reading the file…";
    try {
      const d = await api("/api/import", { method: "POST", body: fd });
      note.textContent =
        d.added + " questions added, " + d.duplicates +
        " already in the bank. The bank now holds " + d.total + ".";
      await loadMeta();
    } catch (e) {
      note.className = "note bad";
      note.textContent = e.message;
    }
  }

  /* -------------------------------------------------------------- keyboard */

  document.addEventListener("keydown", (ev) => {
    const tag = (ev.target.tagName || "").toLowerCase();
    const typing = tag === "input" || tag === "select" || tag === "textarea";

    if (ev.key === "Escape") {
      if (!$("screen-quiz").hidden) { show("setup"); loadMeta(); }
      return;
    }
    if (typing || $("screen-quiz").hidden) return;

    if (ev.key >= "1" && ev.key <= "4") {
      ev.preventDefault();
      pick(parseInt(ev.key, 10) - 1);
    } else if (ev.key === "Enter") {
      ev.preventDefault(); next();
    } else if (ev.key === "Backspace") {
      ev.preventDefault(); prev();
    } else if (ev.key === " ") {
      ev.preventDefault(); reveal();
    } else if (ev.key === "b" || ev.key === "B") {
      ev.preventDefault(); bookmark();
    } else if (ev.key === "s" || ev.key === "S") {
      ev.preventDefault(); skip();
    }
  });

  /* ---------------------------------------------------------------- wiring */

  $("mode-picker").addEventListener("click", (ev) => {
    const b = ev.target.closest(".mode");
    if (!b) return;
    document.querySelectorAll(".mode").forEach((m) => m.classList.remove("is-on"));
    b.classList.add("is-on");
    S.mode = b.dataset.mode;
    $("f-filter").disabled = S.mode === "revision";
    if (S.mode === "revision") $("f-filter").value = "wrong";
    refreshCount();
  });

  ["f-topic", "f-filter", "f-search"].forEach((id) =>
    $(id).addEventListener("input", refreshCount));

  $("btn-start").addEventListener("click", () =>
    startQuiz(parseInt($("f-set").value, 10) || 0));

  $("btn-resume").addEventListener("click", () => {
    let p;
    try { p = JSON.parse($("btn-resume").dataset.payload); }
    catch (e) { return; }
    S.mode = p.mode || "practice";
    S.seed = p.seed || 1;
    S.examEnds = p.examEnds || 0;
    S.answers = p.answers || {};
    $("f-topic").value = p.topic || "";
    $("f-filter").value = p.filter || "all";
    $("f-search").value = p.search || "";
    $("f-shuffle-q").checked = !!p.shuffleQ;
    $("f-shuffle-o").checked = !!p.shuffleO;
    document.querySelectorAll(".mode").forEach((m) =>
      m.classList.toggle("is-on", m.dataset.mode === S.mode));
    startQuiz(p.setIndex || 0, true, p.pos || 0);
  });

  $("btn-prev").addEventListener("click", prev);
  $("btn-next").addEventListener("click", next);
  $("btn-skip").addEventListener("click", skip);
  $("btn-reveal").addEventListener("click", reveal);
  $("btn-submit").addEventListener("click", finish);
  $("btn-bookmark").addEventListener("click", bookmark);
  $("btn-home").addEventListener("click", () => { show("setup"); loadMeta(); });

  $("btn-stats").addEventListener("click", openStats);
  $("btn-stats-home").addEventListener("click", () => { show("setup"); loadMeta(); });
  $("btn-print").addEventListener("click", () => window.print());
  $("btn-reset").addEventListener("click", async () => {
    if (!confirm("Delete every answer, bookmark and statistic? " +
                 "The questions themselves stay.")) return;
    try {
      await api("/api/reset", { method: "POST" });
      localStorage.removeItem(LS_KEY);
      toast("Progress cleared.");
      openStats();
    } catch (e) { toast(e.message); }
  });

  $("btn-import").addEventListener("click", () => show("import"));
  $("btn-import-home").addEventListener("click", () => { show("setup"); loadMeta(); });
  $("btn-do-import").addEventListener("click", doImport);

  $("btn-next-set").addEventListener("click", () => startQuiz(S.setIndex + 1));
  $("btn-result-home").addEventListener("click", () => { show("setup"); loadMeta(); });
  $("btn-review-wrong").addEventListener("click", () => {
    $("f-filter").value = "wrong";
    S.mode = "practice";
    document.querySelectorAll(".mode").forEach((m) =>
      m.classList.toggle("is-on", m.dataset.mode === "practice"));
    startQuiz(0);
  });

  loadMeta().catch((e) => toast(e.message, 6000));
})();

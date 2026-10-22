(function () {
  "use strict";

  var ROLE = "backend_developer";
  var AXIS_LABELS = { systems: "담당 시스템", routines: "반복 업무", incidents: "사고 이력", people: "관계자" };

  var params = new URLSearchParams(window.location.search);
  var leaver = params.get("leaver") || "default";
  var storageKey = "handoff.session." + leaver;

  var el = {
    number: document.getElementById("session-number"),
    timer: document.getElementById("timer"),
    axis: document.getElementById("axis-label"),
    question: document.getElementById("question-text"),
    transcript: document.getElementById("transcript"),
    input: document.getElementById("answer-input"),
    send: document.getElementById("send-button"),
    end: document.getElementById("end-button"),
    start: document.getElementById("start-button"),
    itemsToday: document.getElementById("items-today"),
    itemsTotal: document.getElementById("items-total"),
    summary: document.getElementById("summary"),
    summaryLines: document.getElementById("summary-lines"),
    error: document.getElementById("error")
  };

  var state = { sessionId: null, remaining: 0, timerId: null, busy: false, ending: false, open: false, inflight: null };

  function HttpError(status, detail) {
    this.status = status;
    this.detail = detail;
  }

  function request(method, path, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (res) {
      return res.text().then(function (raw) {
        var data = null;
        try { data = raw ? JSON.parse(raw) : null; } catch (e) { data = null; }
        if (!res.ok) {
          var detail = data && typeof data.detail === "string" ? data.detail : "";
          throw new HttpError(res.status, detail);
        }
        return data;
      });
    });
  }

  function errorMessage(err) {
    if (!(err instanceof HttpError)) return "잠시 후 다시 시도해 주세요. (연결 실패)";
    if (err.status === 409 && err.detail === "session limit reached") return "모든 세션을 마쳤습니다. 수고하셨습니다.";
    if (err.status === 409 && err.detail === "question tree exhausted") return "준비된 질문을 모두 마쳤습니다.";
    if (err.status === 503) return "잠시 후 다시 시도해 주세요. (연결 실패)";
    return "오류가 발생했습니다: " + err.status;
  }

  function isTimeOver(err) {
    return err instanceof HttpError && err.status === 409 && err.detail === "session time over";
  }

  function isClosed(err) {
    return err instanceof HttpError && err.status === 409 && err.detail === "session closed";
  }

  function showError(err) {
    el.error.textContent = errorMessage(err);
    el.error.classList.remove("hidden");
  }

  function clearError() {
    el.error.textContent = "";
    el.error.classList.add("hidden");
  }

  function show(node, visible) {
    node.classList.toggle("hidden", !visible);
  }

  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function renderTimer() {
    var s = Math.max(0, state.remaining);
    el.timer.textContent = pad(Math.floor(s / 60)) + ":" + pad(s % 60) + " 남음";
  }

  function renderNumber(number, planned) {
    el.number.textContent = "세션 " + number + "/" + planned;
  }

  function renderCounts(today, total) {
    if (typeof today === "number") el.itemsToday.textContent = "오늘 쌓인 것: " + today + "항목";
    if (typeof total === "number") el.itemsTotal.textContent = "전체 " + total + "항목";
  }

  function addMessage(kind, text) {
    var div = document.createElement("div");
    div.className = "msg " + kind;
    div.textContent = text;
    el.transcript.appendChild(div);
    el.transcript.scrollTop = el.transcript.scrollHeight;
  }

  function showQuestion(q, appendToTranscript) {
    el.question.textContent = q.text;
    el.axis.textContent = AXIS_LABELS[q.axis] || q.axis;
    if (appendToTranscript) addMessage("bot", q.text);
  }

  function setControls(open) {
    state.open = open;
    el.input.disabled = !open;
    el.send.disabled = !open;
    el.end.disabled = !open;
    show(el.send, open);
    show(el.end, open);
    show(el.start, !open);
    el.start.disabled = false;
  }

  function renderSummary(lines) {
    var items = el.summaryLines.querySelectorAll("li.summary-line");
    var list = Array.isArray(lines) ? lines : [];
    for (var i = 0; i < items.length; i++) {
      items[i].textContent = list[i] || "";
    }
    show(el.summary, true);
  }

  function stopTimer() {
    if (state.timerId !== null) {
      clearInterval(state.timerId);
      state.timerId = null;
    }
  }

  function startTimer(seconds) {
    stopTimer();
    state.remaining = Math.max(0, seconds);
    renderTimer();
    state.timerId = setInterval(function () {
      state.remaining = Math.max(0, state.remaining - 1);
      renderTimer();
      if (state.remaining <= 0) {
        stopTimer();
        onExpired();
      }
    }, 1000);
  }

  function syncRemaining(seconds) {
    if (typeof seconds === "number") {
      state.remaining = Math.max(0, seconds);
      renderTimer();
    }
  }

  function showEnded(summaryLines) {
    stopTimer();
    state.remaining = 0;
    renderTimer();
    setControls(false);
    el.question.textContent = "";
    renderSummary(summaryLines);
  }

  function endFlow() {
    if (state.ending || !state.sessionId) return Promise.resolve();
    state.ending = true;
    stopTimer();
    el.input.disabled = true;
    el.send.disabled = true;
    el.end.disabled = true;
    var id = state.sessionId;
    return request("POST", "/sessions/" + encodeURIComponent(id) + "/end")
      .then(function (data) {
        clearError();
        renderNumber(data.number, el.number.dataset.planned || "?");
        renderCounts(undefined, data.items_total);
        showEnded(data.summary);
      })
      .catch(function (err) {
        if (isClosed(err)) {
          return request("GET", "/sessions/" + encodeURIComponent(id)).then(function (s) {
            clearError();
            renderCounts(s.items_today, s.items_total);
            showEnded(s.summary);
          });
        }
        throw err;
      })
      .catch(function (err) {
        showError(err);
        if (state.open && state.remaining > 0) {
          el.input.disabled = false;
          el.send.disabled = false;
          startTimer(state.remaining);
        }
        el.end.disabled = false;
      })
      .then(function () {
        state.ending = false;
      });
  }

  function sendAnswer() {
    var text = el.input.value.trim();
    if (!text || !state.sessionId || state.busy) return Promise.resolve();
    state.busy = true;
    el.send.disabled = true;
    var id = state.sessionId;
    state.inflight = request("POST", "/sessions/" + encodeURIComponent(id) + "/answers", { text: text })
      .then(function (data) {
        clearError();
        addMessage("user", text);
        el.input.value = "";
        syncRemaining(data.remaining_seconds);
        renderCounts(data.items_today, data.items_total);
        if (data.next_question) {
          showQuestion(data.next_question, true);
          return null;
        }
        return "end";
      })
      .catch(function (err) {
        if (isTimeOver(err)) return "end";
        showError(err);
        return null;
      })
      .then(function (next) {
        state.busy = false;
        if (state.open && !state.ending) el.send.disabled = false;
        return next;
      });
    return state.inflight;
  }

  function onSend() {
    if (state.ending) return;
    sendAnswer().then(function (next) {
      if (next === "end") endFlow();
    });
  }

  function onExpired() {
    if (state.ending) return;
    el.input.disabled = true;
    var pending = state.busy ? state.inflight : (el.input.value.trim() ? sendAnswer() : Promise.resolve());
    pending.then(function () {
      endFlow();
    });
  }

  function beginOpen(data) {
    state.sessionId = data.session_id;
    el.number.dataset.planned = String(data.planned);
    renderNumber(data.number, data.planned);
    renderCounts(data.items_today, data.items_total);
    show(el.summary, false);
    setControls(true);
    if (data.question) showQuestion(data.question, true);
    startTimer(data.remaining_seconds);
    if (data.remaining_seconds <= 0) {
      stopTimer();
      onExpired();
    }
  }

  function onStart() {
    if (state.busy) return;
    state.busy = true;
    el.start.disabled = true;
    clearError();
    request("POST", "/sessions", { role: ROLE, leaver: leaver })
      .then(function (data) {
        try { localStorage.setItem(storageKey, data.session_id); } catch (e) { /* storage unavailable */ }
        el.transcript.textContent = "";
        el.input.value = "";
        beginOpen(data);
      })
      .catch(function (err) {
        showError(err);
        el.start.disabled = false;
      })
      .then(function () {
        state.busy = false;
      });
  }

  function restore() {
    var id = null;
    try { id = localStorage.getItem(storageKey); } catch (e) { id = null; }
    setControls(false);
    renderTimer();
    if (!id) return;
    request("GET", "/sessions/" + encodeURIComponent(id))
      .then(function (s) {
        el.number.dataset.planned = String(s.planned);
        renderNumber(s.number, s.planned);
        renderCounts(s.items_today, s.items_total);
        if (s.status === "open") {
          beginOpen(s);
        } else {
          state.sessionId = s.session_id;
          showEnded(s.summary);
        }
      })
      .catch(function (err) {
        if (err instanceof HttpError && err.status === 404) {
          try { localStorage.removeItem(storageKey); } catch (e) { /* storage unavailable */ }
          return;
        }
        showError(err);
      });
  }

  el.start.addEventListener("click", onStart);
  el.send.addEventListener("click", onSend);
  el.end.addEventListener("click", function () { endFlow(); });
  el.input.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
      ev.preventDefault();
      onSend();
    }
  });

  restore();
})();

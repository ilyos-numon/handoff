(function () {
  "use strict";

  var AXIS_LABELS = {
    systems: "담당 시스템",
    routines: "반복 업무",
    incidents: "사고 이력",
    people: "관계자",
  };
  var CERTAINTY_LABELS = {
    certain: "근거 확실",
    uncertain: "근거 불확실 · 퇴사자 확인 권장",
  };
  var ARTIFACT_KINDS = ["runbook", "onboarding_30d", "pitfalls_top10", "adr", "premortem"];
  var ARTIFACT_TITLES = {
    runbook: "운영 런북",
    onboarding_30d: "30일 온보딩 플랜",
    pitfalls_top10: "함정 Top10",
    adr: "결정 기록(ADR)",
    premortem: "사전 부검: 먼저 터질 일 Top5",
  };
  var SCREEN_ROLE = "successor";
  var LOGIN_REQUIRED = "초대 링크로 들어와 주세요.";
  var FORBIDDEN = "이 화면을 볼 권한이 없습니다.";
  var QUEUED_NOTICE = "퇴사자 질문 목록에 올렸습니다. 다음 세션에서 물어봅니다.";

  var log = document.getElementById("chat-log");
  var input = document.getElementById("chat-input");
  var send = document.getElementById("chat-send");
  var form = document.getElementById("chat-form");
  var fenceForm = document.getElementById("fence-form");
  var fenceInput = document.getElementById("fence-input");
  var fenceSend = document.getElementById("fence-send");
  var errorBox = document.getElementById("chat-error");
  var artifactsBox = document.getElementById("artifacts");

  var leaver = null;

  function showError(text) {
    errorBox.textContent = text;
    errorBox.classList.remove("hidden");
  }

  function clearError() {
    errorBox.textContent = "";
    errorBox.classList.add("hidden");
  }

  function errorText(status) {
    if (status === 401) {
      return LOGIN_REQUIRED;
    }
    if (status === 403) {
      return FORBIDDEN;
    }
    if (status === 503) {
      return "잠시 후 다시 시도해 주세요. (연결 실패)";
    }
    return "오류가 발생했습니다: " + status;
  }

  function setEnabled(enabled) {
    send.disabled = !enabled;
    fenceSend.disabled = !enabled;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function citationMeta(c) {
    var number = c.session_number === null || c.session_number === undefined ? "?" : c.session_number;
    var label = AXIS_LABELS[c.axis] || c.axis;
    var meta = "세션 " + number + " (" + label + ")";
    if (c.created_at) {
      meta += " · " + String(c.created_at).slice(0, 10);
    }
    return meta;
  }

  function appendUser(text) {
    log.appendChild(el("div", "msg user", text));
  }

  function appendExtras(box, data) {
    if (data.certainty) {
      box.appendChild(el("p", "certainty", CERTAINTY_LABELS[data.certainty] || data.certainty));
    }
    if (data.queued) {
      box.appendChild(el("p", "queued-notice", QUEUED_NOTICE));
    }
    if (data.route) {
      box.appendChild(
        el("p", "route", "먼저 물어볼 역할: " + data.route.ask_role + " (" + data.route.reason + ")")
      );
    }
  }

  function appendAnswer(data) {
    var answer = data.answer || {};
    var citations = Array.isArray(answer.citations) ? answer.citations : [];
    var box = el("div", citations.length ? "bot-answer" : "bot-answer not-found");
    box.appendChild(el("p", "answer-text", answer.answer));
    citations.forEach(function (c) {
      var item = el("div", "citation");
      item.appendChild(el("div", "citation-meta", citationMeta(c)));
      item.appendChild(el("div", "citation-excerpt", "\"" + c.excerpt + "\""));
      box.appendChild(item);
    });
    appendExtras(box, data);
    log.appendChild(box);
  }

  function appendFence(data) {
    var box = el("div", data.found ? "msg bot fence-answer" : "msg bot fence-answer not-found");
    (data.reasons || []).forEach(function (p) {
      box.appendChild(el("p", "fence-reason", p.text));
    });
    (data.risks || []).forEach(function (p) {
      box.appendChild(el("p", "fence-risk", p.text));
    });
    appendExtras(box, { certainty: data.certainty, queued: data.queued, route: null });
    log.appendChild(box);
  }

  async function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async function ask() {
    var question = input.value.trim();
    if (!question) {
      showError("질문을 입력해 주세요.");
      return;
    }
    clearError();
    setEnabled(false);
    try {
      var res = await postJson("/kb/ask", { question: question, leaver: leaver });
      if (!res.ok) {
        showError(errorText(res.status));
        return;
      }
      var data = await res.json();
      appendUser(question);
      appendAnswer(data);
      input.value = "";
      log.scrollTop = log.scrollHeight;
    } catch (e) {
      showError(errorText(503));
    } finally {
      setEnabled(true);
    }
  }

  async function askFence() {
    var subject = fenceInput.value.trim();
    if (!subject) {
      showError("이유가 궁금한 것을 입력해 주세요.");
      return;
    }
    clearError();
    setEnabled(false);
    try {
      var res = await postJson("/kb/fence", { leaver: leaver, subject: subject });
      if (!res.ok) {
        showError(errorText(res.status));
        return;
      }
      var data = await res.json();
      appendUser(subject);
      appendFence(data);
      fenceInput.value = "";
      log.scrollTop = log.scrollHeight;
    } catch (e) {
      showError(errorText(503));
    } finally {
      setEnabled(true);
    }
  }

  function renderArtifact(kind, data) {
    var item = el("div", "artifact");
    item.appendChild(el("h3", "artifact-title", ARTIFACT_TITLES[kind] || data.title));
    var details = el("details");
    details.appendChild(el("summary", "", "내용 보기"));
    (data.sections || []).forEach(function (s) {
      details.appendChild(el("h4", "artifact-heading", s.heading));
      details.appendChild(el("p", "artifact-body", s.body));
    });
    item.appendChild(details);
    return item;
  }

  async function loadArtifacts() {
    var results = await Promise.all(
      ARTIFACT_KINDS.map(async function (kind) {
        try {
          var res = await fetch(
            "/leavers/" + encodeURIComponent(leaver) + "/artifacts/" + kind
          );
          if (res.status === 401 || res.status === 403) {
            showError(errorText(res.status));
            return null;
          }
          if (!res.ok) {
            return null;
          }
          return { kind: kind, data: await res.json() };
        } catch (e) {
          return null;
        }
      })
    );
    results.forEach(function (r) {
      if (r) {
        artifactsBox.appendChild(renderArtifact(r.kind, r.data));
      }
    });
    if (artifactsBox.querySelector(".artifact")) {
      artifactsBox.classList.remove("hidden");
    }
  }

  function queryLeaver() {
    var value = new URLSearchParams(window.location.search).get("leaver");
    return value ? value : "default";
  }

  async function init() {
    setEnabled(false);
    var me;
    try {
      var res = await fetch("/auth/me");
      if (!res.ok) {
        showError(errorText(res.status));
        return;
      }
      me = await res.json();
    } catch (e) {
      showError(errorText(503));
      return;
    }
    if (me.auth && !me.role) {
      showError(LOGIN_REQUIRED);
      return;
    }
    if (me.auth && me.role !== SCREEN_ROLE) {
      showError(FORBIDDEN);
      return;
    }
    leaver = me.auth ? me.leaver : queryLeaver();
    setEnabled(true);
    await loadArtifacts();
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (leaver !== null) {
      ask();
    }
  });

  fenceForm.addEventListener("submit", function (event) {
    event.preventDefault();
    if (leaver !== null) {
      askFence();
    }
  });

  init();
})();

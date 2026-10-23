(function () {
  "use strict";

  var AXIS_LABELS = {
    systems: "담당 시스템",
    routines: "반복 업무",
    incidents: "사고 이력",
    people: "관계자",
  };
  var ROLE = "backend_developer";
  var SCREEN_ROLE = "manager";
  var QUEUE_SOURCE_LABELS = {
    manager: "팀장 우선",
    successor: "후임 질문",
    premortem: "사전 부검",
    gap: "빈 구멍",
    generated: "업무 자료",
  };
  var QUEUE_STATUS_LABELS = { pending: "대기", asked: "질문함", answered: "답변됨" };
  var GAP_RISK_LABELS = { high: "높음", medium: "보통", low: "낮음" };
  var ARTIFACT_KINDS = ["runbook", "onboarding_30d", "pitfalls_top10", "adr", "premortem"];
  var ARTIFACT_TITLES = {
    runbook: "운영 런북",
    onboarding_30d: "30일 온보딩 플랜",
    pitfalls_top10: "함정 Top10",
    adr: "결정 기록(ADR)",
    premortem: "사전 부검: 먼저 터질 일 Top5",
  };
  var MSG_LOGIN = "초대 링크로 들어와 주세요.";
  var MSG_FORBIDDEN = "이 화면을 볼 권한이 없습니다.";
  var MSG_RETRY = "잠시 후 다시 시도해 주세요. (연결 실패)";

  var params = new URLSearchParams(window.location.search);
  var leaver = params.get("leaver") || "default";
  var blocked = false;

  var title = document.getElementById("report-title");
  var itemsTotal = document.getElementById("items-total-report");
  var axesBox = document.getElementById("axes");
  var topicsBox = document.getElementById("empty-topics");
  var errorBox = document.getElementById("report-error");
  var statusBox = document.getElementById("report-status");
  var queueBox = document.getElementById("queue");
  var gapsBox = document.getElementById("gaps");
  var unknownBox = document.getElementById("unknown-areas");
  var artifactsBox = document.getElementById("artifacts");
  var priorityForm = document.getElementById("priority-form");
  var priorityInput = document.getElementById("priority-input");

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

  function showError(text) {
    errorBox.textContent = text;
    errorBox.classList.remove("hidden");
  }

  function setStatus(text) {
    statusBox.textContent = text;
  }

  function block(text) {
    blocked = true;
    showError(text);
    Array.prototype.forEach.call(document.querySelectorAll("main button, main input"), function (node) {
      node.disabled = true;
    });
  }

  function leaverPath(suffix) {
    return "/leavers/" + encodeURIComponent(leaver) + suffix;
  }

  // 401/403 은 화면을 막는다. 그 밖의 실패는 { ok: false, status, detail } 로 돌려준다.
  async function api(method, url, body) {
    var init = { method: method, headers: {} };
    if (body !== undefined) {
      init.headers["content-type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    var res;
    try {
      res = await fetch(url, init);
    } catch (e) {
      return { ok: false, status: 0, detail: "" };
    }
    if (res.status === 401) {
      block(MSG_LOGIN);
      return { ok: false, status: 401, detail: "" };
    }
    if (res.status === 403) {
      block(MSG_FORBIDDEN);
      return { ok: false, status: 403, detail: "" };
    }
    var data = null;
    try {
      data = await res.json();
    } catch (e) {
      data = null;
    }
    if (!res.ok) {
      var detail = data && typeof data.detail === "string" ? data.detail : "";
      return { ok: false, status: res.status, detail: detail };
    }
    return { ok: true, status: res.status, data: data };
  }

  function failText(r) {
    if (r.status === 0 || r.status === 503) {
      return MSG_RETRY;
    }
    return "오류가 발생했습니다: " + r.status + (r.detail ? " (" + r.detail + ")" : "");
  }

  function ratio(a) {
    return a.total > 0 ? a.covered / a.total : 0;
  }

  function axisLabel(axis, fallback) {
    return AXIS_LABELS[axis] || fallback || axis;
  }

  function renderAxes(axes) {
    axesBox.textContent = "";
    var lowestIndex = -1;
    var lowestRatio = Infinity;
    axes.forEach(function (a, i) {
      var r = ratio(a);
      if (r < lowestRatio) {
        lowestRatio = r;
        lowestIndex = i;
      }
    });
    axes.forEach(function (a, i) {
      var row = el("div", "axis-row");
      row.appendChild(el("span", "axis-label", axisLabel(a.axis, a.label)));
      var bar = el("span", "axis-bar");
      var fill = document.createElement("span");
      fill.style.width = ratio(a) * 100 + "%";
      bar.appendChild(fill);
      row.appendChild(bar);
      row.appendChild(el("span", "axis-count", a.covered + "/" + a.total));
      if (i === lowestIndex) {
        row.classList.add("lowest");
        row.appendChild(el("span", "lowest-mark", "← 가장 비어 있음"));
      }
      var extra = Number(a.queued_answered) || 0;
      if (extra > 0) {
        row.appendChild(el("span", "axis-extra", "추가 질문 " + extra + "개 답함"));
      }
      axesBox.appendChild(row);
    });
  }

  function renderTopics(topics) {
    topicsBox.textContent = "";
    if (!topics.length) {
      topicsBox.appendChild(el("li", "empty-topic-none", "비어 있는 주제가 없습니다."));
      return;
    }
    topics.forEach(function (t) {
      topicsBox.appendChild(el("li", "empty-topic", axisLabel(t.axis, t.label) + " · " + t.text));
    });
  }

  function renderList(box, items, emptyText, className, textOf) {
    box.textContent = "";
    if (!items.length) {
      box.appendChild(el("li", "report-list-none", emptyText));
      return;
    }
    items.forEach(function (item) {
      box.appendChild(el("li", className, textOf(item)));
    });
  }

  function renderQueue(items) {
    renderList(queueBox, items, "큐에 질문이 없습니다.", "queue-item", function (q) {
      var source = QUEUE_SOURCE_LABELS[q.source] || q.source;
      var state = QUEUE_STATUS_LABELS[q.status] || q.status;
      return q.text + " · " + source + " · " + state;
    });
  }

  function renderGaps(report) {
    var gaps = Array.isArray(report.gaps) ? report.gaps : [];
    var areas = Array.isArray(report.unknown_areas) ? report.unknown_areas : [];
    renderList(gapsBox, gaps, "찾은 빈 구멍이 없습니다.", "gap", function (g) {
      return g.topic + " · 위험도 " + (GAP_RISK_LABELS[g.risk] || g.risk);
    });
    renderList(unknownBox, areas, "찾은 영역이 없습니다.", "unknown-area", function (u) {
      return u.topic;
    });
  }

  function renderArtifact(artifact) {
    var box = el("div", "artifact");
    box.dataset.kind = artifact.kind;
    box.appendChild(el("h3", "artifact-title", ARTIFACT_TITLES[artifact.kind] || artifact.title));
    var details = document.createElement("details");
    details.appendChild(el("summary", null, "내용 보기"));
    (Array.isArray(artifact.sections) ? artifact.sections : []).forEach(function (s) {
      details.appendChild(el("h4", "artifact-heading", s.heading));
      details.appendChild(el("p", "artifact-body", s.body));
    });
    box.appendChild(details);
    return box;
  }

  async function loadCoverage() {
    var url = "/coverage?leaver=" + encodeURIComponent(leaver) + "&role=" + encodeURIComponent(ROLE);
    var r = await api("GET", url);
    if (blocked) {
      return;
    }
    if (!r.ok) {
      showError(failText(r));
      return;
    }
    var data = r.data;
    title.textContent =
      "인수인계 진행 상황 · 세션 " + data.sessions_done + "/" + data.sessions_planned + " 기준";
    itemsTotal.textContent = "전체 " + data.items_total + "항목";
    renderAxes(Array.isArray(data.axes) ? data.axes : []);
    renderTopics(Array.isArray(data.empty_topics) ? data.empty_topics : []);
  }

  async function loadQueue() {
    var r = await api("GET", leaverPath("/queue"));
    if (r.ok && Array.isArray(r.data)) {
      renderQueue(r.data);
    }
  }

  async function loadGaps() {
    var r = await api("GET", leaverPath("/gaps"));
    if (r.ok && r.data) {
      renderGaps(r.data);
    }
  }

  async function loadArtifacts() {
    var found = [];
    for (var i = 0; i < ARTIFACT_KINDS.length && !blocked; i += 1) {
      var r = await api("GET", leaverPath("/artifacts/" + ARTIFACT_KINDS[i]));
      if (r.ok && r.data) {
        found.push(r.data);
      }
    }
    artifactsBox.textContent = "";
    found.forEach(function (a) {
      artifactsBox.appendChild(renderArtifact(a));
    });
  }

  async function reload() {
    await loadCoverage();
    if (blocked) {
      return;
    }
    await loadQueue();
    if (blocked) {
      return;
    }
    await loadGaps();
  }

  async function run(button, action) {
    if (blocked) {
      return;
    }
    if (button) {
      button.disabled = true;
    }
    setStatus("처리 중...");
    try {
      var text = await action();
      if (blocked) {
        setStatus("");
        return;
      }
      setStatus(text);
      await reload();
    } finally {
      if (button && !blocked) {
        button.disabled = false;
      }
    }
  }

  function bind(id, action) {
    var button = document.getElementById(id);
    button.addEventListener("click", function () {
      run(button, action);
    });
  }

  bind("sources-mock", async function () {
    var r = await api("POST", leaverPath("/sources/mock"));
    if (!r.ok) {
      return failText(r);
    }
    return "모의 업무 자료 " + (Array.isArray(r.data) ? r.data.length : 0) + "건을 불러왔습니다.";
  });

  bind("questions-generate", async function () {
    var r = await api("POST", leaverPath("/questions/generate"), { role: ROLE });
    if (!r.ok) {
      return failText(r);
    }
    var queued = r.data && Array.isArray(r.data.queued) ? r.data.queued.length : 0;
    return "업무 자료로 질문 " + queued + "개를 큐에 넣었습니다.";
  });

  bind("premortem-add", async function () {
    var r = await api("POST", leaverPath("/premortem/questions?role=" + encodeURIComponent(ROLE)));
    if (!r.ok) {
      return failText(r);
    }
    return "사전 부검 질문 " + (Array.isArray(r.data) ? r.data.length : 0) + "개를 큐에 넣었습니다.";
  });

  bind("gaps-refresh", async function () {
    var r = await api("POST", leaverPath("/gaps/refresh?role=" + encodeURIComponent(ROLE)));
    if (!r.ok) {
      return failText(r);
    }
    var gaps = Array.isArray(r.data.gaps) ? r.data.gaps.length : 0;
    var areas = Array.isArray(r.data.unknown_areas) ? r.data.unknown_areas.length : 0;
    return "빈 구멍 " + gaps + "개, 아무도 확실히 모르는 영역 " + areas + "개를 찾았습니다.";
  });

  priorityForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = priorityInput.value.trim();
    if (!text) {
      setStatus("질문을 입력해 주세요.");
      return;
    }
    run(document.getElementById("priority-send"), async function () {
      var r = await api("POST", leaverPath("/priority-questions"), { text: text, role: ROLE });
      if (!r.ok) {
        return failText(r);
      }
      priorityInput.value = "";
      return r.status === 201 ? "우선 질문을 큐에 넣었습니다." : "이미 큐에 있는 질문입니다.";
    });
  });

  Array.prototype.forEach.call(
    document.querySelectorAll("#artifact-buttons button[data-kind]"),
    function (button) {
      button.addEventListener("click", function () {
        var kind = button.dataset.kind;
        run(button, async function () {
          var r = await api("POST", leaverPath("/artifacts/" + encodeURIComponent(kind)));
          if (!r.ok) {
            return failText(r);
          }
          await loadArtifacts();
          return "산출물을 만들었습니다: " + (ARTIFACT_TITLES[kind] || kind);
        });
      });
    }
  );

  async function start() {
    var me = await api("GET", "/auth/me");
    if (blocked) {
      return;
    }
    if (me.ok && me.data && me.data.auth) {
      if (!me.data.role) {
        block(MSG_LOGIN);
        return;
      }
      if (me.data.role !== SCREEN_ROLE) {
        block(MSG_FORBIDDEN);
        return;
      }
      leaver = me.data.leaver;
    }
    await reload();
    if (!blocked) {
      await loadArtifacts();
    }
  }

  start();
})();

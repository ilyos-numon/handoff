(function () {
  "use strict";

  var AXIS_LABELS = {
    systems: "담당 시스템",
    routines: "반복 업무",
    incidents: "사고 이력",
    people: "관계자",
  };
  var ROLE = "backend_developer";

  var params = new URLSearchParams(window.location.search);
  var leaver = params.get("leaver") || "default";

  var title = document.getElementById("report-title");
  var itemsTotal = document.getElementById("items-total-report");
  var axesBox = document.getElementById("axes");
  var topicsBox = document.getElementById("empty-topics");
  var errorBox = document.getElementById("report-error");

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

  async function load() {
    try {
      var url = "/coverage?leaver=" + encodeURIComponent(leaver) + "&role=" + encodeURIComponent(ROLE);
      var res = await fetch(url);
      if (!res.ok) {
        showError(res.status === 503 ? "잠시 후 다시 시도해 주세요. (연결 실패)" : "오류가 발생했습니다: " + res.status);
        return;
      }
      var data = await res.json();
      title.textContent =
        "인수인계 진행 상황 · 세션 " + data.sessions_done + "/" + data.sessions_planned + " 기준";
      itemsTotal.textContent = "전체 " + data.items_total + "항목";
      renderAxes(Array.isArray(data.axes) ? data.axes : []);
      renderTopics(Array.isArray(data.empty_topics) ? data.empty_topics : []);
    } catch (e) {
      showError("잠시 후 다시 시도해 주세요. (연결 실패)");
    }
  }

  load();
})();

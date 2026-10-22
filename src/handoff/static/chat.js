(function () {
  "use strict";

  var AXIS_LABELS = {
    systems: "담당 시스템",
    routines: "반복 업무",
    incidents: "사고 이력",
    people: "관계자",
  };

  var log = document.getElementById("chat-log");
  var input = document.getElementById("chat-input");
  var send = document.getElementById("chat-send");
  var form = document.getElementById("chat-form");
  var errorBox = document.getElementById("chat-error");

  function showError(text) {
    errorBox.textContent = text;
    errorBox.classList.remove("hidden");
  }

  function clearError() {
    errorBox.textContent = "";
    errorBox.classList.add("hidden");
  }

  function errorText(status) {
    if (status === 503) {
      return "잠시 후 다시 시도해 주세요. (연결 실패)";
    }
    return "오류가 발생했습니다: " + status;
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

  function appendAnswer(data) {
    var citations = Array.isArray(data.citations) ? data.citations : [];
    var box = el("div", citations.length ? "bot-answer" : "bot-answer not-found");
    box.appendChild(el("p", "answer-text", data.answer));
    citations.forEach(function (c) {
      var item = el("div", "citation");
      item.appendChild(el("div", "citation-meta", citationMeta(c)));
      item.appendChild(el("div", "citation-excerpt", "\"" + c.excerpt + "\""));
      box.appendChild(item);
    });
    log.appendChild(box);
  }

  async function ask() {
    var question = input.value.trim();
    if (!question) {
      showError("질문을 입력해 주세요.");
      return;
    }
    clearError();
    send.disabled = true;
    try {
      var res = await fetch("/kb/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: question }),
      });
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
      send.disabled = false;
    }
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    ask();
  });
})();

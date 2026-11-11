(function () {
  "use strict";

  var INVITE_BUTTONS = [
    { role: "manager", label: "팀장 링크" },
    { role: "leaver", label: "퇴사자 링크" },
    { role: "successor", label: "후임 링크" },
  ];
  var ROLE_LABELS = { manager: "팀장", leaver: "퇴사자", successor: "후임" };
  var NO_ALIAS = "(별칭 없음)";
  var MSG_LOGIN = "관리자 링크로 들어와 주세요.";
  var MSG_FORBIDDEN = "이 화면을 볼 권한이 없습니다.";
  var MSG_DISABLED = "인증이 꺼져 있어 관리 기능을 쓸 수 없습니다.";
  var MSG_RETRY = "잠시 후 다시 시도해 주세요. (연결 실패)";
  var MSG_ALIAS = "별칭은 1~30자이고 이메일·전화번호를 넣을 수 없습니다.";

  var errorBox = document.getElementById("admin-error");
  var statusBox = document.getElementById("admin-status");
  var form = document.getElementById("leaver-form");
  var aliasInput = document.getElementById("alias-input");
  var leaversBox = document.getElementById("leavers");

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

  function clearError() {
    errorBox.textContent = "";
    errorBox.classList.add("hidden");
  }

  function setStatus(text) {
    statusBox.textContent = text;
  }

  function block(text) {
    showError(text);
    Array.prototype.forEach.call(document.querySelectorAll("main button, main input"), function (node) {
      node.disabled = true;
    });
  }

  // 401/403/인증 꺼짐은 화면을 막는다. 그 밖의 실패는 { ok: false, status, detail } 로 돌려준다.
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
      if (res.status === 404 && detail === "auth disabled") {
        block(MSG_DISABLED);
      }
      return { ok: false, status: res.status, detail: detail };
    }
    return { ok: true, status: res.status, data: data };
  }

  function failText(r) {
    if (r.status === 0 || r.status === 503) {
      return MSG_RETRY;
    }
    if (r.status === 422) {
      return MSG_ALIAS;
    }
    return "오류가 발생했습니다: " + r.status + (r.detail ? " (" + r.detail + ")" : "");
  }

  async function copyUrl(input) {
    try {
      await navigator.clipboard.writeText(input.value);
      setStatus("링크를 복사했습니다.");
    } catch (e) {
      input.focus();
      input.select();
      setStatus("복사하지 못했습니다. 선택된 링크를 직접 복사하세요.");
    }
  }

  function showInvite(box, invite) {
    var row = box.querySelector('[data-role="' + invite.role + '"]');
    if (!row) {
      row = el("div", "report-form");
      row.setAttribute("data-role", invite.role);
      row.appendChild(el("span", "report-status", ROLE_LABELS[invite.role] || invite.role));
      var input = el("input", "invite-url");
      input.type = "text";
      input.readOnly = true;
      row.appendChild(input);
      var copy = el("button", "secondary invite-copy", "복사");
      copy.type = "button";
      copy.addEventListener("click", function () {
        copyUrl(input);
      });
      row.appendChild(copy);
      box.appendChild(row);
    }
    row.querySelector(".invite-url").value = invite.url;
  }

  async function createInvite(leaverId, role, box) {
    clearError();
    var r = await api("POST", "/admin/leavers/" + encodeURIComponent(leaverId) + "/invites", { role: role });
    if (!r.ok) {
      if (r.status !== 401 && r.status !== 403) {
        showError(failText(r));
      }
      return;
    }
    showInvite(box, r.data);
    setStatus((ROLE_LABELS[role] || role) + " 링크를 만들었습니다. 만료: " + new Date(r.data.expires_at).toLocaleString());
  }

  function renderLeavers(items) {
    leaversBox.textContent = "";
    if (!items.length) {
      leaversBox.appendChild(el("li", "report-list-none", "등록된 퇴사자가 없습니다."));
      return;
    }
    items.forEach(function (item) {
      var li = el("li", "leaver");
      li.setAttribute("data-leaver", item.leaver);
      li.appendChild(el("span", "leaver-alias", item.alias || NO_ALIAS));
      li.appendChild(document.createTextNode(" · "));
      li.appendChild(el("span", "leaver-id", item.leaver));
      var buttons = el("div", "button-row");
      var invites = el("div", "invites");
      INVITE_BUTTONS.forEach(function (b) {
        var btn = el("button", "secondary", b.label);
        btn.type = "button";
        btn.setAttribute("data-role", b.role);
        btn.addEventListener("click", function () {
          createInvite(item.leaver, b.role, invites);
        });
        buttons.appendChild(btn);
      });
      li.appendChild(buttons);
      li.appendChild(invites);
      leaversBox.appendChild(li);
    });
  }

  async function loadLeavers() {
    var r = await api("GET", "/admin/leavers");
    if (!r.ok) {
      if (r.status !== 401 && r.status !== 403 && r.detail !== "auth disabled") {
        showError(failText(r));
      }
      return;
    }
    renderLeavers(Array.isArray(r.data) ? r.data : []);
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    clearError();
    var alias = aliasInput.value.trim();
    var r = await api("POST", "/admin/leavers", { alias: alias ? alias : null });
    if (!r.ok) {
      if (r.status !== 401 && r.status !== 403 && r.detail !== "auth disabled") {
        showError(failText(r));
      }
      return;
    }
    aliasInput.value = "";
    setStatus("퇴사자를 등록했습니다: " + (r.data.alias || NO_ALIAS) + " · " + r.data.leaver);
    await loadLeavers();
  });

  loadLeavers();
})();

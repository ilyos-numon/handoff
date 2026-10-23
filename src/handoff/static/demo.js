// Demo mode helper: role header for side-by-side panes, role bar, and the /demo split page.
(function () {
  "use strict";

  var ROLE_HEADER = "X-Handoff-As";
  var DEMO_ROLES = ["leaver", "successor", "manager"];
  var ROLE_LABELS = { manager: "팀장", leaver: "퇴사자", successor: "후임" };
  var BAR_ORDER = ["manager", "leaver", "successor"];
  var PANES = { manager: "/report?as=manager", leaver: "/?as=leaver", successor: "/chat?as=successor" };
  var BUSY_MESSAGE = "데모 방문자가 많습니다. 잠시 후 다시 시도해 주세요.";
  var FAIL_MESSAGE = "데모를 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.";

  // Synchronous part: a pane opened with ?as=<role> sends that role on every request.
  var asRole = new URLSearchParams(window.location.search).get("as");
  if (DEMO_ROLES.indexOf(asRole) !== -1 && typeof window.fetch === "function") {
    var originalFetch = window.fetch.bind(window);
    window.fetch = function (input, init) {
      var options = init ? Object.assign({}, init) : {};
      var base = options.headers || (input instanceof Request ? input.headers : undefined);
      var headers = new Headers(base);
      headers.set(ROLE_HEADER, asRole);
      options.headers = headers;
      return originalFetch(input, options);
    };
  }

  function isTopWindow() {
    try {
      return window.top === window;
    } catch (e) {
      return false;
    }
  }

  function crossOriginFrame() {
    try {
      // Reading the top location throws when an other-origin page embeds us.
      return window.top !== window && !window.top.location.href;
    } catch (e) {
      return true;
    }
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
  }

  function getStatus() {
    return fetch("/demo/status", { credentials: "same-origin" }).then(function (res) {
      if (!res.ok) throw new Error("status " + res.status);
      return res.json();
    });
  }

  function formatExpires(iso) {
    if (!iso) return "샌드박스 없음";
    var when = new Date(iso);
    if (isNaN(when.getTime())) return "샌드박스 없음";
    var hh = String(when.getHours()).padStart(2, "0");
    var mm = String(when.getMinutes()).padStart(2, "0");
    return "만료 " + (when.getMonth() + 1) + "/" + when.getDate() + " " + hh + ":" + mm;
  }

  function button(id, label, onClick) {
    var b = document.createElement("button");
    b.type = "button";
    b.id = id;
    b.className = "secondary";
    b.textContent = label;
    b.addEventListener("click", onClick);
    return b;
  }

  function switchRole(role) {
    postJson("/demo/role", { role: role })
      .then(function (res) {
        if (!res.ok) throw new Error("role " + res.status);
        return res.json();
      })
      .then(function (data) {
        window.location.href = data.home;
      })
      .catch(function () {
        window.location.href = "/demo";
      });
  }

  function resetSandbox() {
    postJson("/demo/reset")
      .then(function (res) {
        if (!res.ok) throw new Error("reset " + res.status);
        window.location.reload();
      })
      .catch(function () {
        window.location.href = "/demo";
      });
  }

  function renderBar(status) {
    var old = document.getElementById("demo-bar");
    if (old) old.remove();
    var bar = document.createElement("nav");
    bar.id = "demo-bar";
    bar.className = "demo-bar";
    var title = document.createElement("strong");
    title.textContent = "데모";
    bar.appendChild(title);
    BAR_ORDER.forEach(function (role) {
      var b = button("demo-role-" + role, ROLE_LABELS[role] + " 화면", function () {
        switchRole(role);
      });
      if (status.role === role && window.location.pathname !== "/demo") b.className = "primary";
      bar.appendChild(b);
    });
    var split = document.createElement("a");
    split.id = "demo-split";
    split.href = "/demo";
    split.textContent = "나란히 보기";
    bar.appendChild(split);
    var expires = document.createElement("span");
    expires.id = "demo-expires";
    expires.textContent = formatExpires(status.expires_at);
    bar.appendChild(expires);
    bar.appendChild(button("demo-reset", "처음부터 다시", resetSandbox));
    document.body.insertBefore(bar, document.body.firstChild);
  }

  function showSplitError(message) {
    var box = document.getElementById("demo-error");
    if (!box) return;
    box.textContent = message;
    box.classList.remove("hidden");
    var retry = document.getElementById("demo-start");
    if (retry) retry.classList.remove("hidden");
  }

  function fillPanes() {
    Object.keys(PANES).forEach(function (role) {
      var frame = document.getElementById("demo-pane-" + role);
      if (frame) frame.src = PANES[role];
    });
  }

  function startSandbox() {
    return postJson("/demo/start").then(function (res) {
      if (res.status === 503) throw new Error("busy");
      if (!res.ok) throw new Error("start " + res.status);
      return res.json();
    });
  }

  function openSplit() {
    var box = document.getElementById("demo-error");
    if (box) box.classList.add("hidden");
    var retry = document.getElementById("demo-start");
    if (retry) retry.classList.add("hidden");
    return getStatus()
      .then(function (status) {
        if (status.leaver) return status;
        return startSandbox().then(getStatus);
      })
      .then(function (status) {
        renderBar(status);
        fillPanes();
      })
      .catch(function (err) {
        showSplitError(err && err.message === "busy" ? BUSY_MESSAGE : FAIL_MESSAGE);
      });
  }

  function initSplitPage() {
    if (crossOriginFrame()) {
      var link = document.getElementById("demo-open-new");
      if (link) {
        link.href = window.location.href;
        link.classList.remove("hidden");
      }
    }
    var retry = document.getElementById("demo-start");
    if (retry) retry.addEventListener("click", openSplit);
    openSplit();
  }

  function initScreen() {
    if (!isTopWindow()) return;
    getStatus()
      .then(function (status) {
        if (status && status.demo === true) renderBar(status);
      })
      .catch(function () {
        /* demo off or unreachable: draw nothing */
      });
  }

  function init() {
    if (document.getElementById("demo-split-page")) initSplitPage();
    else initScreen();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

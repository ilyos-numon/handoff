import { useLocation } from "react-router-dom";
import { AS_ROLE, Api, IN_FRAME } from "../lib/api";
import { useApp } from "../lib/app";
import { dateTime } from "../lib/format";
import { ROLE_LABEL } from "../lib/labels";
import type { DemoRole } from "../lib/types";

const ORDER: DemoRole[] = ["manager", "leaver", "successor"];

/** 데모 서버에서만, 최상위 창에서만 그린다(05 C). */
export function DemoBar() {
  const { demo } = useApp();
  const { pathname } = useLocation();
  if (!demo?.demo || IN_FRAME || pathname === "/intro") return null;
  const current = AS_ROLE ?? demo.role;

  const go = async (role: DemoRole) => {
    try { const r = await Api.demoRole(role); window.location.href = r.home; }
    catch { window.location.href = "/demo"; }
  };
  const reset = async () => {
    try { await Api.demoReset(); window.location.reload(); }
    catch { window.location.href = "/demo"; }
  };

  return (
    <nav className="demo-bar" aria-label="데모">
      <div className="demo-bar-inner">
        <span className="badge warm">데모</span>
        <div className="seg" role="group" aria-label="역할 바꾸기">
          {ORDER.map((r) => (
            <button key={r} type="button" className="seg-btn" aria-pressed={pathname !== "/demo" && current === r} onClick={() => go(r)}>
              {ROLE_LABEL[r]}
            </button>
          ))}
          <a className="seg-btn" href="/demo" aria-current={pathname === "/demo" ? "page" : undefined}>나란히 보기</a>
        </div>
        <span className="grow" />
        <span className="xs muted num">{demo.expires_at ? `만료 ${dateTime(demo.expires_at)}` : "샌드박스 없음"}</span>
        <button type="button" className="btn ghost sm" onClick={reset} disabled={!demo.leaver}>처음부터 다시</button>
      </div>
    </nav>
  );
}

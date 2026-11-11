import type { ReactNode } from "react";
import { Link, Navigate } from "react-router-dom";
import { useApp } from "../lib/app";
import { ROLE_HOME, ROLE_LABEL } from "../lib/labels";
import type { Role } from "../lib/types";
import { Shell } from "./Shell";
import { Skeleton } from "./ui";

/**
 * 01 §3.5 시작 순서를 그대로 옮긴 관문.
 * auth:false → ?leaver= (없으면 default) / role:null → 안내 / 역할 다름 → 안내 / 맞으면 me.leaver.
 */
export function RoleGate({ role, title, narrow, children }:
  { role: Role; title: string; narrow?: boolean; children: (leaver: string) => ReactNode }) {
  const { ready, me, demo, error } = useApp();

  if (!ready) return <Shell title={title} narrow={narrow}><div className="panel"><Skeleton /></div></Shell>;

  if (!me) {
    return (
      <Shell title={title} narrow={narrow}>
        <Blocked heading="서버에 연결할 수 없습니다" body={error ? "잠시 후 다시 시도해 주세요." : ""}>
          <button className="btn" type="button" onClick={() => window.location.reload()}>다시 시도</button>
        </Blocked>
      </Shell>
    );
  }

  if (!me.auth) {
    const leaver = new URLSearchParams(window.location.search).get("leaver") || "default";
    return <Shell role={role} title={title} narrow={narrow}>{children(leaver)}</Shell>;
  }

  if (!me.role) {
    // 로그인 전 첫 방문은 소개 페이지로 보낸다(데모 iframe 은 제외).
    if (!demo?.demo) return <Navigate to="/intro" replace />;
    return (
      <Shell title={title} narrow={narrow}>
        <Blocked heading="데모 공간이 없습니다" body="데모를 시작하면 이 화면을 바로 써 볼 수 있습니다.">
          <a className="btn primary" href="/demo">데모 시작</a>
        </Blocked>
      </Shell>
    );
  }

  if (me.role !== role) {
    return (
      <Shell role={me.role} title={title} narrow={narrow}>
        <Blocked heading={`이 화면은 ${ROLE_LABEL[role]} 전용입니다`}
          body={`지금은 ${ROLE_LABEL[me.role]}(으)로 들어와 있습니다.`}>
          <Link className="btn" to={ROLE_HOME[me.role]}>{ROLE_LABEL[me.role]} 화면으로</Link>
        </Blocked>
      </Shell>
    );
  }

  return <Shell role={role} title={title} narrow={narrow}>{children(me.leaver!)}</Shell>;
}

export function Blocked({ heading, body, children }: { heading: string; body?: string; children?: ReactNode }) {
  return (
    <div className="blocked fade-in">
      <h1>{heading}</h1>
      {body && <p className="ink2">{body}</p>}
      {children && <div className="row" style={{ marginTop: 16 }}>{children}</div>}
    </div>
  );
}

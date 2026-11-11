import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Api, IN_FRAME } from "../lib/api";
import { useApp } from "../lib/app";
import { ROLE_LABEL } from "../lib/labels";
import type { Role } from "../lib/types";
import { DemoBar } from "./DemoBar";
import { Logo } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";

export function Shell({ role, title, right, narrow, children }:
  { role?: Role; title?: string; right?: ReactNode; narrow?: boolean; children: ReactNode }) {
  const { me, demo } = useApp();
  const canLogout = me?.auth && me.role && !demo?.demo && !IN_FRAME;
  const logout = async () => { try { await Api.logout(); } finally { window.location.href = "/intro"; } };
  return (
    <>
      <DemoBar />
      <header className="shell-top">
        <div className="shell-top-inner">
          <Link to="/intro" className="brand" aria-label="Handoff 소개"><Logo />Handoff</Link>
          {title && <span className="shell-title">{title}</span>}
          <span className="grow" />
          {right}
          {role && !IN_FRAME && <Link className="btn ghost sm shell-help" to={`/intro/${role}`}><span className="shell-help-icon" aria-hidden="true">?</span><span className="shell-help-text">이 화면 안내</span></Link>}
          {role && (
            <span className="role-chip" style={{ ["--role-c" as string]: `var(--role-${role})` }}>
              <i />{ROLE_LABEL[role]}
            </span>
          )}
          {!IN_FRAME && <ThemeToggle />}
          {canLogout && <button type="button" className="btn ghost sm" onClick={logout}>나가기</button>}
        </div>
      </header>
      <main className={`shell-main${narrow ? " narrow" : ""}`}>{children}</main>
    </>
  );
}

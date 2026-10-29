import { Link, useParams } from "react-router-dom";
import { Logo } from "../components/Logo";
import { ThemeToggle } from "../components/ThemeToggle";
import { useApp } from "../lib/app";
import { INTRO_ROLES, ROLE_INTROS, type IntroRole } from "../lib/intro-roles";
import { ROLE_HOME, ROLE_LABEL } from "../lib/labels";
import { NotFound } from "./NotFound";
import "../styles/intro.css";

const isIntroRole = (v: string | undefined): v is IntroRole => !!v && (INTRO_ROLES as string[]).includes(v);

// 역할 소개 (08-help-and-intro.md §6). 애니메이션 없이 정적으로 그린다.
export default function RoleIntroPage() {
  const { role } = useParams();
  const { me } = useApp();
  if (!isIntroRole(role)) return <NotFound />;
  const c = ROLE_INTROS[role];
  const label = ROLE_LABEL[role];
  const mine = me?.role === role;
  const others = INTRO_ROLES.filter((r) => r !== role);

  return (
    <div className="intro role-intro" style={{ ["--rc" as string]: `var(--role-${role})` }}>
      <header className="intro-nav">
        <Link to="/intro" className="brand"><Logo />Handoff</Link>
        <nav className="row">
          {me?.role && !mine && <Link className="btn sm" to={ROLE_HOME[me.role]}>{ROLE_LABEL[me.role]} 화면으로</Link>}
          <ThemeToggle />
        </nav>
      </header>

      <section className="intro-sec role-intro-head">
        <span className="role-tag"><i />{label}</span>
        <h1 className="role-intro-title">{label} 안내</h1>
        <p className="ink2 sec-lead">{c.summary}</p>
        <div className="hero-cta">
          {mine ? <Link className="btn primary lg" to={ROLE_HOME[role]}>내 화면으로</Link>
            : <span className="note small">초대 링크로 들어오면 이 화면이 열립니다.</span>}
        </div>
      </section>

      <section className="intro-sec">
        <h2>언제 쓰나요</h2>
        <ul className="role-intro-list">
          {c.when.map((w) => <li key={w}>{w}</li>)}
        </ul>
      </section>

      <section className="intro-sec">
        <h2>화면에서 하는 일</h2>
        <ol className="role-intro-steps">
          {c.steps.map((s, i) => (
            <li key={s.title}>
              <span className="p-no">0{i + 1}</span>
              <p><strong>{s.title}</strong> — {s.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="intro-sec">
        <h2>볼 수 있는 것과 볼 수 없는 것</h2>
        <div className="role-intro-see">
          <div>
            <h3>볼 수 있는 것</h3>
            <ul className="role-intro-list">{c.canSee.map((s) => <li key={s}>{s}</li>)}</ul>
          </div>
          <div>
            <h3>볼 수 없는 것</h3>
            <ul className="role-intro-list">{c.cannotSee.map((s) => <li key={s}>{s}</li>)}</ul>
          </div>
        </div>
      </section>

      <section className="intro-sec">
        <h2>자주 묻는 질문</h2>
        <div className="role-intro-faq">
          {c.faq.map((f) => (
            <details key={f.q}>
              <summary>{f.q}</summary>
              <p>{f.a}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="intro-sec role-intro-more">
        <h3>다른 역할 안내</h3>
        <nav className="row role-intro-links">
          {others.map((r) => <Link key={r} className="btn ghost sm" to={`/intro/${r}`}>{ROLE_LABEL[r]} 안내</Link>)}
          <Link className="btn sm" to="/intro">전체 소개로</Link>
        </nav>
      </section>
    </div>
  );
}

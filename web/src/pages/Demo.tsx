import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { DemoBar } from "../components/DemoBar";
import { Logo } from "../components/Logo";
import { Blocked } from "../components/RoleGate";
import { Button } from "../components/ui";
import { Api, ApiError, IN_FRAME, userMessage } from "../lib/api";
import { useApp } from "../lib/app";
import { ROLE_LABEL } from "../lib/labels";
import type { DemoRole } from "../lib/types";

const PANES: { role: DemoRole; src: string }[] = [
  { role: "manager", src: "/report?as=manager" },
  { role: "leaver", src: "/?as=leaver" },
  { role: "successor", src: "/chat?as=successor" },
];

export function DemoPage() {
  const { ready, demo, reload } = useApp();
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [focus, setFocus] = useState<DemoRole>("manager");

  const start = async () => {
    setStarting(true); setError(null);
    try { await Api.demoStart(); await reload(); }
    catch (e) { setError(userMessage(e as ApiError)); }
    finally { setStarting(false); }
  };
  useEffect(() => { if (ready && demo?.demo && !demo.leaver && !starting && !error) void start(); /* eslint-disable-next-line */ }, [ready, demo]);

  if (!ready) return null;
  if (!demo?.demo) {
    return (
      <main className="shell-main narrow">
        <Blocked heading="데모 서버가 아닙니다" body="make demo 로 띄운 서버에서만 열립니다.">
          <Link className="btn" to="/intro">소개 보기</Link>
        </Blocked>
      </main>
    );
  }
  return (
    <div className="demo-page">
      <DemoBar />
      <header className="demo-head">
        <Link to="/intro" className="brand"><Logo />Handoff</Link>
        <p className="small ink2">
          팀장이 자료와 우선 질문을 준비했고, 퇴사자의 첫 세션 답변이 이미 들어 있습니다.
          후임 창에서 질문해 보고, 퇴사자 창에서 ‘세션 시작’을 눌러 두 번째 세션을 이어 가 보세요. 이 공간은 하루 뒤 지워집니다.
        </p>
        <p className="small demo-warn">연습용 공간입니다. 실제 이름·연락처·회사 정보는 넣지 마세요. 서버가 쉬거나 하루가 지나면 지워집니다.</p>
      </header>
      {error ? (
        <Blocked heading="데모를 열지 못했습니다" body={error}>
          <Button variant="primary" busy={starting} onClick={start}>다시 시도</Button>
        </Blocked>
      ) : !demo.leaver ? (
        <div className="demo-wait"><span className="spinner" /> 나만의 데모 공간을 준비하는 중</div>
      ) : (
        <>
          <div className="demo-switch seg-light" role="group">
            {PANES.map((p) => <button key={p.role} type="button" aria-pressed={focus === p.role} onClick={() => setFocus(p.role)}>{ROLE_LABEL[p.role]}</button>)}
          </div>
          <section className="demo-panes">
            {PANES.map((p) => (
              <figure key={p.role} className={`demo-pane${focus === p.role ? " focus" : ""}`}>
                <iframe src={p.src} title={`${ROLE_LABEL[p.role]} 화면`} />
              </figure>
            ))}
          </section>
          {IN_FRAME && <a className="btn sm" href="/demo" target="_blank" rel="noopener">새 창에서 열기</a>}
        </>
      )}
    </div>
  );
}

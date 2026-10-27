import { useEffect, useState } from "react";
import { CardRefs } from "../../components/CardRefs";
import { Button, Empty, ErrorNote, Thinking } from "../../components/ui";
import { Api, ApiError } from "../../lib/api";
import { keys, store } from "../../lib/store";
import type { DrillFeedback, DrillScenario, KnowledgeCard } from "../../lib/types";

/** 점수·등급 없이 혼자 점검하는 화면(03 §7). 답 원문은 서버에 남지 않는다. */
export function DrillTab({ leaver, cards }: { leaver: string; cards: Record<string, KnowledgeCard> | null }) {
  const [history, setHistory] = useState<{ id: string; title: string }[]>(() => store.json(keys.drills(leaver), []));
  const [drill, setDrill] = useState<DrillScenario | null>(null);
  const [text, setText] = useState("");
  const [fb, setFb] = useState<DrillFeedback | null>(null);
  const [busy, setBusy] = useState<"new" | "answer" | "open" | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [seed, setSeed] = useState("");

  useEffect(() => { store.setJson(keys.drills(leaver), history.slice(0, 20)); }, [history, leaver]);

  const create = async () => {
    setBusy("new"); setError(null); setFb(null); setText("");
    try {
      const d = await Api.newDrill(leaver, seed || undefined);
      setDrill(d);
      setHistory((h) => [{ id: d.id, title: d.title }, ...h.filter((x) => x.id !== d.id)]);
    } catch (e) { setError(e as ApiError); } finally { setBusy(null); }
  };
  const open = async (id: string) => {
    setBusy("open"); setError(null); setFb(null); setText("");
    try { setDrill(await Api.drill(id)); }
    catch (e) {
      if (e instanceof ApiError && e.status === 404) setHistory((h) => h.filter((x) => x.id !== id));
      setError(e as ApiError);
    } finally { setBusy(null); }
  };
  const answer = async () => {
    if (!drill || !text.trim()) return;
    setBusy("answer"); setError(null);
    try { setFb(await Api.drillAnswer(drill.id, text)); } catch (e) { setError(e as ApiError); } finally { setBusy(null); }
  };

  const seedable = cards ? Object.values(cards).filter((c) => ["pitfall", "procedure", "decision"].includes(c.type)) : [];

  return (
    <div className="drill">
      <aside className="stack" style={{ ["--gap" as string]: "12px" }}>
        <div className="panel tight stack" style={{ ["--gap" as string]: "10px" }}>
          <h3>새 상황 받기</h3>
          <select className="select" value={seed} onChange={(e) => setSeed(e.target.value)} aria-label="카드 고르기">
            <option value="">서버가 골라 주기</option>
            {seedable.map((c) => <option key={c.id} value={c.id}>{c.title.slice(0, 40)}</option>)}
          </select>
          <Button variant="primary" busy={busy === "new"} onClick={create}>상황 만들기</Button>
          <p className="xs muted">점수는 없습니다. 놓친 요점만 짚어 드립니다.</p>
        </div>
        {history.length > 0 && (
          <div className="panel tight">
            <h3 style={{ marginBottom: 8 }}>지난 훈련</h3>
            <ul className="list compact">
              {history.map((h) => (
                <li key={h.id}><button type="button" className="link" onClick={() => open(h.id)}>{h.title.replace(/^훈련:\s*/, "")}</button></li>
              ))}
            </ul>
          </div>
        )}
      </aside>
      <section className="stack" style={{ ["--gap" as string]: "14px" }}>
        <ErrorNote error={error} override={error?.is(409, "no cards") ? "훈련에 쓸 카드가 아직 없습니다. 퇴사자 세션이 더 필요합니다." : undefined} />
        {busy === "new" || busy === "open" ? <div className="panel"><Thinking label="상황을 준비하는 중" /></div>
          : !drill ? <Empty>왼쪽에서 상황을 받아 보세요. "이 상황이면 무엇부터 하지?"를 미리 연습하는 곳입니다.</Empty>
          : (
            <div className="panel fade-in stack" style={{ ["--gap" as string]: "14px" }}>
              <span className="badge warm" style={{ alignSelf: "flex-start" }}>지금 당번은 당신입니다</span>
              <h2>{drill.title.replace(/^훈련:\s*/, "")}</h2>
              <p className="ink2">{drill.situation}</p>
              <p><strong>{drill.ask}</strong></p>
              <textarea className="textarea" rows={5} value={text} onChange={(e) => setText(e.target.value)}
                placeholder="확인할 것, 순서, 알릴 사람을 적어 보세요." disabled={busy === "answer"} />
              <div className="row"><Button variant="primary" busy={busy === "answer"} disabled={!text.trim()} onClick={answer}>점검 받기</Button></div>
              {fb && (
                <div className="feedback fade-in">
                  <p className="small">{fb.feedback}</p>
                  {fb.covered_points.length > 0 && (
                    <div><span className="xs muted">다룬 요점</span>
                      <ul className="checks ok">{fb.covered_points.map((p, i) => <li key={i}>{p}</li>)}</ul></div>
                  )}
                  {fb.missed_points.length > 0 && (
                    <div><span className="xs muted">더 살펴볼 요점</span>
                      <ul className="checks more">{fb.missed_points.map((p, i) => <li key={i}>{p}</li>)}</ul></div>
                  )}
                  <CardRefs ids={fb.card_refs} cards={cards} />
                </div>
              )}
            </div>
          )}
      </section>
    </div>
  );
}

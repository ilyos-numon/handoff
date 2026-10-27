import { useState, type FormEvent } from "react";
import { CardRefs } from "../../components/CardRefs";
import { Button, CertaintyBadge, ErrorNote, Thinking } from "../../components/ui";
import { Api, ApiError } from "../../lib/api";
import { useApp } from "../../lib/app";
import { charLen } from "../../lib/format";
import type { FencePoint, FenceResponse, KnowledgeCard } from "../../lib/types";
import { DEMO_FENCE } from "./examples";

export function FenceTab({ leaver }: { leaver: string }) {
  const { demo } = useApp();
  const [subject, setSubject] = useState("");
  const [res, setRes] = useState<FenceResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const go = async (s: string) => {
    s = s.trim();
    if (!s || busy) return;
    setBusy(true); setError(null); setRes(null); setSubject(s);
    try { setRes(await Api.fence(leaver, s)); } catch (e) { setError(e as ApiError); } finally { setBusy(false); }
  };
  const submit = (e: FormEvent) => { e.preventDefault(); void go(subject); };
  const cards = res ? Object.fromEntries(res.cards.map((c) => [c.id, c])) as Record<string, KnowledgeCard> : null;

  return (
    <div className="stack" style={{ ["--gap" as string]: "18px" }}>
      <div className="note">
        이상해 보이는 설정이나 절차를 없애기 전에, 왜 그렇게 됐는지와 바꾸면 무엇이 깨지는지 먼저 확인하세요.
      </div>
      <form className="row" onSubmit={submit}>
        <input className="input grow" value={subject} maxLength={200} onChange={(e) => setSubject(e.target.value)}
          placeholder="예: 이미지 태그 롤백" aria-label="이유가 궁금한 것" disabled={busy} />
        <Button type="submit" variant="primary" busy={busy} disabled={!subject.trim() || charLen(subject) > 200}>이유 찾기</Button>
      </form>
      {demo?.demo && <div className="row">{DEMO_FENCE.map((s) => <button key={s} type="button" className="chip" onClick={() => go(s)}>{s}</button>)}</div>}
      {busy && <Thinking />}
      <ErrorNote error={error} retry={() => go(subject)} />
      {res && (
        <article className="answer fade-in">
          {res.found ? (
            <>
              <div className="row"><CertaintyBadge value={res.certainty} /></div>
              <Points title="이유" points={res.reasons} cards={cards} />
              <Points title="바꾸면 생길 위험" points={res.risks} cards={cards} />
            </>
          ) : (
            <>
              <p>이 결정의 이유는 인수인계 기록에 없습니다.</p>
              <Points title="관련 위험" points={res.risks} cards={cards} />
              {res.queued && <p className="small ink2">퇴사자 질문 목록에 올렸습니다. 다음 세션에서 물어봅니다.</p>}
            </>
          )}
          {res.claims_removed > 0 && <p className="xs muted">근거와 맞지 않는 문장 {res.claims_removed}개를 뺐습니다.</p>}
          {res.invalidated_when.length > 0 && (
            <div className="watch-box">
              <span className="xs muted">이런 일이 생기면 다시 확인하세요</span>
              <ul className="watch">{res.invalidated_when.map((c, i) => <li key={i}>{c.condition}</li>)}</ul>
            </div>
          )}
        </article>
      )}
    </div>
  );
}

function Points({ title, points, cards }: { title: string; points: FencePoint[]; cards: Record<string, KnowledgeCard> | null }) {
  if (!points.length) return null;
  return (
    <div className="points">
      <h3>{title}</h3>
      {points.map((p, i) => (
        <div key={i} className="point"><p>{p.text}</p><CardRefs ids={p.card_ids} cards={cards} /></div>
      ))}
    </div>
  );
}

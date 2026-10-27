import { useEffect, useRef, useState, type FormEvent } from "react";
import { CardRefs } from "../../components/CardRefs";
import { Button, CertaintyBadge, ErrorNote, Thinking } from "../../components/ui";
import { Api, ApiError } from "../../lib/api";
import { useApp } from "../../lib/app";
import { date } from "../../lib/format";
import { AXIS_LABEL } from "../../lib/labels";
import type { AskResponse, KnowledgeCard } from "../../lib/types";
import { DEMO_ASK } from "./examples";

type Item = { q: string; res?: AskResponse; error?: ApiError };

export function AskTab({ leaver, cards, seed, onSeedUsed, onAsked }:
  { leaver: string; cards: Record<string, KnowledgeCard> | null; seed: string | null; onSeedUsed: () => void; onAsked: () => void }) {
  const { demo } = useApp();
  const [items, setItems] = useState<Item[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const ask = async (q: string) => {
    q = q.trim();
    if (!q || busy) return;
    setBusy(true); setText("");
    setItems((prev) => [{ q }, ...prev]);
    try {
      const res = await Api.ask(leaver, q);
      setItems((prev) => prev.map((it, i) => (i === 0 ? { ...it, res } : it)));
      if (res.answer.found) onAsked();
    } catch (e) {
      setItems((prev) => prev.map((it, i) => (i === 0 ? { ...it, error: e as ApiError } : it)));
      setText(q); // 503 등: 입력을 지우지 않는다
    } finally { setBusy(false); inputRef.current?.focus(); }
  };

  useEffect(() => { if (seed) { void ask(seed); onSeedUsed(); } /* eslint-disable-next-line */ }, [seed]);

  const submit = (e: FormEvent) => { e.preventDefault(); void ask(text); };

  return (
    <div className="stack" style={{ ["--gap" as string]: "18px" }}>
      <form className="ask-box" onSubmit={submit}>
        <textarea ref={inputRef} className="textarea" rows={2} value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void ask(text); } }}
          placeholder="예: 정산 배치가 실패하면 누구에게 알리나요?" aria-label="질문" disabled={busy} />
        <div className="row-between">
          <div className="row">
            {demo?.demo && DEMO_ASK.map((s) => (
              <button key={s} type="button" className="chip" onClick={() => ask(s)} disabled={busy}>{s}</button>
            ))}
          </div>
          <Button type="submit" variant="primary" busy={busy} disabled={!text.trim()}>묻기</Button>
        </div>
      </form>

      {items.length === 0 && (
        <p className="small muted">퇴사자가 세션을 마감한 뒤에야 그 답이 검색됩니다. 아직 아무것도 안 나와도 정상입니다.</p>
      )}

      {items.map((it, i) => (
        <article key={items.length - i} className="qa fade-in">
          <p className="qa-q">{it.q}</p>
          {!it.res && !it.error && <Thinking />}
          {it.error && <ErrorNote error={it.error} retry={() => ask(it.q)} />}
          {it.res && <AnswerView res={it.res} cards={cards} />}
        </article>
      ))}
    </div>
  );
}

function AnswerView({ res, cards }: { res: AskResponse; cards: Record<string, KnowledgeCard> | null }) {
  const a = res.answer;
  if (!a.found) {
    return (
      <div className="answer missing">
        <p>{a.answer}</p>
        {res.queued && (
          <p className="small ink2">이 질문은 퇴사자 질문 목록에 올렸습니다. 다음 세션에서 먼저 물어봅니다.</p>
        )}
        {res.route && (
          <div className="route">
            <span className="xs muted">기다리는 동안 먼저 물어볼 곳</span>
            <strong>{res.route.ask_role}</strong>
            <span className="small ink2">{res.route.reason}</span>
            {res.route.basis === "contact_card" && <CardRefs ids={res.route.card_ids} cards={cards} />}
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="answer">
      <div className="row"><CertaintyBadge value={res.certainty} /></div>
      <p className="pre">{a.answer}</p>
      {a.claims_removed > 0 && <p className="xs muted">근거로 확인되지 않은 문장 {a.claims_removed}개를 뺐습니다.</p>}
      {a.citations.length > 0 && (
        <ol className="citations">
          {a.citations.map((c, i) => (
            <li key={i}>
              <span className="xs muted">
                {c.session_number ? `세션 ${c.session_number}` : "세션"} · {AXIS_LABEL[c.axis]}{c.created_at ? ` · ${date(c.created_at)}` : ""}
              </span>
              <p className="small">{c.excerpt}</p>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

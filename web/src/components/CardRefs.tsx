import { useState } from "react";
import { CARD_TYPE_LABEL } from "../lib/labels";
import type { KnowledgeCard } from "../lib/types";
import { CertaintyBadge } from "./ui";

/** 근거 카드 id 칩. 누르면 카드 내용을 펼친다. 목록에 없는 id 는 재추출로 바뀐 것(07 #8). */
export function CardRefs({ ids, cards, showEvidence = true }:
  { ids: string[]; cards: Record<string, KnowledgeCard> | null; showEvidence?: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!ids.length) return null;
  const card = open ? cards?.[open] : undefined;
  return (
    <div className="card-refs">
      <div className="row">
        <span className="xs muted">근거</span>
        {ids.map((id) => {
          const missing = cards !== null && !cards[id];
          return (
            <button key={id} type="button" className={`chip${open === id ? " on" : ""}${missing ? " missing" : ""}`}
              onClick={() => setOpen(open === id ? null : id)} title={missing ? "근거 카드가 갱신됐습니다" : undefined}>
              {id}
            </button>
          );
        })}
      </div>
      {open && (
        <div className="card-pop fade-in">
          {!cards ? <span className="xs muted">카드를 불러오는 중…</span>
            : !card ? <span className="small muted">근거 카드가 갱신됐습니다. 산출물을 다시 만들면 새 근거로 이어집니다.</span>
            : <CardView card={card} showEvidence={showEvidence} />}
        </div>
      )}
    </div>
  );
}

export function CardView({ card, showEvidence = true }: { card: KnowledgeCard; showEvidence?: boolean }) {
  return (
    <div className="stack" style={{ ["--gap" as string]: "8px" }}>
      <div className="row">
        <span className="badge">{CARD_TYPE_LABEL[card.type]}</span>
        <CertaintyBadge value={card.certainty} />
        {card.uncertain_cues.length > 0 && <span className="xs muted">‘{card.uncertain_cues.join("’, ‘")}’라고 답함</span>}
      </div>
      <h3>{card.title}</h3>
      {card.body !== card.title && <p className="small ink2 pre">{card.body}</p>}
      {showEvidence && card.evidence.map((ev, i) => (
        <blockquote key={i} className="quote">
          <span className="xs muted">{ev.session_number ? `세션 ${ev.session_number}` : "세션"}에서 남긴 답</span>
          <p className="small">{ev.excerpt}</p>
        </blockquote>
      ))}
      {card.invalidated_when.length > 0 && (
        <ul className="watch">{card.invalidated_when.map((c, i) => <li key={i}>{c.condition}</li>)}</ul>
      )}
    </div>
  );
}

import { useState } from "react";
import { ago } from "../lib/format";
import type { Artifact, KnowledgeCard } from "../lib/types";
import { CardRefs } from "./CardRefs";

/** cards=null 이면 근거 칩을 펼치지 않는다(팀장은 카드 목록이 403). */
export function ArtifactView({ art, cards, showRefs = true }:
  { art: Artifact; cards: Record<string, KnowledgeCard> | null; showRefs?: boolean }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(art.markdown); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* */ }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([art.markdown], { type: "text/markdown;charset=utf-8" }));
    const a = document.createElement("a"); a.href = url; a.download = `${art.kind}.md`; a.click(); URL.revokeObjectURL(url);
  };
  const stale = cards !== null && art.card_ids.some((id) => !cards[id]) && Object.keys(cards).length > 0;
  return (
    <article className="artifact">
      <div className="row-between" style={{ marginBottom: 16 }}>
        <div>
          <h2>{art.title}</h2>
          <span className="xs muted">{ago(art.generated_at)} 생성 · 절 {art.sections.length}개</span>
        </div>
        <div className="row">
          <button type="button" className="btn sm ghost" onClick={copy}>{copied ? "복사했습니다" : "마크다운 복사"}</button>
          <button type="button" className="btn sm ghost" onClick={download}>내려받기</button>
        </div>
      </div>
      {stale && <p className="note warm small" style={{ marginBottom: 12 }}>근거 카드 일부가 갱신됐습니다. 최신 내용이 필요하면 팀장에게 다시 만들어 달라고 하세요.</p>}
      <ol className="sections">
        {art.sections.map((s, i) => (
          <li key={i}>
            <h3>{s.heading}</h3>
            {s.body.trim() !== s.heading.replace(/^\d+\.\s*/, "").trim() && <p className="pre small ink2">{s.body}</p>}
            {showRefs && <CardRefs ids={s.card_ids} cards={cards} />}
          </li>
        ))}
      </ol>
    </article>
  );
}

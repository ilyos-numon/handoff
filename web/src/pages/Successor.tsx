import { useCallback, useEffect, useState } from "react";
import { RoleGate } from "../components/RoleGate";
import { Tabs } from "../components/ui";
import { Api } from "../lib/api";
import type { KnowledgeCard } from "../lib/types";
import { AskTab } from "./successor/AskTab";
import { FenceTab } from "./successor/FenceTab";
import { HandedTab } from "./successor/HandedTab";
import { DocsTab } from "./successor/DocsTab";
import { CardsTab } from "./successor/CardsTab";
import { DrillTab } from "./successor/DrillTab";

type Tab = "ask" | "fence" | "handed" | "docs" | "cards" | "drill";

export function SuccessorPage() {
  return <RoleGate role="successor" title="기록에 묻기">{(leaver) => <Successor leaver={leaver} />}</RoleGate>;
}

function Successor({ leaver }: { leaver: string }) {
  const [tab, setTab] = useState<Tab>("ask");
  const [askSeed, setAskSeed] = useState<string | null>(null);
  const [cards, setCards] = useState<Record<string, KnowledgeCard> | null>(null);

  // 근거 칩이 카드 내용을 펼칠 수 있게 카드 목록을 한 번 받아 둔다.
  const loadCards = useCallback(async () => {
    try { const list = await Api.cards(leaver); setCards(Object.fromEntries(list.map((c) => [c.id, c]))); }
    catch { setCards({}); }
  }, [leaver]);
  useEffect(() => { void loadCards(); }, [loadCards]);

  return (
    <>
      <div className="page-head">
        <h1>막히면 먼저 기록에 물어보세요</h1>
        <p>퇴사자가 세션에서 남긴 답을 근거와 함께 찾아 드립니다. 기록에 없으면 다음 세션에서 퇴사자에게 대신 묻습니다.</p>
      </div>
      <Tabs value={tab} onChange={setTab} items={[
        { id: "ask", label: "질문하기" },
        { id: "fence", label: "이건 왜 이렇게?" },
        { id: "handed", label: "넘긴 질문" },
        { id: "docs", label: "인수인계 문서" },
        { id: "cards", label: "지식 카드" },
        { id: "drill", label: "장애 훈련" },
      ]} />
      <div hidden={tab !== "ask"}><AskTab leaver={leaver} cards={cards} seed={askSeed} onSeedUsed={() => setAskSeed(null)} onAsked={loadCards} /></div>
      {tab === "fence" && <FenceTab leaver={leaver} />}
      {tab === "handed" && <HandedTab leaver={leaver} />}
      {tab === "docs" && <DocsTab leaver={leaver} cards={cards} />}
      {tab === "cards" && <CardsTab leaver={leaver} />}
      {tab === "drill" && <DrillTab leaver={leaver} cards={cards} />}
    </>
  );
}

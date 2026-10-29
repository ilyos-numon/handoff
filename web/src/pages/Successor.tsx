import { useCallback, useEffect, useState } from "react";
import { HelpTip } from "../components/HelpTip";
import { RoleGate } from "../components/RoleGate";
import { Tabs } from "../components/ui";
import { Api } from "../lib/api";
import type { HelpKey } from "../lib/help";
import type { KnowledgeCard } from "../lib/types";
import { AskTab } from "./successor/AskTab";
import { FenceTab } from "./successor/FenceTab";
import { HandedTab } from "./successor/HandedTab";
import { DocsTab } from "./successor/DocsTab";
import { CardsTab } from "./successor/CardsTab";
import { DrillTab } from "./successor/DrillTab";

type Tab = "ask" | "handed" | "docs" | "cards" | "fence" | "drill";

const TABS: { id: Tab; label: string }[] = [
  { id: "ask", label: "질문하기" },
  { id: "handed", label: "넘긴 질문" },
  { id: "docs", label: "인수인계 문서" },
  { id: "cards", label: "지식 카드" },
  { id: "fence", label: "이건 왜 이렇게?" },
  { id: "drill", label: "장애 훈련" },
];

const TAB_DESC: Record<Tab, string> = {
  ask: "퇴사자가 남긴 기록에서 근거와 함께 답을 찾습니다.",
  handed: "기록에 없어 다음 세션에 대신 묻기로 한 질문을 봅니다.",
  docs: "세션 기록으로 만든 인수인계 문서를 읽습니다.",
  cards: "세션에서 정리된 지식 카드를 주제별로 봅니다.",
  fence: "이상해 보이는 설정이나 절차가 왜 그런지 기록에서 찾아봅니다.",
  drill: "기록을 바탕으로 장애 상황을 연습합니다.",
};

const TAB_HELP: Record<Tab, HelpKey> = {
  ask: "successor.ask", handed: "successor.handed", docs: "successor.docs",
  cards: "successor.cards", fence: "successor.fence", drill: "successor.drill",
};

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
      <Tabs value={tab} onChange={setTab} items={TABS} />
      <div className="tab-desc">{TAB_DESC[tab]} <HelpTip k={TAB_HELP[tab]} /></div>
      <div hidden={tab !== "ask"}><AskTab leaver={leaver} cards={cards} seed={askSeed} onSeedUsed={() => setAskSeed(null)} onAsked={loadCards} /></div>
      {tab === "fence" && <FenceTab leaver={leaver} />}
      {tab === "handed" && <HandedTab leaver={leaver} />}
      {tab === "docs" && <DocsTab leaver={leaver} cards={cards} />}
      {tab === "cards" && <CardsTab leaver={leaver} />}
      {tab === "drill" && <DrillTab leaver={leaver} cards={cards} />}
    </>
  );
}

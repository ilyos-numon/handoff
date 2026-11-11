import { useCallback, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { HelpTip } from "../components/HelpTip";
import { RoleGate } from "../components/RoleGate";
import { Tabs } from "../components/ui";
import { Api } from "../lib/api";
import { useLoad, useOnFocus } from "../lib/hooks";
import { CoveragePanel } from "./manager/CoveragePanel";
import { GapsPanel } from "./manager/GapsPanel";
import { QueuePanel } from "./manager/QueuePanel";
import { SourcesPanel } from "./manager/SourcesPanel";
import { ArtifactsPanel } from "./manager/ArtifactsPanel";
import { RiskPanel } from "./manager/RiskPanel";
import { PriorityForm } from "./manager/PriorityForm";
import { MyQuestionsPanel } from "./manager/MyQuestionsPanel";

type MgrTab = "status" | "ask" | "docs";
const MGR_TABS: { id: MgrTab; label: string }[] = [
  { id: "status", label: "빈 곳 보기" }, { id: "ask", label: "질문 남기기" }, { id: "docs", label: "자료와 문서" },
];
const TAB_DESC: Record<MgrTab, string> = {
  status: "어느 업무가 아직 기록되지 않았는지 봅니다.",
  ask: "꼭 남겨야 할 질문을 넣고, 내가 남긴 질문의 답을 확인합니다.",
  docs: "업무 자료를 넣어 질문을 만들고, 기록으로 문서를 만듭니다.",
};

export function ManagerPage() {
  return <RoleGate role="manager" title="인수인계 현황">{(leaver) => <Manager leaver={leaver} />}</RoleGate>;
}

/** 팀장은 '무엇이 비어 있는지'만 본다. 다른 사람의 답변 원문·요약·카드 목록 화면은 만들지 않고,
 *  팀장 본인이 남긴 우선 질문의 답만 열어 볼 수 있다(04 원칙). */
function Manager({ leaver }: { leaver: string }) {
  const coverage = useLoad(() => Api.coverage(leaver), [leaver]);
  const queue = useLoad(() => Api.queue(leaver), [leaver]);
  const gaps = useLoad(() => Api.gaps(leaver), [leaver]);
  const [tick, setTick] = useState(0);

  const refreshAll = useCallback(() => {
    void coverage.reload(); void queue.reload(); void gaps.reload(); setTick((t) => t + 1);
  }, [coverage.reload, queue.reload, gaps.reload]); // eslint-disable-line react-hooks/exhaustive-deps
  useOnFocus(refreshAll);

  // 탭은 ?tab= 과 맞춘다. 다른 쿼리(leaver, as 등)는 그대로 둔다.
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab: MgrTab = MGR_TABS.some((t) => t.id === raw) ? (raw as MgrTab) : "status";
  const setTab = (t: MgrTab) => setParams((p) => { const n = new URLSearchParams(p); n.set("tab", t); return n; }, { replace: true });
  // 세 탭 모두 마운트해 두고 고르지 않은 탭만 숨긴다(데이터 로딩·새로 고침은 그대로).
  const pane = (id: MgrTab) => ({ role: "tabpanel", "aria-label": MGR_TABS.find((t) => t.id === id)!.label, hidden: tab !== id, className: "mgr-pane" });

  return (
    <>
      <div className="page-head row-between">
        <div>
          <div className="page-title"><h1>무엇이 아직 안 넘어왔나</h1><HelpTip k="manager.guide" /></div>
          <p>다른 사람의 답변 내용은 보이지 않습니다. 내가 남긴 질문의 답만 열어 볼 수 있습니다.</p>
        </div>
        <button type="button" className="btn ghost sm" onClick={refreshAll}>새로 고침</button>
      </div>
      <Tabs value={tab} onChange={setTab} items={MGR_TABS} />
      <p className="tab-desc">{TAB_DESC[tab]}</p>
      <div {...pane("status")}>
        <CoveragePanel load={coverage} leaver={leaver} onQueued={refreshAll} />
        <GapsPanel load={gaps} leaver={leaver} onChanged={refreshAll} />
      </div>
      <div {...pane("ask")}>
        <PriorityForm leaver={leaver} onQueued={refreshAll} />
        <MyQuestionsPanel load={queue} leaver={leaver} />
        <QueuePanel load={queue} />
      </div>
      <div {...pane("docs")}>
        <SourcesPanel leaver={leaver} onChanged={refreshAll} />
        <ArtifactsPanel leaver={leaver} tick={tick} />
        <RiskPanel leaver={leaver} />
      </div>
    </>
  );
}

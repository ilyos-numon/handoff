import { useCallback, useState } from "react";
import { RoleGate } from "../components/RoleGate";
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

  return (
    <>
      <div className="page-head row-between">
        <div>
          <h1>무엇이 아직 안 넘어왔나</h1>
          <p>다른 사람의 답변 내용은 보이지 않습니다. 내가 남긴 질문의 답만 열어 볼 수 있습니다.</p>
        </div>
        <button type="button" className="btn ghost sm" onClick={refreshAll}>새로 고침</button>
      </div>
      <div className="mgr">
        <div className="mgr-main">
          <CoveragePanel load={coverage} leaver={leaver} onQueued={refreshAll} />
          <GapsPanel load={gaps} leaver={leaver} onChanged={refreshAll} />
          <ArtifactsPanel leaver={leaver} tick={tick} />
          <RiskPanel leaver={leaver} />
        </div>
        <aside className="mgr-side">
          <PriorityForm leaver={leaver} onQueued={refreshAll} />
          <MyQuestionsPanel load={queue} leaver={leaver} />
          <QueuePanel load={queue} />
          <SourcesPanel leaver={leaver} onChanged={refreshAll} />
        </aside>
      </div>
    </>
  );
}

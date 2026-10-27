import { useState } from "react";
import { Empty, ErrorNote, Panel, Skeleton } from "../../components/ui";
import { Api } from "../../lib/api";
import { useLoad } from "../../lib/hooks";
import { CARD_TYPE_LABEL, EVENT_LABEL, EVENTS } from "../../lib/labels";
import type { ChangeEvent } from "../../lib/types";

/** at-risk 는 팀장에게도 열려 있지만 답변 원문이 섞여 온다 → atRiskForManager 가 버린다(07 #1).
 * 인증 켠 서버는 2026-10-27 부터 빼고 보낸다. 인증 끔 대비로 남긴다. */
export function RiskPanel({ leaver }: { leaver: string }) {
  const [event, setEvent] = useState<ChangeEvent>("system_replaced");
  const { data, loading, error, reload } = useLoad(() => Api.atRiskForManager(leaver, event), [leaver, event]);
  return (
    <Panel title="조직이 바뀌면 틀려질 지식" desc="예정된 변화를 고르면 다시 확인해야 할 기록을 보여 줍니다."
      action={
        <select className="select w-auto" value={event} onChange={(e) => setEvent(e.target.value as ChangeEvent)} aria-label="변화">
          {EVENTS.map((ev) => <option key={ev} value={ev}>{EVENT_LABEL[ev]}</option>)}
        </select>
      }>
      <ErrorNote error={error} retry={reload} />
      {loading && !data ? <Skeleton /> : !data || data.cards.length === 0 ? <Empty>해당하는 기록이 없습니다.</Empty> : (
        <ul className="list">
          {data.cards.map(({ card, conditions }) => (
            <li key={card.id}>
              <div className="row"><span className="badge">{CARD_TYPE_LABEL[card.type]}</span><span className="small">{card.title}</span></div>
              <p className="xs" style={{ color: "var(--warm)", marginTop: 4 }}>{conditions[0]?.condition}</p>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

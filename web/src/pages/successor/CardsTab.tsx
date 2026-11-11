import { useState } from "react";
import { CardView } from "../../components/CardRefs";
import { Empty, ErrorNote, Skeleton } from "../../components/ui";
import { Api } from "../../lib/api";
import { useLoad } from "../../lib/hooks";
import { CARD_TYPE_LABEL, EVENT_LABEL, EVENTS } from "../../lib/labels";
import type { CardType, Certainty, ChangeEvent } from "../../lib/types";

const TYPES = Object.keys(CARD_TYPE_LABEL) as CardType[];

export function CardsTab({ leaver }: { leaver: string }) {
  const [mode, setMode] = useState<"all" | "risk">("all");
  return (
    <div className="stack" style={{ ["--gap" as string]: "16px" }}>
      <div className="seg-light" role="group">
        <button type="button" aria-pressed={mode === "all"} onClick={() => setMode("all")}>전체 카드</button>
        <button type="button" aria-pressed={mode === "risk"} onClick={() => setMode("risk")}>변화가 생기면 틀려질 카드</button>
      </div>
      {mode === "all" ? <AllCards leaver={leaver} /> : <RiskCards leaver={leaver} />}
    </div>
  );
}

function AllCards({ leaver }: { leaver: string }) {
  const [type, setType] = useState<CardType | "">("");
  const [cert, setCert] = useState<Certainty | "">("");
  const { data, loading, error, reload } = useLoad(
    () => Api.cards(leaver, { type: type || undefined, certainty: cert || undefined }), [leaver, type, cert]);
  return (
    <>
      <div className="row">
        <select className="select w-auto" value={type} onChange={(e) => setType(e.target.value as CardType | "")} aria-label="종류">
          <option value="">모든 종류</option>
          {TYPES.map((t) => <option key={t} value={t}>{CARD_TYPE_LABEL[t]}</option>)}
        </select>
        <select className="select w-auto" value={cert} onChange={(e) => setCert(e.target.value as Certainty | "")} aria-label="확신도">
          <option value="">확신도 전체</option>
          <option value="certain">확실한 것만</option>
          <option value="uncertain">불확실한 것만</option>
        </select>
        {data && <span className="xs muted">{data.length}장</span>}
      </div>
      <ErrorNote error={error} retry={reload} />
      {loading && !data ? <div className="panel"><Skeleton /></div>
        : data && data.length === 0 ? <Empty>카드가 없습니다. 퇴사자가 세션을 마치면 자동으로 생깁니다.</Empty>
        : <div className="card-grid">{data?.map((c) => <div key={c.id} className="panel tight"><CardView card={c} /></div>)}</div>}
    </>
  );
}

function RiskCards({ leaver }: { leaver: string }) {
  const [event, setEvent] = useState<ChangeEvent>("system_replaced");
  const [target, setTarget] = useState("");
  const [applied, setApplied] = useState("");
  const { data, loading, error, reload } = useLoad(() => Api.atRisk(leaver, event, applied || undefined), [leaver, event, applied]);
  return (
    <>
      <form className="row" onSubmit={(e) => { e.preventDefault(); setApplied(target.trim()); }}>
        <select className="select w-auto" value={event} onChange={(e) => setEvent(e.target.value as ChangeEvent)} aria-label="변화">
          {EVENTS.map((ev) => <option key={ev} value={ev}>{EVENT_LABEL[ev]}</option>)}
        </select>
        <input className="input grow" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="대상으로 좁히기 (예: 정산)" />
        <button type="submit" className="btn">보기</button>
      </form>
      <ErrorNote error={error} retry={reload} />
      {loading && !data ? <div className="panel"><Skeleton /></div>
        : data && data.cards.length === 0 ? <Empty>{data.label}이(가) 생겨도 틀려질 카드는 없습니다.</Empty>
        : <div className="card-grid">{data?.cards.map(({ card, conditions }) => (
            <div key={card.id} className="panel tight">
              <p className="note warm xs" style={{ marginBottom: 10 }}>{conditions[0]?.condition}</p>
              <CardView card={{ ...card, invalidated_when: [] }} />
            </div>
          ))}</div>}
    </>
  );
}

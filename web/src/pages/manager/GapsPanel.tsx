import { useState } from "react";
import { Button, Empty, ErrorNote, Panel, Skeleton } from "../../components/ui";
import { HelpTip } from "../../components/HelpTip";
import { Api } from "../../lib/api";
import { useAction, type useLoad } from "../../lib/hooks";
import { GAP_KIND_LABEL, RISK_LABEL } from "../../lib/labels";
import type { GapReport } from "../../lib/types";

type Load = ReturnType<typeof useLoad<GapReport>>;

export function GapsPanel({ load, leaver, onChanged }: { load: Load; leaver: string; onChanged: () => void }) {
  const { data, loading, error } = load;
  const [msg, setMsg] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const refresh = useAction(async () => {
    const r = await Api.refreshGaps(leaver);
    // "새로 넣은 수" 는 refresh 응답에서만 센다(07 #14).
    setMsg(`빈 구멍 ${r.gaps.length}개를 찾았고, 확인 질문 ${r.queued.length}개를 큐에 새로 넣었습니다.`);
    onChanged();
  });

  return (
    <Panel title="빈 구멍" help="manager.gaps" desc="자료와 답변을 맞대 보고 기록이 얇은 곳을 찾습니다."
      action={<Button size="sm" busy={refresh.busy} onClick={() => refresh.run()}>{refresh.busy ? "찾는 중" : "다시 찾기"}</Button>}>
      <ErrorNote error={refresh.error ?? error} />
      {msg && <p className="note accent small fade-in" style={{ marginBottom: 12 }}>{msg}</p>}
      {loading && !data ? <Skeleton /> : !data || data.gaps.length === 0 ? <Empty>찾은 빈 구멍이 없습니다. ‘다시 찾기’를 눌러 보세요.</Empty> : (
        <ul className="list">
          {/* id 는 refresh 마다 바뀐다 → kind+topic 을 키로(07 #15) */}
          {(all ? data.gaps : data.gaps.slice(0, 6)).map((g) => (
            <li key={`${g.kind}:${g.topic}`} className="gap">
              <div className="row-between">
                <strong>{g.topic}</strong>
                <span className={`risk r-${g.risk}`}>위험도 {RISK_LABEL[g.risk]}</span>
              </div>
              <span className="xs muted">{GAP_KIND_LABEL[g.kind]} · {g.detail}</span>
            </li>
          ))}
        </ul>
      )}
      {data && data.gaps.length > 6 && (
        <button type="button" className="btn ghost sm" onClick={() => setAll(!all)}>{all ? "접기" : `${data.gaps.length - 6}개 더 보기`}</button>
      )}
      {data && data.unknown_areas.length > 0 && (
        <>
          <hr className="divider" />
          <div className="panel-title" style={{ marginBottom: 8 }}><h3>아무도 확실히 모르는 영역</h3><HelpTip k="manager.unknownAreas" /></div>
          <ul className="list">
            {data.unknown_areas.map((u) => (
              <li key={u.topic}><strong className="small">{u.topic}</strong><p className="xs muted">{u.detail}</p></li>
            ))}
          </ul>
        </>
      )}
    </Panel>
  );
}

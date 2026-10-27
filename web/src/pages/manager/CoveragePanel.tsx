import { useState } from "react";
import { Empty, ErrorNote, Panel, Skeleton } from "../../components/ui";
import { Api } from "../../lib/api";
import type { useLoad } from "../../lib/hooks";
import type { CoverageReport, EmptyTopic } from "../../lib/types";

type Load = ReturnType<typeof useLoad<CoverageReport>>;

export function CoveragePanel({ load, leaver, onQueued }: { load: Load; leaver: string; onQueued: () => void }) {
  const { data, loading, error, reload } = load;
  const [all, setAll] = useState(false);
  const [queued, setQueued] = useState<Record<string, boolean>>({});

  if (loading && !data) return <Panel title="진행 상황"><Skeleton lines={4} /></Panel>;
  if (!data) return <Panel title="진행 상황"><ErrorNote error={error} retry={reload} /></Panel>;

  const ratio = (c: { covered: number; total: number }) => (c.total ? c.covered / c.total : 0);
  const lowest = data.axes.reduce((m, a, i) => (ratio(a) < ratio(data.axes[m]) ? i : m), 0);
  const topics = all ? data.empty_topics : data.empty_topics.slice(0, 6);

  const ask = async (t: EmptyTopic) => {
    try { await Api.priority(leaver, t.text, t.axis); setQueued((q) => ({ ...q, [t.question_id]: true })); onQueued(); } catch { /* 공통 오류는 큐 패널에서 드러난다 */ }
  };

  return (
    <Panel title="진행 상황" desc={`세션 ${data.sessions_done}/${data.sessions_planned} 기준 · 전체 ${data.items_total}항목`}>
      <div className="axes">
        {data.axes.map((a, i) => (
          <div key={a.axis} className="axis-row">
            <span className="axis-name">{a.label}</span>
            <div className="bar" role="meter" aria-valuenow={a.covered} aria-valuemin={0} aria-valuemax={a.total} aria-label={a.label}>
              <i style={{ width: `${ratio(a) * 100}%` }} />
            </div>
            <span className="num small">{a.covered}/{a.total}</span>
            <span className="axis-note xs">
              {i === lowest && <span className="badge warm">가장 비어 있음</span>}
              {a.queued_answered > 0 && <span className="muted">추가 질문 {a.queued_answered}개 답함</span>}
            </span>
          </div>
        ))}
      </div>
      <p className="xs muted" style={{ marginTop: 8 }}>퇴사자가 세션을 마감해야 반영됩니다.</p>
      <hr className="divider" />
      <div className="row-between" style={{ marginBottom: 8 }}>
        <h3>비어 있는 주제 <span className="muted num">{data.empty_topics.length}</span></h3>
      </div>
      {data.empty_topics.length === 0 ? <Empty>비어 있는 주제가 없습니다.</Empty> : (
        <ul className="list">
          {topics.map((t) => (
            <li key={t.question_id} className="row-between topic">
              <div className="grow"><span className="xs muted">{t.label}</span><p className="small">{t.text}</p></div>
              <button type="button" className="btn sm ghost" disabled={queued[t.question_id]} onClick={() => ask(t)}>
                {queued[t.question_id] ? "큐에 넣음" : "우선 질문하기"}
              </button>
            </li>
          ))}
        </ul>
      )}
      {data.empty_topics.length > 6 && (
        <button type="button" className="btn ghost sm" onClick={() => setAll(!all)}>{all ? "접기" : `${data.empty_topics.length - 6}개 더 보기`}</button>
      )}
    </Panel>
  );
}

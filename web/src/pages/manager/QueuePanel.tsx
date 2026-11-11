import { useState } from "react";
import { Empty, ErrorNote, Panel, Skeleton } from "../../components/ui";
import type { useLoad } from "../../lib/hooks";
import { AXIS_LABEL, SOURCE_LABEL, SOURCE_PRIORITY, STATUS_LABEL } from "../../lib/labels";
import type { QueueItem, QueueStatus } from "../../lib/types";

type Load = ReturnType<typeof useLoad<QueueItem[]>>;
const FILTERS: (QueueStatus | "all")[] = ["pending", "asked", "answered", "all"];

export function QueuePanel({ load }: { load: Load }) {
  const { data, loading, error, reload } = load;
  const [f, setF] = useState<QueueStatus | "all">("pending");
  const [more, setMore] = useState(false);
  const cap = more ? 200 : 8;
  // 서버는 id 순 → 실제로 나가는 순서(출처 우선순위)로 다시 정렬한다.
  const items = (data ?? [])
    .filter((q) => f === "all" || q.status === f)
    .sort((a, b) => SOURCE_PRIORITY[a.source] - SOURCE_PRIORITY[b.source] || +a.id.slice(1) - +b.id.slice(1));
  const count = (s: QueueStatus) => (data ?? []).filter((q) => q.status === s).length;

  return (
    <Panel title="다음 세션에 물을 질문" help="manager.queue" desc="위에서부터 한 세션에 두어 개씩 먼저 나갑니다.">
      <div className="seg-light" role="group" style={{ marginBottom: 12 }}>
        {FILTERS.map((s) => (
          <button key={s} type="button" aria-pressed={f === s} onClick={() => setF(s)}>
            {s === "all" ? "전체" : STATUS_LABEL[s]}{s !== "all" && data ? ` ${count(s)}` : ""}
          </button>
        ))}
      </div>
      <ErrorNote error={error} retry={reload} />
      {loading && !data ? <Skeleton /> : items.length === 0 ? <Empty>큐에 질문이 없습니다.</Empty> : (
        <ol className="queue">
          {items.slice(0, cap).map((q) => (
            <li key={q.id}>
              <p className="small">{q.text}</p>
              <span className="xs muted">
                <span className={`src s-${q.source}`}>{SOURCE_LABEL[q.source]}</span> · {AXIS_LABEL[q.axis]} · {STATUS_LABEL[q.status]}
              </span>
            </li>
          ))}
        </ol>
      )}
      {items.length > 8 && <button type="button" className="btn ghost sm" onClick={() => setMore(!more)}>{more ? "접기" : `${items.length - 8}개 더 보기`}</button>}
    </Panel>
  );
}

import { useState } from "react";
import { QuestionThreadView } from "../../components/QuestionThread";
import { Empty, ErrorNote, Panel, Skeleton } from "../../components/ui";
import { ago } from "../../lib/format";
import type { useLoad } from "../../lib/hooks";
import { ANSWERED_BADGE, STATUS_LABEL } from "../../lib/labels";
import type { QueueItem } from "../../lib/types";

type Load = ReturnType<typeof useLoad<QueueItem[]>>;

/** 팀장이 직접 남긴 우선 질문(source=manager)만 보여 준다. 다른 출처 답은 열 수 없다. */
export function MyQuestionsPanel({ load, leaver }: { load: Load; leaver: string }) {
  const { data, loading, error, reload } = load;
  const [open, setOpen] = useState<string | null>(null);
  const items = (data ?? [])
    .filter((q) => q.source === "manager")
    .sort((a, b) => +b.id.slice(1) - +a.id.slice(1));

  return (
    <Panel title="내가 남긴 질문" help="manager.myQuestions" desc="질문을 누르면 퇴사자 답을 볼 수 있어요.">
      {open ? (
        <QuestionThreadView leaver={leaver} qid={open} asker="manager"
          onBack={() => { setOpen(null); void reload(); }} />
      ) : (
        <>
          <ErrorNote error={error} retry={reload} />
          {loading && !data && <Skeleton lines={2} />}
          {data && items.length === 0 && <Empty>아직 남긴 질문이 없습니다.</Empty>}
          {items.length > 0 && (
            <ul className="list">
              {items.map((q) => (
                <li key={q.id}>
                  <button type="button" className="link row-between" style={{ width: "100%" }} onClick={() => setOpen(q.id)}>
                    <span className="grow stack" style={{ ["--gap" as string]: "2px" }}>
                      <span className="ink2">{q.text}</span>
                      <span className="xs muted">{ago(q.created_at)}</span>
                    </span>
                    <span className="row">
                      <span className={`status s-${q.status}`}><i />{STATUS_LABEL[q.status]}</span>
                      {q.status === "answered" && <span className="badge accent">{ANSWERED_BADGE}</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Panel>
  );
}

import { useState } from "react";
import { HelpTip } from "../../components/HelpTip";
import { QuestionThreadView } from "../../components/QuestionThread";
import { Button, Empty, ErrorNote, Skeleton } from "../../components/ui";
import { Api } from "../../lib/api";
import { ago } from "../../lib/format";
import { useLoad, useOnFocus } from "../../lib/hooks";
import { ANSWERED_BADGE, STATUS_SUCCESSOR_LABEL } from "../../lib/labels";

export function HandedTab({ leaver }: { leaver: string }) {
  const { data, loading, error, reload } = useLoad(() => Api.successorQuestions(leaver), [leaver]);
  const [open, setOpen] = useState<string | null>(null);
  useOnFocus(reload); // 푸시가 없으니 창에 돌아올 때 다시 읽는다

  if (open) {
    return (
      <div className="panel">
        <QuestionThreadView leaver={leaver} qid={open} asker="successor"
          onBack={() => { setOpen(null); void reload(); }} />
      </div>
    );
  }
  if (loading && !data) return <div className="panel"><Skeleton /></div>;
  return (
    <div className="panel">
      <div className="panel-head">
        <div><div className="panel-title"><h2>퇴사자에게 넘긴 질문</h2><HelpTip k="successor.handed" /></div><p>질문을 누르면 퇴사자 답을 볼 수 있어요.</p></div>
        <Button size="sm" variant="ghost" onClick={reload} busy={loading}>새로 고침</Button>
      </div>
      <ErrorNote error={error} retry={reload} />
      {data && data.length === 0 && <Empty>아직 넘긴 질문이 없습니다.</Empty>}
      {data && data.length > 0 && (
        <ul className="list">
          {[...data].reverse().map((q) => (
            <li key={q.id}>
              <button type="button" className="link row-between" style={{ width: "100%" }} onClick={() => setOpen(q.id)}>
                <span className="grow stack" style={{ ["--gap" as string]: "2px" }}>
                  <span className="ink2">{q.text}</span>
                  <span className="xs muted">{ago(q.created_at)}</span>
                </span>
                <span className="row">
                  <span className={`status s-${q.status}`}><i />{STATUS_SUCCESSOR_LABEL[q.status]}</span>
                  {q.status === "answered" && <span className="badge accent">{ANSWERED_BADGE}</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

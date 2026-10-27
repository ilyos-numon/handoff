import { Button, Empty, ErrorNote, Skeleton } from "../../components/ui";
import { Api } from "../../lib/api";
import { ago } from "../../lib/format";
import { useLoad, useOnFocus } from "../../lib/hooks";
import { STATUS_SUCCESSOR_LABEL } from "../../lib/labels";

export function HandedTab({ leaver, onReask }: { leaver: string; onReask: (q: string) => void }) {
  const { data, loading, error, reload } = useLoad(() => Api.successorQuestions(leaver), [leaver]);
  useOnFocus(reload); // 푸시가 없으니 창에 돌아올 때 다시 읽는다

  if (loading && !data) return <div className="panel"><Skeleton /></div>;
  return (
    <div className="panel">
      <div className="panel-head">
        <div><h2>퇴사자에게 넘긴 질문</h2><p>답이 오면 같은 질문을 다시 해 보세요. 이제 근거와 함께 답이 나옵니다.</p></div>
        <Button size="sm" variant="ghost" onClick={reload} busy={loading}>새로 고침</Button>
      </div>
      <ErrorNote error={error} retry={reload} />
      {data && data.length === 0 && <Empty>아직 넘긴 질문이 없습니다.</Empty>}
      {data && data.length > 0 && (
        <ul className="list">
          {[...data].reverse().map((q) => (
            <li key={q.id} className="row-between">
              <div className="grow">
                <p>{q.text}</p>
                <span className="xs muted">{ago(q.created_at)}</span>
              </div>
              <div className="row">
                <span className={`status s-${q.status}`}><i />{STATUS_SUCCESSOR_LABEL[q.status]}</span>
                {q.status === "answered" && <Button size="sm" onClick={() => onReask(q.text)}>다시 묻기</Button>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

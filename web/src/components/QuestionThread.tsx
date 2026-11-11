import { useCallback } from "react";
import { Api } from "../lib/api";
import { date } from "../lib/format";
import { useLoad, useOnFocus } from "../lib/hooks";
import { THREAD_STATE_NOTE } from "../lib/labels";
import type { ThreadTurn } from "../lib/types";
import { Button, ErrorNote, Skeleton } from "./ui";

const ASKER_LABEL: Record<"successor" | "manager", string> = { successor: "내 질문", manager: "내가 남긴 질문" };

/** 질문한 사람(후임·팀장)이 자기 질문과 퇴사자 답을 대화로 본다. 상태는 서버가 정한다. */
export function QuestionThreadView({ leaver, qid, asker, onBack }:
  { leaver: string; qid: string; asker: "successor" | "manager"; onBack: () => void }) {
  const { data, loading, error, reload } = useLoad(() => Api.questionThread(leaver, qid), [leaver, qid]);
  const refresh = useCallback(() => { void reload(); }, [reload]);
  useOnFocus(refresh);

  return (
    <section role="region" aria-label="질문 대화" className="thread">
      <div className="row">
        <Button size="sm" variant="ghost" onClick={onBack}>목록으로</Button>
      </div>
      {loading && !data && <Skeleton lines={3} />}
      <ErrorNote error={error} retry={refresh} />
      {data && (
        <ol className="thread-turns">
          <li className="bubble asker">
            <span className="bubble-head">{ASKER_LABEL[asker]} · {date(data.item.created_at)}</span>
            <p className="pre">{data.item.text}</p>
          </li>
          {data.turns.map((t) => <AnswerBubble key={`${t.question_id}-${t.created_at}`} turn={t} />)}
          {data.state !== "answered" && (
            <li className="thread-note" role="status">{THREAD_STATE_NOTE[data.state]}</li>
          )}
        </ol>
      )}
    </section>
  );
}

function AnswerBubble({ turn }: { turn: ThreadTurn }) {
  const head = ["퇴사자 답", turn.session_number !== null ? `세션 ${turn.session_number}` : null, date(turn.created_at)]
    .filter(Boolean).join(" · ");
  return (
    <li className="bubble leaver">
      <span className="bubble-head">{head}</span>
      {turn.follow_up && <p className="bubble-follow">이어서 물은 것: {turn.question}</p>}
      <p className="pre">{turn.answer}</p>
    </li>
  );
}

import { useState, type FormEvent } from "react";
import { Button, ErrorNote, Panel } from "../../components/ui";
import { Api } from "../../lib/api";
import { charLen } from "../../lib/format";
import { useAction } from "../../lib/hooks";
import { AXES, AXIS_LABEL } from "../../lib/labels";
import type { Axis } from "../../lib/types";

export function PriorityForm({ leaver, onQueued }: { leaver: string; onQueued: () => void }) {
  const [text, setText] = useState("");
  const [axis, setAxis] = useState<Axis | "">("");
  const [msg, setMsg] = useState<string | null>(null);
  const act = useAction(async () => {
    const r = await Api.priority(leaver, text.trim(), axis || undefined);
    setMsg(r.created ? "우선 질문을 큐 맨 앞에 넣었습니다." : "이미 큐에 있는 질문입니다.");
    setText(""); onQueued();
  });
  const premortem = useAction(async () => {
    const r = await Api.premortem(leaver);
    setMsg(`사전 부검 질문 ${r.length}개가 큐에 있습니다.`); // 새로 넣었는지는 응답으로 알 수 없다(07 #17)
    onQueued();
  });
  const len = charLen(text.trim());
  const submit = (e: FormEvent) => { e.preventDefault(); if (len) void act.run(); };

  return (
    <Panel title="꼭 남겨 달라고 할 질문" help="manager.priority" desc="다음 세션에 가장 먼저 나갑니다.">
      <form className="stack" onSubmit={submit} style={{ ["--gap" as string]: "10px" }}>
        <textarea className="textarea" rows={3} value={text} onChange={(e) => setText(e.target.value)}
          placeholder="예: 정산 배치가 새벽에 실패하면 누가 깨어 있어야 하나요?" aria-label="우선 질문" />
        <div className="row-between">
          <select className="select w-auto" value={axis} onChange={(e) => setAxis(e.target.value as Axis | "")} aria-label="축">
            <option value="">축 자동</option>
            {AXES.map((a) => <option key={a} value={a}>{AXIS_LABEL[a]}</option>)}
          </select>
          <span className={`xs num ${len > 300 ? "" : "muted"}`} style={len > 300 ? { color: "var(--danger)" } : undefined}>{len}/300</span>
        </div>
        <Button type="submit" variant="primary" busy={act.busy} disabled={!len || len > 300}>큐에 넣기</Button>
      </form>
      <hr className="divider" />
      <div className="row-between">
        <span className="small ink2">떠난 뒤 먼저 터질 일 묻기</span>
        <Button size="sm" busy={premortem.busy} onClick={() => premortem.run()}>사전 부검 질문</Button>
      </div>
      <ErrorNote error={act.error ?? premortem.error} />
      {msg && <p className="note accent small fade-in" style={{ marginTop: 10 }}>{msg}</p>}
    </Panel>
  );
}

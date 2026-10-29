import { useState, type FormEvent } from "react";
import { Button, ErrorNote, Panel, Skeleton } from "../../components/ui";
import { Api } from "../../lib/api";
import { useApp } from "../../lib/app";
import { charLen } from "../../lib/format";
import { useAction, useLoad } from "../../lib/hooks";
import { SOURCE_KIND_LABEL, SOURCE_KINDS } from "../../lib/labels";
import type { SourceKind } from "../../lib/types";

export function SourcesPanel({ leaver, onChanged }: { leaver: string; onChanged: () => void }) {
  const { demo } = useApp();
  const sources = useLoad(() => Api.sources(leaver), [leaver]);
  const [form, setForm] = useState(false);
  const [kind, setKind] = useState<SourceKind>("wiki");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const mock = useAction(async () => { const r = await Api.mockSources(leaver); setMsg(`모의 자료 ${r.length}건을 넣었습니다.`); await sources.reload(); });
  const add = useAction(async () => {
    const r = await Api.addSource(leaver, { kind, title: title.trim(), text: text.trim() });
    setMsg(r.replaced ? `저장했습니다. 연락처 등 ${r.replaced}곳을 가렸습니다.` : "저장했습니다.");
    setTitle(""); setText(""); setForm(false); await sources.reload();
  });
  // 다시 부르면 엔티티 언급 수가 누적된다(07 #9) → 버튼 잠금 + 안내.
  const gen = useAction(async () => {
    const r = await Api.generate(leaver);
    setMsg(r.queued.length ? `업무 자료로 질문 ${r.queued.length}개를 큐에 넣었습니다.` : "새로 넣을 질문이 없습니다. 자료가 바뀌면 다시 눌러 주세요.");
    onChanged();
  });

  const list = sources.data ?? [];
  const submit = (e: FormEvent) => { e.preventDefault(); void add.run(); };
  const titleLen = charLen(title.trim()), textLen = charLen(text.trim());

  return (
    <Panel title="업무 자료" help="manager.sources" desc="위키·런북을 넣으면 거기 나온 이름으로 질문을 만듭니다.">
      {sources.loading && !sources.data ? <Skeleton lines={2} /> : (
        <div className="stack" style={{ ["--gap" as string]: "10px" }}>
          <p className="small"><strong className="num">{list.length}</strong>건 저장됨</p>
          {list.length > 0 && (
            <ul className="src-list">{list.slice(-5).reverse().map((s) => (
              <li key={s.id}><span className="badge">{SOURCE_KIND_LABEL[s.kind]}</span><span className="small grow">{s.title}</span></li>
            ))}</ul>
          )}
          {/* 모의 자료는 부를 때마다 9건 추가된다(07 #16) → 자료가 없을 때만 보인다. */}
          {list.length === 0 && <Button busy={mock.busy} onClick={() => mock.run()}>모의 자료 불러오기</Button>}
          {!demo?.demo && !form && <Button variant="ghost" size="sm" onClick={() => setForm(true)}>자료 직접 넣기</Button>}
          {demo?.demo && <p className="xs muted">데모에서는 모의 자료만 쓸 수 있습니다.</p>}
          {form && (
            <form className="stack fade-in" onSubmit={submit} style={{ ["--gap" as string]: "8px" }}>
              <select className="select" value={kind} onChange={(e) => setKind(e.target.value as SourceKind)} aria-label="종류">
                {SOURCE_KINDS.map((k) => <option key={k} value={k}>{SOURCE_KIND_LABEL[k]}</option>)}
              </select>
              <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="제목" maxLength={200} />
              <textarea className="textarea" rows={5} value={text} onChange={(e) => setText(e.target.value)} placeholder="본문 (최대 2만 자)" />
              <span className="hint">이메일·전화번호는 저장 전에 가려집니다. <span className="num">{textLen}/20000</span></span>
              <div className="row">
                <Button type="submit" variant="primary" size="sm" busy={add.busy} disabled={!titleLen || !textLen || textLen > 20000}>저장</Button>
                <Button size="sm" variant="ghost" onClick={() => setForm(false)}>취소</Button>
              </div>
            </form>
          )}
          <hr className="divider" style={{ margin: "6px 0" }} />
          <Button busy={gen.busy} disabled={list.length === 0} onClick={() => gen.run()}>{gen.busy ? "질문 만드는 중" : "자료로 질문 만들기"}</Button>
        </div>
      )}
      <ErrorNote error={mock.error ?? add.error ?? gen.error ?? sources.error} />
      {msg && <p className="note accent small fade-in" style={{ marginTop: 10 }}>{msg}</p>}
    </Panel>
  );
}

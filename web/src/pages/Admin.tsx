import { useState, type FormEvent } from "react";
import { Blocked, RoleGate } from "../components/RoleGate";
import { Button, Empty, ErrorNote, Panel, Skeleton } from "../components/ui";
import { Api } from "../lib/api";
import { charLen, date, dateTime } from "../lib/format";
import { useAction, useLoad } from "../lib/hooks";
import { ROLE_LABEL } from "../lib/labels";
import type { DemoRole, InviteResponse, LeaverRecord } from "../lib/types";

export function AdminPage() {
  return <RoleGate role="admin" title="관리" narrow>{() => <Admin />}</RoleGate>;
}

const INVITE_ROLES: DemoRole[] = ["leaver", "successor", "manager"];
const CONTACT = /[\w.+-]+@[\w-]+\.[\w.-]+|0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}/;

function Admin() {
  const list = useLoad(() => Api.leavers(), []);
  const [alias, setAlias] = useState("");
  const len = charLen(alias.trim());
  const bad = alias && (len > 30 || CONTACT.test(alias));
  const create = useAction(async () => { await Api.createLeaver(alias.trim()); setAlias(""); await list.reload(); });
  const submit = (e: FormEvent) => { e.preventDefault(); if (!bad) void create.run(); };

  // 인증을 끈 서버면 관리 API 가 404 auth disabled.
  if (list.error?.is(404, "auth disabled")) {
    return <Blocked heading="인증이 꺼진 서버입니다" body="관리 기능은 HANDOFF_AUTH=on 서버에서만 쓸 수 있습니다." />;
  }

  return (
    <>
      <div className="page-head">
        <h1>퇴사자 등록과 초대 링크</h1>
        <p>링크는 메일로 나가지 않습니다. 복사해서 직접 전달해 주세요. 링크는 비밀번호와 같습니다.</p>
      </div>
      <Panel title="퇴사자 등록">
        <form className="stack" onSubmit={submit} style={{ ["--gap" as string]: "8px" }}>
          <div className="row">
            <input className="input grow" value={alias} maxLength={30} onChange={(e) => setAlias(e.target.value)}
              placeholder="별칭 (예: 결제팀 백엔드 A)" aria-label="별칭" aria-invalid={!!bad} />
            <Button type="submit" variant="primary" busy={create.busy} disabled={!!bad}>등록</Button>
          </div>
          <span className="hint">실명 대신 팀과 업무로 적어 주세요. 이메일·전화번호는 넣을 수 없습니다. 비워 두어도 됩니다.</span>
          {create.error?.status === 422 && <p className="note error small">별칭은 1~30자이고 이메일·전화번호를 넣을 수 없습니다.</p>}
          {create.error && create.error.status !== 422 && <ErrorNote error={create.error} />}
        </form>
      </Panel>
      <Panel title="등록된 퇴사자" desc={list.data ? `${list.data.length}명` : undefined}>
        <ErrorNote error={list.error} retry={list.reload} />
        {list.loading && !list.data ? <Skeleton /> : list.data?.length === 0 ? <Empty>아직 등록한 퇴사자가 없습니다.</Empty> : (
          <ul className="list">{list.data?.map((l) => <LeaverRow key={l.leaver} rec={l} onMissing={list.reload} />)}</ul>
        )}
      </Panel>
    </>
  );
}

function LeaverRow({ rec, onMissing }: { rec: LeaverRecord; onMissing: () => void }) {
  const [links, setLinks] = useState<Partial<Record<DemoRole, InviteResponse>>>({});
  const [busy, setBusy] = useState<DemoRole | null>(null);
  const [copied, setCopied] = useState<DemoRole | null>(null);
  const make = async (role: DemoRole) => {
    setBusy(role);
    try { const r = await Api.invite(rec.leaver, role); setLinks((l) => ({ ...l, [role]: r })); }
    catch (e) { if ((e as { status?: number }).status === 404) onMissing(); }
    finally { setBusy(null); }
  };
  const copy = async (role: DemoRole, url: string) => {
    try { await navigator.clipboard.writeText(url); setCopied(role); setTimeout(() => setCopied(null), 1500); } catch { /* */ }
  };
  return (
    <li className="stack" style={{ ["--gap" as string]: "10px" }}>
      <div className="row-between">
        <div><strong>{rec.alias ?? "별칭 없음"}</strong> <span className="xs muted" style={{ fontFamily: "var(--mono)" }}>{rec.leaver}</span></div>
        <span className="xs muted">{date(rec.created_at)} 등록</span>
      </div>
      <div className="row">
        {INVITE_ROLES.map((r) => (
          <Button key={r} size="sm" busy={busy === r} onClick={() => make(r)}>{ROLE_LABEL[r]} 링크</Button>
        ))}
      </div>
      {INVITE_ROLES.filter((r) => links[r]).map((r) => (
        <div key={r} className="invite fade-in">
          <span className="role-chip" style={{ ["--role-c" as string]: `var(--role-${r})` }}><i />{ROLE_LABEL[r]}</span>
          <input className="input grow mono" readOnly value={links[r]!.url} onFocus={(e) => e.currentTarget.select()} aria-label={`${ROLE_LABEL[r]} 링크`} />
          <Button size="sm" onClick={() => copy(r, links[r]!.url)}>{copied === r ? "복사함" : "복사"}</Button>
          <span className="xs muted">{dateTime(links[r]!.expires_at)}까지</span>
        </div>
      ))}
    </li>
  );
}

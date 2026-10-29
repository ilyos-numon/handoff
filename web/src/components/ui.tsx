import type { ButtonHTMLAttributes, ReactNode } from "react";
import { userMessage, type ApiError } from "../lib/api";
import { CERTAINTY_LABEL } from "../lib/labels";
import type { Certainty } from "../lib/types";
import type { HelpKey } from "../lib/help";
import { HelpTip } from "./HelpTip";

export function Button({ busy, variant, size, children, ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean; variant?: "primary" | "ghost"; size?: "sm" | "lg" }) {
  return (
    <button type="button" {...rest} className={["btn", variant, size, rest.className].filter(Boolean).join(" ")}
      disabled={rest.disabled || busy} aria-busy={busy || undefined}>
      {busy && <span className="spinner" aria-hidden />}
      {children}
    </button>
  );
}

export function Thinking({ label = "기록을 찾는 중" }: { label?: string }) {
  return <span className="thinking" role="status">{label}<span aria-hidden><i /><i /><i /></span></span>;
}

export function ErrorNote({ error, retry, override }: { error: ApiError | null; retry?: () => void; override?: string }) {
  if (!error) return null;
  return (
    <div className="note error row-between fade-in" role="alert">
      <span>{override ?? userMessage(error)}</span>
      {retry && <Button size="sm" variant="ghost" onClick={retry}>다시 시도</Button>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Panel({ title, desc, action, children, className, help }:
  { title?: ReactNode; desc?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; help?: HelpKey }) {
  return (
    <section className={["panel", className].filter(Boolean).join(" ")}>
      {(title || action) && (
        <div className="panel-head">
          <div>
            {help ? <div className="panel-title"><h2>{title}</h2><HelpTip k={help} /></div> : <h2>{title}</h2>}
            {desc && <p>{desc}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function CertaintyBadge({ value }: { value: Certainty | null }) {
  if (!value) return null;
  return <span className={`badge ${value === "certain" ? "accent" : "warm"}`}>{CERTAINTY_LABEL[value]}</span>;
}

export function Tabs<T extends string>({ value, onChange, items }:
  { value: T; onChange: (v: T) => void; items: { id: T; label: ReactNode }[] }) {
  return (
    <div className="tabs" role="tablist">
      {items.map((it) => (
        <button key={it.id} type="button" role="tab" className="tab" aria-selected={value === it.id} onClick={() => onChange(it.id)}>
          {it.label}
        </button>
      ))}
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="stack" style={{ ["--gap" as string]: "10px" }} aria-hidden>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton" style={{ width: `${90 - i * 18}%` }} />
      ))}
    </div>
  );
}

export function Markdown({ text }: { text: string }) {
  // 산출물 markdown 은 # / ## / - / 문단만 쓴다. 외부 라이브러리 없이 안전하게 그린다.
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => { if (list.length) { blocks.push(<ul key={blocks.length}>{list.map((l, i) => <li key={i}>{l}</li>)}</ul>); list = []; } };
  for (const line of text.split("\n")) {
    if (line.startsWith("- ")) { list.push(line.slice(2)); continue; }
    flush();
    if (line.startsWith("## ")) blocks.push(<h2 key={blocks.length}>{line.slice(3)}</h2>);
    else if (line.startsWith("# ")) blocks.push(<h1 key={blocks.length}>{line.slice(2)}</h1>);
    else if (line.trim()) blocks.push(<p key={blocks.length}>{line}</p>);
  }
  flush();
  return <div className="md">{blocks}</div>;
}

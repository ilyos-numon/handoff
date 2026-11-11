import { useEffect, useId, useRef, useState } from "react";
import { HELP, type HelpKey } from "../lib/help";

const OPEN_EVENT = "handoff:help-open";

// 제목·label·버튼 안에 넣지 말고 그 옆 형제로 둔다 (08-help-and-intro.md §1).
export function HelpTip({ k, className }: { k: HelpKey; className?: string }) {
  const { title, body } = HELP[k];
  const id = useId();
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const onOther = (e: Event) => { if ((e as CustomEvent<string>).detail !== id) setOpen(false); };
    window.addEventListener(OPEN_EVENT, onOther);
    return () => window.removeEventListener(OPEN_EVENT, onOther);
  }, [id]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    const onDown = (e: PointerEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  const toggle = () => {
    if (!open) window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: id }));
    setOpen(!open);
  };
  const close = () => { setOpen(false); buttonRef.current?.focus(); };

  return (
    <span ref={wrapRef} className={["help", className].filter(Boolean).join(" ")}>
      <button ref={buttonRef} type="button" className="help-btn" aria-label={`${title} 설명`}
        aria-expanded={open} aria-controls={id} onClick={toggle}>
        <span className="help-dot" aria-hidden>?</span>
      </button>
      {open && (
        <div id={id} role="note" aria-label={`${title} 도움말`} className="help-pop">
          <strong>{title}</strong>
          <p>{body}</p>
          <button type="button" className="help-close" onClick={close}>닫기</button>
        </div>
      )}
    </span>
  );
}

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { RoleGate } from "../components/RoleGate";
import { Button, ErrorNote, Skeleton, Thinking } from "../components/ui";
import { Api, ApiError, userMessage } from "../lib/api";
import { clock, maskContacts } from "../lib/format";
import { AXES, AXIS_LABEL, SOURCE_LEAVER_LABEL } from "../lib/labels";
import { keys, store } from "../lib/store";
import type { Question } from "../lib/types";

export function LeaverPage() {
  return <RoleGate role="leaver" title="오늘의 15분" narrow>{(leaver) => <LeaverSession leaver={leaver} />}</RoleGate>;
}

type Turn = { who: "q" | "a"; text: string; q?: Question };
type Phase =
  | { t: "loading" }
  | { t: "idle"; summary?: string[]; number?: number; note?: string }
  | { t: "active" }
  | { t: "ending" }
  | { t: "finished"; message: string };

interface Live { sid: string; number: number; planned: number; question: Question | null; today: number; total: number }

function LeaverSession({ leaver }: { leaver: string }) {
  const [phase, setPhase] = useState<Phase>({ t: "loading" });
  const [live, setLive] = useState<Live | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [remaining, setRemaining] = useState(0);
  const deadline = useRef(0);
  const sendingRef = useRef<Promise<unknown> | null>(null);
  const endingRef = useRef(false);
  const draftRef = useRef("");
  draftRef.current = draft;
  const liveRef = useRef<Live | null>(null);
  liveRef.current = live;
  const logEnd = useRef<HTMLDivElement>(null);

  const syncTimer = (sec: number) => { deadline.current = Date.now() + sec * 1000; setRemaining(sec); };

  const persist = (sid: string, t: Turn[]) => store.setJson(keys.transcript(sid), t);
  const pushTurns = (sid: string, add: Turn[]) => setTurns((prev) => { const next = [...prev, ...add]; persist(sid, next); return next; });

  /* ---------- 끝내기: 409 session closed 면 상태를 다시 읽어 요약을 받는다(02 §6) ---------- */
  const end = useCallback(async (note?: string) => {
    const cur = liveRef.current;
    if (!cur || endingRef.current) return;
    endingRef.current = true;
    if (sendingRef.current) await sendingRef.current.catch(() => undefined); // 보내던 답을 먼저 기다린다
    setPhase({ t: "ending" }); setError(null);
    try {
      let summary: string[]; let number = cur.number;
      try {
        const r = await Api.endSession(cur.sid);
        summary = r.summary; number = r.number;
      } catch (e) {
        if (e instanceof ApiError && e.is(409, "session closed")) summary = (await Api.session(cur.sid)).summary;
        else throw e;
      }
      store.del(keys.transcript(cur.sid)); // 답 내용은 개인 정보 → 세션이 끝나면 지운다(02 §13)
      setLive(null); setTurns([]);
      setPhase({ t: "idle", summary, number, note });
    } catch (e) {
      // 503 이면 세션은 열린 채로 남는다 → 시간이 남았을 때만 입력을 다시 연다.
      setError(e as ApiError);
      setPhase({ t: "active" });
    } finally {
      endingRef.current = false;
    }
  }, []);

  /* ---------- 진입: 저장된 세션 복구(02 §7) ---------- */
  useEffect(() => {
    let alive = true;
    (async () => {
      const sid = store.get(keys.session(leaver));
      if (!sid) { setPhase({ t: "idle" }); return; }
      try {
        const s = await Api.session(sid);
        if (!alive) return;
        if (s.status === "ended") { setPhase({ t: "idle", summary: s.summary, number: s.number }); return; }
        const l: Live = { sid, number: s.number, planned: s.planned, question: s.question, today: s.items_today, total: s.items_total };
        setLive(l); liveRef.current = l;
        const saved = store.json<Turn[]>(keys.transcript(sid), []);
        const t = saved.length ? saved : s.question ? [{ who: "q" as const, text: s.question.text, q: s.question }] : [];
        setTurns(t);
        syncTimer(s.remaining_seconds);
        setPhase({ t: "active" });
        if (s.remaining_seconds <= 0 || !s.question) void end();
      } catch (e) {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 404) { store.del(keys.session(leaver)); setPhase({ t: "idle" }); }
        else { setError(e as ApiError); setPhase({ t: "idle" }); }
      }
    })();
    return () => { alive = false; };
  }, [leaver, end]);

  /* ---------- 타이머: 표시용. 0 이 되면 쓰던 답을 보내고 끝낸다(02 §8) ---------- */
  useEffect(() => {
    if (phase.t !== "active") return;
    const id = window.setInterval(() => {
      const left = Math.max(0, Math.ceil((deadline.current - Date.now()) / 1000));
      setRemaining(left);
      if (left === 0) {
        window.clearInterval(id);
        void (async () => {
          if (draftRef.current.trim() && !sendingRef.current) await send(true);
          await end("15분이 지나 오늘 세션을 마감했습니다.");
        })();
      }
    }, 1000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase.t, end]);

  /* ---------- 다른 탭에서 새 세션을 시작하면 알린다(02 §13) ---------- */
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === keys.session(leaver) && liveRef.current && e.newValue !== liveRef.current.sid) {
        setError(new ApiError(409, "session closed"));
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [leaver]);

  useEffect(() => { logEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns.length, sending]);

  /* ---------- 시작 ---------- */
  const start = async () => {
    if (starting) return; // 시작만 눌러도 회차가 줄어든다(07 #5)
    setStarting(true); setError(null);
    try {
      const r = await Api.startSession(leaver);
      store.set(keys.session(leaver), r.session_id);
      const l: Live = { sid: r.session_id, number: r.number, planned: r.planned, question: r.question, today: r.items_today, total: r.items_total };
      setLive(l); liveRef.current = l;
      const first: Turn[] = [{ who: "q", text: r.question.text, q: r.question }];
      setTurns(first); persist(r.session_id, first);
      syncTimer(r.remaining_seconds);
      setPhase({ t: "active" });
    } catch (e) {
      const err = e as ApiError;
      if (err.is(409, "session limit reached") || err.is(409, "question tree exhausted")) setPhase({ t: "finished", message: userMessage(err) });
      else setError(err);
    } finally { setStarting(false); }
  };

  /* ---------- 답 보내기 ---------- */
  const send = async (fromTimer = false) => {
    const cur = liveRef.current;
    const text = draftRef.current.trim();
    if (!cur || !text || sendingRef.current) return;
    setSending(true); setError(null);
    const p = (async () => {
      try {
        const r = await Api.answer(cur.sid, text);
        setDraft("");
        syncTimer(r.remaining_seconds);
        const add: Turn[] = [{ who: "a", text }];
        if (r.next_question) add.push({ who: "q", text: r.next_question.text, q: r.next_question });
        pushTurns(cur.sid, add);
        const l = { ...cur, question: r.next_question, today: r.items_today, total: r.items_total };
        setLive(l); liveRef.current = l;
        if (!r.next_question && !fromTimer) void end("오늘 준비한 질문을 모두 마쳤습니다.");
      } catch (e) {
        const err = e as ApiError;
        if (err.is(409, "session time over")) { void end("시간이 지나 마지막 답은 저장되지 않았습니다."); return; }
        if (err.is(409, "session closed")) { void end("다른 창에서 세션이 끝났습니다."); return; }
        if (err.status === 404) { store.del(keys.session(leaver)); setLive(null); setPhase({ t: "idle" }); return; }
        setError(err);
      }
    })();
    sendingRef.current = p;
    await p;
    sendingRef.current = null;
    setSending(false);
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void send(); }
  };

  /* ---------- 그리기 ---------- */
  if (phase.t === "loading") return <div className="panel"><Skeleton /></div>;

  if (phase.t === "finished") {
    return (
      <div className="leaver-hero fade-in">
        <p className="eyebrow">인수인계 완료</p>
        <h1>{phase.message}</h1>
        <p className="ink2">남겨 주신 답은 후임이 질문할 때 근거로 쓰입니다.</p>
      </div>
    );
  }

  if (phase.t === "idle") {
    return (
      <div className="stack fade-in" style={{ ["--gap" as string]: "20px" }}>
        {phase.summary ? (
          <section className="panel summary">
            <p className="eyebrow">{phase.number ? `세션 ${phase.number} 정리` : "오늘 정리"}</p>
            <h1>오늘은 여기까지입니다.</h1>
            {phase.note && <p className="small muted" style={{ marginTop: 6 }}>{phase.note}</p>}
            <ol className="summary-list">
              {phase.summary.map((s, i) => <li key={i}>{s === "-" ? <span className="muted">—</span> : maskContacts(s)}</li>)}
            </ol>
          </section>
        ) : (
          <div className="leaver-hero">
            <p className="eyebrow">하루 15분</p>
            <h1>질문 몇 개에 답하면<br />오늘 몫은 끝납니다.</h1>
            <p className="ink2">문서를 쓰지 않아도 됩니다. 평소 말하듯 적어 주시면 후임이 물어볼 때 그 답이 근거로 쓰입니다.</p>
          </div>
        )}
        <ErrorNote error={error} />
        <div className="row">
          <Button variant="primary" size="lg" busy={starting} onClick={start}>
            {phase.summary ? "다음 세션 시작" : "세션 시작"}
          </Button>
          {phase.summary && <span className="small muted">내일 이어서 하셔도 됩니다.</span>}
        </div>
      </div>
    );
  }

  const q = live?.question;
  const ending = phase.t === "ending";
  const low = remaining <= 60;
  return (
    <div className="session fade-in">
      <div className="session-meta row-between">
        <div className="row">
          <span className="badge outline num">세션 {live?.number}/{live?.planned}</span>
          <div className="axis-dots" aria-label={q ? `지금 축: ${AXIS_LABEL[q.axis]}` : undefined}>
            {AXES.map((a) => <span key={a} className={q?.axis === a ? "on" : ""} title={AXIS_LABEL[a]}>{AXIS_LABEL[a]}</span>)}
          </div>
        </div>
        <span className={`timer num${low ? " low" : ""}`} aria-live="off">{clock(remaining)} 남음</span>
      </div>
      <div className="progress-line"><i style={{ transform: `scaleX(${Math.min(1, remaining / 900)})` }} /></div>

      <div className="transcript" aria-live="polite">
        {turns.map((t, i) => t.who === "q" ? (
          <div key={i} className={`turn q${i === turns.length - 1 ? " current" : ""}`}>
            {t.q?.kind === "queued" && t.q.source && <span className="badge warm">{SOURCE_LEAVER_LABEL[t.q.source]}</span>}
            {t.q?.kind === "follow_up" && <span className="xs muted">이어서</span>}
            <p className="pre">{t.text}</p>
          </div>
        ) : (
          <div key={i} className="turn a"><p className="pre">{t.text}</p></div>
        ))}
        {sending && <div className="turn q"><Thinking label="다음 질문을 고르는 중" /></div>}
        <div ref={logEnd} style={{ scrollMarginBottom: 220 }} />
      </div>

      <div className="composer">
        <ErrorNote error={error} />
        <textarea className="textarea" rows={4} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey}
          placeholder="생각나는 대로 적어 주세요. 정확하지 않아도 괜찮습니다." disabled={ending || !q} aria-label="답변" />
        <div className="row-between">
          <span className="xs muted num">오늘 {live?.today ?? 0}항목 · 전체 {live?.total ?? 0}항목</span>
          <div className="row">
            <Button variant="ghost" onClick={() => end()} busy={ending} disabled={sending}>
              {ending ? "정리 중" : "오늘은 여기까지"}
            </Button>
            <Button variant="primary" onClick={() => send()} busy={sending} disabled={!draft.trim() || ending || !q}>
              보내기 <span className="kbd">⌘↵</span>
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

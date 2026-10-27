import { useRef } from "react";
import { Link } from "react-router-dom";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { SplitText } from "gsap/SplitText";
import { MotionPathPlugin } from "gsap/MotionPathPlugin";
import { DrawSVGPlugin } from "gsap/DrawSVGPlugin";
import { useGSAP } from "@gsap/react";
import { Logo } from "../components/Logo";
import { useApp } from "../lib/app";
import { ROLE_HOME, ROLE_LABEL } from "../lib/labels";
import "../styles/intro.css";

gsap.registerPlugin(ScrollTrigger, SplitText, MotionPathPlugin, DrawSVGPlugin, useGSAP);

const LOOP = [
  { k: "후임", t: "후임이 묻는다", d: "“배포를 왜 금요일에는 안 하나요?” 문서를 뒤지는 대신 기록에 바로 묻습니다." },
  { k: "큐", t: "기록에 없으면 넘긴다", d: "지어내지 않습니다. 답이 없으면 그 질문을 퇴사자 질문 목록에 올립니다." },
  { k: "퇴사자", t: "다음 15분에 답한다", d: "퇴사자의 다음 세션은 후임이 물어본 질문으로 시작합니다." },
  { k: "기록", t: "근거와 함께 남는다", d: "세션을 마치면 답이 기록이 되고, 같은 질문에 회차와 날짜가 붙은 답이 나옵니다." },
];

const AXES = [
  { label: "담당 시스템", v: 0.8 },
  { label: "반복 업무", v: 0.6 },
  { label: "사고 이력", v: 0.5 },
  { label: "관계자", v: 0.3 },
];

export default function IntroPage() {
  const root = useRef<HTMLDivElement>(null);
  const { me, demo } = useApp();
  const home = me?.role ? ROLE_HOME[me.role] : null;

  useGSAP(() => {
    const mm = gsap.matchMedia();
    mm.add(
      { motion: "(prefers-reduced-motion: no-preference)", wide: "(min-width: 900px)" },
      (ctx) => {
        const { motion, wide } = ctx.conditions as { motion: boolean; wide: boolean };
        if (!motion) {
          gsap.set(".loop-node", { opacity: 1 });
          gsap.set(".baton-dot", { motionPath: { path: ".baton", start: 0.9, end: 0.9 } });
          return;
        }

        // 배턴 끝 점을 곡선 위에 올려 둔다
        gsap.set(".baton-dot", { motionPath: { path: ".baton", start: 0.9, end: 0.9 } });

        /* ---- 히어로: 글자가 한 자씩 올라오고, 선이 그려지고, 질문 카드가 들어온다 ---- */
        const split = SplitText.create(".hero-title", { type: "words,chars", wordsClass: "w", charsClass: "c" });
        const hero = gsap.timeline({ defaults: { ease: "expo.out" } });
        hero
          .from(split.chars, { yPercent: 110, opacity: 0, duration: 1.1, stagger: 0.022 })
          .from(".hero-sub", { y: 16, opacity: 0, duration: 0.9 }, "-=0.7")
          .from(".hero-cta > *", { y: 12, opacity: 0, duration: 0.7, stagger: 0.08 }, "-=0.6")
          .from(".baton", { drawSVG: "0%", duration: 1.8, ease: "power2.inOut" }, 0.2)
          .from(".baton-dot", { scale: 0, transformOrigin: "50% 50%", duration: 0.6, ease: "back.out(3)" }, 1.6)
          .from(".hero-card", { y: 40, opacity: 0, rotate: -2, duration: 1.2 }, 0.5)
          .from(".hero-card .hc-line", { opacity: 0, x: -8, duration: 0.5, stagger: 0.25 }, 1.1);

        // 카드의 남은 시간이 실제처럼 줄어든다
        const t = { s: 680 };
        gsap.to(t, {
          s: 0, duration: 680, ease: "none", delay: 1.4,
          onUpdate: () => {
            const el = root.current?.querySelector(".hc-timer");
            if (el) el.textContent = `${String(Math.floor(t.s / 60)).padStart(2, "0")}:${String(Math.floor(t.s % 60)).padStart(2, "0")} 남음`;
          },
        });

        // 스크롤하면 히어로가 살짝 물러난다
        gsap.to(".hero-inner", {
          yPercent: -12, opacity: 0.25, ease: "none",
          scrollTrigger: { trigger: ".hero", start: "top top", end: "bottom top", scrub: true },
        });

        /* ---- 문제: 큰 문장이 단어 단위로 진해진다 ---- */
        const pSplit = SplitText.create(".problem-text", { type: "words" });
        gsap.fromTo(pSplit.words, { opacity: 0.14 }, {
          opacity: 1, stagger: 0.08, ease: "none",
          scrollTrigger: { trigger: ".problem", start: "top 70%", end: "bottom 60%", scrub: true },
        });
        gsap.from(".problem-facts li", {
          y: 24, opacity: 0, stagger: 0.12, duration: 0.8, ease: "power3.out",
          scrollTrigger: { trigger: ".problem-facts", start: "top 85%" },
        });

        /* ---- 세 사람: 넓은 화면에서는 가로로 고정 스크롤, 좁으면 하나씩 떠오른다 ---- */
        if (wide) {
          const track = root.current!.querySelector<HTMLElement>(".roles-track")!;
          const dist = () => track.scrollWidth - window.innerWidth;
          const h = gsap.to(track, {
            x: () => -dist(), ease: "none",
            scrollTrigger: { trigger: ".roles", start: "top top", end: () => `+=${dist()}`, pin: true, scrub: 0.6, invalidateOnRefresh: true },
          });
          gsap.utils.toArray<HTMLElement>(".role-card").forEach((card) => {
            gsap.from(card.querySelectorAll(".mock > *"), {
              y: 18, opacity: 0, stagger: 0.12, ease: "power2.out",
              scrollTrigger: { trigger: card, containerAnimation: h, start: "left 75%", end: "left 35%", scrub: true },
            });
          });
        } else {
          gsap.utils.toArray<HTMLElement>(".role-card").forEach((card) => {
            gsap.from(card, { y: 40, opacity: 0, duration: 0.9, ease: "power3.out", scrollTrigger: { trigger: card, start: "top 85%" } });
          });
        }
        // 팀장 카드의 막대가 차오른다
        gsap.utils.toArray<HTMLElement>(".mock-bar i").forEach((bar) => {
          gsap.from(bar, { scaleX: 0, transformOrigin: "left", duration: 1.2, ease: "expo.out", scrollTrigger: { trigger: bar, start: "top 90%", horizontal: false } });
        });

        /* ---- 루프: 점이 원을 따라 돌며 네 단계를 차례로 켠다 ---- */
        const nodes = gsap.utils.toArray<HTMLElement>(".loop-node");
        const steps = gsap.utils.toArray<HTMLElement>(".loop-step");
        gsap.set(steps, { opacity: 0, y: 12 });
        gsap.set(steps[0], { opacity: 1, y: 0 });
        const loop = gsap.timeline({
          scrollTrigger: { trigger: ".loop", start: "top top", end: "+=2400", pin: true, scrub: 0.8 },
        });
        loop.from(".loop-ring", { drawSVG: "0%", duration: 1, ease: "none" }, 0);
        loop.to(".loop-traveler", {
          duration: 4, ease: "none",
          motionPath: { path: ".loop-ring", start: 0, end: 1 },
        }, 0);
        nodes.forEach((n, i) => {
          loop.to(n, { opacity: 1, scale: 1, duration: 0.2 }, i);
          if (i > 0) {
            loop.to(steps[i - 1], { opacity: 0, y: -12, duration: 0.25 }, i);
            loop.to(steps[i], { opacity: 1, y: 0, duration: 0.25 }, i + 0.05);
          }
        });
        loop.to(".loop-center", { scale: 1.06, duration: 0.3, yoyo: true, repeat: 1 }, 3.7);

        /* ---- 네 축: 숫자가 올라가며 막대가 찬다 ---- */
        gsap.utils.toArray<HTMLElement>(".axis-big").forEach((row, i) => {
          const bar = row.querySelector(".ab-bar i");
          const num = row.querySelector<HTMLElement>(".ab-num")!;
          const target = Number(num.dataset.v);
          const o = { v: 0 };
          const tl = gsap.timeline({ scrollTrigger: { trigger: row, start: "top 85%" }, delay: i * 0.08 });
          tl.from(row, { opacity: 0, x: -24, duration: 0.6, ease: "power3.out" })
            .from(bar, { scaleX: 0, transformOrigin: "left", duration: 1.2, ease: "expo.out" }, "<0.1")
            .to(o, { v: target, duration: 1.2, ease: "expo.out", onUpdate: () => { num.textContent = `${Math.round(o.v)}/10`; } }, "<");
        });

        /* ---- 원칙: 아래에서 겹쳐 올라온다 ---- */
        gsap.from(".principle", {
          y: 60, opacity: 0, stagger: 0.15, duration: 1, ease: "power4.out",
          scrollTrigger: { trigger: ".principles", start: "top 75%" },
        });

        /* ---- 마지막: 배턴이 다시 그려진다 ---- */
        const end = gsap.timeline({ scrollTrigger: { trigger: ".outro", start: "top 70%" } });
        end.from(".outro-baton", { drawSVG: "0%", duration: 1.4, ease: "power2.inOut" })
          .from(".outro h2", { y: 30, opacity: 0, duration: 0.9, ease: "expo.out" }, 0.2)
          .from(".outro .hero-cta > *", { y: 12, opacity: 0, duration: 0.6, stagger: 0.08 }, 0.5);

        return () => { split.revert(); pSplit.revert(); };
      },
    );
  }, { scope: root });

  return (
    <div ref={root} className="intro">
      <header className="intro-nav">
        <Link to="/intro" className="brand"><Logo />Handoff</Link>
        <nav className="row">
          {demo?.demo && <a className="btn ghost sm" href="/demo">데모</a>}
          {home && me?.role && <Link className="btn sm" to={ROLE_HOME[me.role]}>{ROLE_LABEL[me.role]} 화면으로</Link>}
        </nav>
      </header>

      {/* ---------- 히어로 ---------- */}
      <section className="hero">
        <svg className="hero-art" viewBox="0 0 1200 700" preserveAspectRatio="xMidYMid slice" aria-hidden>
          <path className="baton" d="M-40 640 C 260 640, 420 560, 640 600 S 1000 660, 1240 540" />
          <circle className="baton-dot" cx="0" cy="0" r="8" />
        </svg>
        <div className="hero-inner">
          <div className="hero-copy">
            <p className="eyebrow">퇴사자 인수인계</p>
            <h1 className="hero-title">문서 대신,<br />물어보면 답하는 기록.</h1>
            <p className="hero-sub">퇴사까지 매일 15분, 화면이 묻고 퇴사자가 답합니다. 후임은 그 기록에 질문하고, 팀장은 무엇이 아직 비었는지만 봅니다.</p>
            <div className="hero-cta">
              {demo?.demo ? <a className="btn primary lg" href="/demo">데모 열어 보기</a>
                : home ? <Link className="btn primary lg" to={home}>내 화면으로</Link>
                : <span className="note small">초대 링크를 받으셨다면 그 링크로 들어오시면 됩니다.</span>}
              <a className="btn ghost lg" href="#how">어떻게 돌아가나요</a>
            </div>
          </div>
          <div className="hero-card" aria-hidden>
            <div className="hc-head"><span className="badge outline">세션 5/10</span><span className="hc-timer num">15:00 남음</span></div>
            <div className="hc-axis"><span>담당 시스템</span><span>반복 업무</span><span className="on">사고 이력</span><span>관계자</span></div>
            <p className="hc-line hc-q">결제 서비스에서 배포가 가장 무서운 부분은 어디인가요? 왜요?</p>
            <p className="hc-line hc-a">정산 배치요. 배포 직후 큐가 밀리면 새벽 배치가 타임아웃 나요.</p>
            <p className="hc-line hc-f"><span className="xs muted">이어서</span>그때 어떻게 복구했나요? 지금도 같은 방법이 통하나요?</p>
          </div>
        </div>
        <span className="scroll-cue" aria-hidden />
      </section>

      {/* ---------- 문제 ---------- */}
      <section className="problem">
        <p className="problem-text">
          인수인계 문서는 쓰는 사람이 무엇을 아는지 모르는 채로 쓰입니다. 정작 필요한 건 왜 그렇게 했는지, 어디가 함정인지, 누구에게 물어야 하는지인데 그건 문서에 잘 남지 않습니다.
        </p>
        <ul className="problem-facts">
          <li><strong>40쪽</strong><span>읽어도 머리에 남지 않는 인수인계 문서</span></li>
          <li><strong>2주</strong><span>퇴사자가 문서 한 장에 쓰는 마지막 시간</span></li>
          <li><strong>같은 실수</strong><span>후임이 입사 뒤에 다시 겪는 사고</span></li>
        </ul>
      </section>

      {/* ---------- 세 사람 ---------- */}
      <section className="roles" id="how">
        <div className="roles-track">
          <div className="roles-intro">
            <p className="eyebrow">세 사람, 세 화면</p>
            <h2>각자 할 일만 합니다.</h2>
            <p className="ink2">퇴사자는 답하고, 후임은 묻고, 팀장은 빈 곳을 봅니다. 서로의 화면을 들여다볼 필요가 없습니다.</p>
          </div>

          <article className="role-card" style={{ ["--rc" as string]: "var(--role-leaver)" }}>
            <span className="role-tag"><i />퇴사자</span>
            <h3>하루 15분, 질문에 답하기</h3>
            <p>서너 문장 쓰면 꼬리 질문이 옵니다. ‘그냥’, ‘감으로’ 같은 말이 나오면 그 판단 기준을 다시 묻습니다.</p>
            <div className="mock">
              <div className="mock-row"><span className="badge outline">세션 3/10</span><span className="xs muted num">09:12 남음</span></div>
              <p className="mock-q">매주 손으로 직접 돌리는 작업이 있나요?</p>
              <p className="mock-a">보통은 감으로 로그를 보고 판단해요.</p>
              <p className="mock-q sm">‘감으로’라고 하셨는데, 어떤 신호를 보고 정하나요?</p>
            </div>
          </article>

          <article className="role-card" style={{ ["--rc" as string]: "var(--role-successor)" }}>
            <span className="role-tag"><i />후임</span>
            <h3>막히면 기록에 묻기</h3>
            <p>답 아래에 퇴사자가 남긴 원래 답과 회차가 붙습니다. 퇴사자도 확신하지 못한 내용이면 그렇다고 알려 줍니다.</p>
            <div className="mock">
              <p className="mock-q">배포를 왜 금요일에는 안 하나요?</p>
              <p className="mock-ans">금요일 배포 후 정산 배치가 주말 내내 실패한 적이 있어 금요일 배포를 피합니다.</p>
              <div className="mock-cite"><span className="xs muted">세션 4 · 사고 이력</span><span className="badge accent">근거 확실</span></div>
            </div>
          </article>

          <article className="role-card" style={{ ["--rc" as string]: "var(--role-manager)" }}>
            <span className="role-tag"><i />팀장</span>
            <h3>무엇이 비었는지만 보기</h3>
            <p>답변 내용은 보이지 않습니다. 네 축별로 얼마나 채워졌는지와 비어 있는 주제, 다음 세션에 나갈 질문만 봅니다.</p>
            <div className="mock">
              {AXES.map((a) => (
                <div key={a.label} className="mock-axis"><span className="xs">{a.label}</span><span className="mock-bar"><i style={{ width: `${a.v * 100}%` }} /></span></div>
              ))}
              <p className="xs muted" style={{ marginTop: 6 }}>비어 있는 주제 · 외부 PG사 장애 시 연락 경로</p>
            </div>
          </article>
        </div>
      </section>

      {/* ---------- 루프 ---------- */}
      <section className="loop">
        <div className="loop-inner">
          <div className="loop-copy">
            <p className="eyebrow">질문이 도는 길</p>
            <h2>모르는 건 모른다고 하고,<br />다음 날 대신 물어봅니다.</h2>
            <div className="loop-steps">
              {LOOP.map((s, i) => (
                <div key={s.k} className="loop-step">
                  <span className="xs muted num">0{i + 1}</span>
                  <h3>{s.t}</h3>
                  <p className="ink2">{s.d}</p>
                </div>
              ))}
            </div>
          </div>
          <div className="loop-art">
            <svg viewBox="0 0 520 520" aria-hidden>
              <circle cx="260" cy="260" r="200" className="loop-track" />
              <path className="loop-ring" d="M260 60 A200 200 0 1 1 259.99 60" />
              <circle r="9" className="loop-traveler" cx="0" cy="0" />
            </svg>
            {LOOP.map((s, i) => <span key={s.k} className={`loop-node n${i}`}>{s.k}</span>)}
            <div className="loop-center"><strong>Handoff</strong><span className="xs muted">지식이 쌓이는 곳</span></div>
          </div>
        </div>
      </section>

      {/* ---------- 네 축 ---------- */}
      <section className="axes-sec">
        <div className="axes-head">
          <p className="eyebrow">네 갈래 질문</p>
          <h2>질문은 네 축을 돌며 깊어집니다.</h2>
          <p className="ink2">한 축만 파고들지 않도록 번갈아 묻고, 팀장은 어느 축이 가장 비었는지 한눈에 봅니다.</p>
        </div>
        <div className="axes-list">
          {[["담당 시스템", 8, "배포가 무서운 곳, 손대면 안 되는 설정"], ["반복 업무", 6, "매주·매달 손으로 돌리는 일"], ["사고 이력", 5, "가장 오래 걸린 장애와 복구 방법"], ["관계자", 3, "막히면 먼저 연락할 역할과 경로"]].map(([label, v, d]) => (
            <div key={label as string} className="axis-big">
              <div className="ab-top"><h3>{label}</h3><span className="ab-num num" data-v={v}>{`${v}/10`}</span></div>
              <span className="ab-bar"><i style={{ width: `${(v as number) * 10}%` }} /></span>
              <p className="small muted">{d}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ---------- 원칙 ---------- */}
      <section className="principles">
        <p className="eyebrow">지키는 것</p>
        <div className="principle-grid">
          <div className="principle"><span className="p-no">01</span><h3>사람을 점수 매기지 않습니다</h3><p>깊이 판정은 다음 질문을 고르는 데만 씁니다. 순위도, 채움 비율도 사람에게 보여 주지 않습니다.</p></div>
          <div className="principle"><span className="p-no">02</span><h3>팀장은 답변을 읽지 않습니다</h3><p>팀장 화면에는 비어 있는 주제와 질문 큐만 있습니다. 퇴사자의 원래 답은 보이지 않습니다.</p></div>
          <div className="principle"><span className="p-no">03</span><h3>모르면 지어내지 않습니다</h3><p>기록에 없는 질문에는 없다고 답하고, 그 질문을 퇴사자에게 돌려보냅니다.</p></div>
        </div>
      </section>

      {/* ---------- 끝 ---------- */}
      <section className="outro">
        <svg className="outro-art" viewBox="0 0 800 120" aria-hidden><path className="outro-baton" d="M10 90 C 200 90, 260 20, 400 40 S 640 100, 790 30" /></svg>
        <h2>떠나는 사람의 15분이<br />남는 사람의 첫 주가 됩니다.</h2>
        <div className="hero-cta">
          {demo?.demo ? <a className="btn primary lg" href="/demo">데모 열어 보기</a>
            : home ? <Link className="btn primary lg" to={home}>내 화면으로</Link>
            : <span className="note small">관리자에게 받은 초대 링크로 들어오세요.</span>}
        </div>
        <p className="xs muted foot">Handoff · 메일을 보내지 않습니다. 링크는 담당자가 직접 전달합니다.</p>
      </section>
    </div>
  );
}

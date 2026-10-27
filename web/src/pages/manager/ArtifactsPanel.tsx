import { useEffect, useState } from "react";
import { ArtifactView } from "../../components/ArtifactView";
import { Button, ErrorNote, Panel, Skeleton } from "../../components/ui";
import { Api, ApiError } from "../../lib/api";
import { ARTIFACT_KINDS, ARTIFACT_TITLE } from "../../lib/labels";
import type { ArtifactKind } from "../../lib/types";
import { loadArtifacts, type ArtifactMap } from "../successor/DocsTab";

export function ArtifactsPanel({ leaver, tick }: { leaver: string; tick: number }) {
  const [map, setMap] = useState<ArtifactMap | null>(null);
  const [open, setOpen] = useState<ArtifactKind | null>(null);
  const [busy, setBusy] = useState<ArtifactKind | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => { loadArtifacts(leaver).then(setMap).catch(() => setMap({})); }, [leaver, tick]);

  const make = async (k: ArtifactKind) => {
    setBusy(k); setError(null);
    try { const a = await Api.makeArtifact(leaver, k); setMap((m) => ({ ...m, [k]: a })); setOpen(k); }
    catch (e) { setError(e as ApiError); }
    finally { setBusy(null); }
  };

  return (
    <Panel title="인수인계 산출물" desc="퇴사자 답에서 뽑은 카드로 문서를 만듭니다. 후임은 읽기만 합니다.">
      {!map ? <Skeleton /> : (
        <div className="art-grid">
          {ARTIFACT_KINDS.map((k) => (
            <div key={k} className={`art-tile${open === k ? " on" : ""}`}>
              <button type="button" className="art-title" disabled={!map[k]} onClick={() => setOpen(open === k ? null : k)}>
                {ARTIFACT_TITLE[k]}
                <span className="xs muted">{map[k] ? `절 ${map[k]!.sections.length}개` : "아직 없음"}</span>
              </button>
              <Button size="sm" variant={map[k] ? "ghost" : undefined} busy={busy === k} disabled={busy !== null && busy !== k} onClick={() => make(k)}>
                {map[k] ? "다시 만들기" : "만들기"}
              </Button>
            </div>
          ))}
        </div>
      )}
      <ErrorNote error={error} override={error?.is(409, "no cards") ? "아직 문서를 만들 기록이 없습니다. 퇴사자 세션이 끝난 뒤 다시 해 보세요." : undefined} />
      {open && map?.[open] && <div className="art-open fade-in"><ArtifactView art={map[open]!} cards={null} showRefs={false} /></div>}
    </Panel>
  );
}

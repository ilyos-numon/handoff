import { useEffect, useState } from "react";
import { ArtifactView } from "../../components/ArtifactView";
import { Empty, Skeleton } from "../../components/ui";
import { Api, ApiError } from "../../lib/api";
import { ARTIFACT_KINDS, ARTIFACT_TITLE } from "../../lib/labels";
import type { Artifact, ArtifactKind, KnowledgeCard } from "../../lib/types";

export type ArtifactMap = Partial<Record<ArtifactKind, Artifact | null>>;

export async function loadArtifacts(leaver: string): Promise<ArtifactMap> {
  const pairs = await Promise.all(ARTIFACT_KINDS.map(async (k) => {
    try { return [k, await Api.artifact(leaver, k)] as const; }
    catch (e) { if (e instanceof ApiError && e.status === 404) return [k, null] as const; throw e; }
  }));
  return Object.fromEntries(pairs);
}

export function DocsTab({ leaver, cards }: { leaver: string; cards: Record<string, KnowledgeCard> | null }) {
  const [map, setMap] = useState<ArtifactMap | null>(null);
  const [kind, setKind] = useState<ArtifactKind>("runbook");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    loadArtifacts(leaver).then((m) => {
      setMap(m);
      const first = ARTIFACT_KINDS.find((k) => m[k]);
      if (first) setKind(first);
    }).catch(() => setFailed(true));
  }, [leaver]);

  if (failed) return <Empty>문서를 불러오지 못했습니다.</Empty>;
  if (!map) return <div className="panel"><Skeleton /></div>;
  const art = map[kind];
  return (
    <div className="docs">
      <nav className="docs-nav" aria-label="문서 종류">
        {ARTIFACT_KINDS.map((k) => (
          <button key={k} type="button" className="docs-link" aria-current={k === kind || undefined} onClick={() => setKind(k)}>
            <span>{ARTIFACT_TITLE[k]}</span>
            {!map[k] && <span className="xs muted">아직 없음</span>}
          </button>
        ))}
      </nav>
      <div className="panel docs-body">
        {art ? <ArtifactView art={art} cards={cards} /> : <Empty>팀장이 아직 이 문서를 만들지 않았습니다.</Empty>}
      </div>
    </div>
  );
}

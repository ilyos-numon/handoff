"""Session hooks that connect interview sessions to the knowledge loop."""

from handoff.cards import CardService
from handoff.contracts import QueueItem, is_queue_id, topic_id
from handoff.depth import DepthService
from handoff.kb import KBEntry
from handoff.knowledge_store import KnowledgeStore
from handoff.providers import ProviderError
from handoff.question_tree import FollowUp
from handoff.session import Question
from handoff.store import SessionStore


class KnowledgeHooks:
    """``SessionHooks`` implementation: queue-first questions, depth follow-ups, cards on end."""

    def __init__(
        self,
        store: KnowledgeStore,
        sessions: SessionStore,
        depth: DepthService,
        cards: CardService,
        queue_per_session: int,
    ) -> None:
        self._store = store
        self._sessions = sessions
        self._depth = depth
        self._cards = cards
        self._queue_per_session = queue_per_session

    def next_queued(self, session_id: str, leaver: str, role: str) -> QueueItem | None:
        if self._store.count_asked_in_session(session_id) >= self._queue_per_session:
            return None
        item = self._store.next_pending(leaver, role)
        if item is not None:
            self._store.mark_asked(item.id, session_id)
        return item

    def after_answer(
        self, session_id: str, answered: Question, text: str, candidates: list[FollowUp]
    ) -> Question | None:
        nxt = self._depth.decide_follow_up(session_id, answered, text, candidates)
        if nxt is None and is_queue_id(answered.id):
            self._store.mark_answered(topic_id(answered.id), session_id)
        return nxt

    def on_end(self, session_id: str, leaver: str, role: str, answers: list[KBEntry]) -> None:
        topics = {topic_id(a.question_id) for a in answers}
        for item in self._store.list_queue(leaver, status="asked"):
            if item.asked_session_id == session_id and item.id in topics:
                self._store.mark_answered(item.id, session_id)
        self._store.reset_asked(session_id)
        try:
            self._cards.extract_for_session(session_id)
        except ProviderError:
            pass

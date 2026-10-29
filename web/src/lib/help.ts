// 화면 "?" 도움말 문구 (08-help-and-intro.md §2). 제목은 E2E 가 찾는 이름이라 바꾸지 않는다.
export type HelpKey =
  | "leaver.start" | "leaver.timer" | "leaver.axes" | "leaver.source" | "leaver.followup" | "leaver.close"
  | "successor.ask" | "successor.handed" | "successor.docs" | "successor.cards" | "successor.fence" | "successor.drill"
  | "successor.certainty" | "successor.citation"
  | "manager.guide" | "manager.coverage" | "manager.emptyTopics" | "manager.gaps" | "manager.unknownAreas"
  | "manager.priority" | "manager.myQuestions" | "manager.queue" | "manager.sources" | "manager.artifacts" | "manager.risk"
  | "admin.register" | "admin.list" | "admin.invite";

export interface HelpEntry { title: string; body: string }

export const HELP: Record<HelpKey, HelpEntry> = {
  "leaver.start": {
    title: "세션",
    body: "하루 한 번, 15분 동안 질문에 답하는 시간입니다. 최대 10번까지 하고, 남긴 답은 후임이 찾아 보는 기록이 됩니다.",
  },
  "leaver.timer": {
    title: "남은 시간",
    body: "이번 세션에 남은 시간입니다. 시간이 다 되면 세션이 저절로 끝나고, 그때까지 쓴 답은 저장됩니다.",
  },
  "leaver.axes": {
    title: "네 가지 축",
    body: "담당 시스템, 반복 업무, 사고 이력, 관계자 네 갈래로 나눠 묻습니다. 지금 질문이 어느 갈래인지 표시합니다.",
  },
  "leaver.source": {
    title: "질문 출처",
    body: "이 질문을 누가 왜 넣었는지 보여 줍니다. 팀장이 지정한 질문의 답은 그 팀장도 볼 수 있고, 다른 질문의 답은 팀장에게 보이지 않습니다.",
  },
  "leaver.followup": {
    title: "꼬리 질문",
    body: "방금 답에서 빠진 이유나 절차를 한 번 더 묻는 질문입니다. 질문 하나에 보통 한 번만 붙습니다.",
  },
  "leaver.close": {
    title: "세션 마치기",
    body: "오늘 세션을 여기서 끝냅니다. 답한 내용은 저장되고, 못 다 한 질문은 다음 세션에 다시 나옵니다.",
  },
  "successor.ask": {
    title: "질문하기",
    body: "궁금한 업무를 물으면 퇴사자가 남긴 기록에서 답을 찾아 줍니다. 기록에 없으면 다음 세션에 퇴사자에게 대신 물을 수 있어요.",
  },
  "successor.handed": {
    title: "넘긴 질문",
    body: "기록에 없어 퇴사자에게 넘긴 내 질문입니다. 다음 세션에서 퇴사자가 답하면 여기서 답을 볼 수 있어요.",
  },
  "successor.docs": {
    title: "인수인계 문서",
    body: "퇴사자 기록으로 만든 운영 런북, 30일 온보딩 플랜 같은 문서입니다. 업무를 처음 익힐 때 차례로 읽어 보세요.",
  },
  "successor.cards": {
    title: "지식 카드",
    body: "퇴사자 답에서 절차·함정·결정·연락처·대상을 한 장씩 뽑아 둔 것입니다. 답의 근거를 펼쳐 볼 때도 이 카드가 나옵니다.",
  },
  "successor.fence": {
    title: "이건 왜 이렇게?",
    body: "설정이나 절차를 바꾸거나 없애기 전에, 왜 지금 방식인지와 바꾸면 무엇이 깨지는지 기록에서 찾아 줍니다. 이유가 없으면 퇴사자에게 물어요.",
  },
  "successor.drill": {
    title: "장애 훈련",
    body: "지식 카드로 만든 장애 상황을 혼자 연습합니다. 대응을 적으면 빠진 요점을 알려 주고, 내 답은 저장하지 않으며 팀장도 볼 수 없습니다.",
  },
  "successor.certainty": {
    title: "근거 확실·불확실",
    body: "답의 근거가 기록에 분명히 있으면 확실, 기록이 애매하면 불확실로 표시합니다. 불확실이면 퇴사자에게 한 번 더 확인하세요.",
  },
  "successor.citation": {
    title: "근거",
    body: "답을 만들 때 참고한 퇴사자 기록과 지식 카드입니다. 눌러서 원래 내용을 확인할 수 있어요.",
  },
  "manager.guide": {
    title: "팀장 화면 쓰는 순서",
    body: "왼쪽 탭부터 차례로 씁니다. 빈 곳을 보고, 꼭 필요한 질문을 남기고, 업무 자료를 넣어 인수인계 문서를 만듭니다.",
  },
  "manager.coverage": {
    title: "진행 상황",
    body: "네 가지 축마다 주제가 얼마나 기록됐는지 보여 줍니다. 팀장은 빈 곳만 보고, 다른 사람이 남긴 답 내용은 보이지 않습니다.",
  },
  "manager.emptyTopics": {
    title: "비어 있는 주제",
    body: "아직 한 번도 기록되지 않은 주제입니다. 줄마다 있는 버튼을 누르면 다음 세션에 먼저 묻도록 넣습니다.",
  },
  "manager.gaps": {
    title: "빈 구멍",
    body: "언급만 되고 기록이 없거나, 기록끼리 어긋나거나, 한 곳에만 있는 지식입니다. 찾은 구멍마다 확인 질문이 자동으로 들어갑니다.",
  },
  "manager.unknownAreas": {
    title: "아무도 확실히 모르는 영역",
    body: "퇴사자도 확실하지 않다고 말한 주제입니다. 다른 사람에게 따로 확인하거나 문서로 남겨 두는 것이 좋아요.",
  },
  "manager.priority": {
    title: "꼭 남겨 달라고 할 질문",
    body: "퇴사 전에 반드시 기록해야 할 질문을 직접 넣습니다. 퇴사자의 다음 세션에서 가장 먼저 나옵니다.",
  },
  "manager.myQuestions": {
    title: "내가 남긴 질문",
    body: "내가 직접 넣은 질문과 그 답입니다. 팀장 화면에서는 내가 남긴 질문의 답만 열리고, 다른 질문의 답은 보이지 않습니다.",
  },
  "manager.queue": {
    title: "다음 세션에 물을 질문",
    body: "퇴사자의 다음 세션에서 물을 질문 목록입니다. 팀장·후임·빈 구멍·업무 자료에서 들어온 질문이 상태별로 보입니다.",
  },
  "manager.sources": {
    title: "업무 자료",
    body: "위키, 런북, 정기 작업, 사고 기록 같은 자료를 넣으면 거기서 퇴사자에게 물을 질문을 만듭니다.",
  },
  "manager.artifacts": {
    title: "인수인계 산출물",
    body: "지금까지의 기록으로 런북, 온보딩 플랜, 함정 모음 등 문서 5종을 만듭니다. 기록이 늘면 다시 만들 수 있어요.",
  },
  "manager.risk": {
    title: "조직이 바뀌면 틀려질 지식",
    body: "시스템 교체나 조직 개편 같은 일이 생겼을 때 다시 확인해야 할 기록을 골라 줍니다.",
  },
  "admin.register": {
    title: "퇴사자 등록",
    body: "인수인계를 시작할 퇴사자를 등록합니다. 실명 대신 화면에 보일 짧은 이름만 넣고, 이메일·전화번호는 받지 않습니다.",
  },
  "admin.list": {
    title: "등록된 퇴사자",
    body: "지금까지 등록한 퇴사자 목록입니다. 관리자는 등록과 링크 만들기만 하고, 퇴사자의 기록 내용은 볼 수 없습니다.",
  },
  "admin.invite": {
    title: "초대 링크",
    body: "퇴사자·후임·팀장이 각자 화면에 들어갈 링크입니다. 메일로 보내지 않으니 복사해서 직접 전해 주세요. 72시간이 지나면 다시 만듭니다.",
  },
};

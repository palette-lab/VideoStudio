import { usePluginTranslations, type TranslationResources } from "@palettelab/sdk"

/* App strings in English and Korean. The language comes from Palette OS (usePlatform().language);
   the toggle in the top bar asks the OS to switch it. Missing Korean keys fall back to English. */
export const resources = {
  en: {
    nav: {
      home: "VideoStudio home", create: "Create", history: "History", newChat: "New chat",
      settings: "Settings", account: "Account", theme: "Theme", light: "Light", dark: "Dark",
      language: "Language", userId: "User ID", orgId: "Organization ID", role: "Role", copy: "Copy", copied: "Copied",
      toggleTheme: "Switch to {{mode}} mode", toggleLang: "Switch language",
    },
    hero: {
      badge: "AI Video Router", apps: "{{n}} studio apps",
      t1: "Create Studio", t2: "Quality", t3: "Videos", t4: "from One Prompt.",
      lead: "Describe the video and add any images, clips or music. VideoStudio works out what it takes, picks the right studio apps, and asks before anything runs.",
      tryOne: "Try one",
    },
    modes: {
      auto: "Auto", autoHint: "The AI looks closely, picks one app or combines apps when one is not enough, and reviews the plan",
      storyboard: "Storyboard", storyboardHint: "Storyboard & Video only: 20-60 s multi-scene stories and ads",
      flow: "Flow Video", flowHint: "Flow Video only: AI clips and films from 3 to 120 s",
      motion: "Motion Video", motionHint: "Motion Studio only: animated text, charts and graphics",
      motion3d: "3D Motion Video", motion3dHint: "Full 3D motion graphics for any topic: realistic 3D scenes with editable words on top, a new look every video",
      explainer: "Document Explainer", explainerHint: "Upload a PDF, Word, TXT or Markdown file: the AI reads it and explainroo makes a narrated explainer video of it",
      presenter: "Presenter", presenterHint: "Presenter Studio only: a person talking to camera with lip-sync (YouTube explainers, UGC ads, testimonials)",
    },
    stage: { upload: "Reading your request", perception: "Looking at your files", enhancing: "Enhancing your prompt", understanding: "Working out what you want" },
    status: {
      draft: "Draft", analyzing: "Thinking", needs_clarification: "Needs your answer", awaiting_approval: "Ready to approve",
      not_possible: "Not possible", approved: "Starting", executing: "Running", completed: "Finished", failed: "Failed",
      error: "Error", superseded: "Replaced", cancelled: "Cancelled", running: "Running", succeeded: "Done", queued: "Queued",
      planned: "Planned", not_run: "Not run",
    },
    composer: {
      auto: "Auto", describe: "Describe the video", message: "Message",
      placeholder: "e.g. A 20-second launch video for these sneakers, with an exploded view of the sole",
      working: "Working on it…", answer: "Type your answer…",
      change: "Ask for a change, e.g. “make it 9:16” or “add captions”…", followUp: "Ask a follow-up or describe a change…",
      addFiles: "Add files", addFilesAria: "Add images, video or audio", filesAdded: "{{n}} file(s) added",
      length: "Length", lengthHint: "Video length (Auto reads it from your prompt)",
      format: "Format", formatHint: "Format (Auto reads it from your prompt)", mode: "Mode",
      portrait: "Portrait", landscape: "Landscape", square: "Square", tall: "Tall",
      sending: "Sending", uploading: "Uploading {{i}}/{{n}}", generate: "Generate plan", send: "Send",
      remove: "Remove {{name}}",
      dockFoot: "Earlier messages and files stay in this chat. Delete it from History.",
    },
    history: {
      empty: "Your videos appear here.", filesOnly: "Files only", open: "Open: {{name}}", turns: "{{n}} turns",
      stillRunning: "Still running. Stop it and delete?", confirm: "Delete this chat?", deleting: "Deleting…",
      delete: "Delete", cancel: "Cancel", preparing: "Preparing…", download: "Download",
      making: "Making your video…", loadingPreview: "Loading preview",
    },
    chat: {
      loading: "Loading the chat…", dismiss: "Dismiss", analysis: "Analysis", thinking: "Thinking",
      enhanced: "Enhanced prompt", fromWords: "written from your words", assumed: "Assumed",
      usedAs: "used as {{role}}", unclear: "purpose unclear", cancel: "Cancel", replaced: "Replaced by the follow-up below.",
      wrong: "Something went wrong:", tryAgain: "Try again", cancelledNothing: "Cancelled. Nothing more was made.",
      noApp: "No connected app can do this yet.", notInMode: "This can’t be done in this mode.",
      whyRuledOut: "Why each app was ruled out", letAutoPick: "Let Auto pick the apps",
      attachHint: "Attach the file with the clip button below and send.",
      pickOrType: "Pick an option or type your own answer below.", typeBelow: "Type your answer below.",
      defaults: "Use sensible defaults", letAuto: "Let Auto decide", answered: "Answered.",
      changed: "Changed:",
    },
    plan: {
      combine: "I’ll combine", use: "I’ll use", running: "Running", effects: "Effects + motion design", clean: "Clean finish", documentary: "Documentary finish",
      simulatedSuffix: " · simulated", title: "Plan · {{n}} app(s)", confidence: "confidence {{n}}%", worth: "Worth knowing",
      unsure: "Not fully sure this is the best route.", unavailable: "{{a}} is unavailable; {{b}} would be used.",
      accept: "I accept these changes", approve: "Approve and run", simulated: "Simulated", real: "Real",
      noneConnected: "These apps aren’t connected yet.", someConnected: "{{a}} of {{b}} apps aren’t connected yet.",
      allConnected: "Every step runs in a connected app.", orType: "Or type a change below.",
      connected: "Connected", unverified: "Unverified profile", unverifiedHint: "Capabilities are assumed until the app owner confirms them",
      gets: "Gets {{x}} → makes a video", nothing: "nothing", yourRequest: "your request", fromStep: "video from step {{n}}",
      retry: "Retry this step", useApp: "Use {{name}}", acceptPrefix: "accept:",
      recommended: "Recommended", selected: "Selected", artDirection: "Art direction", palette: "Palette",
      lockedPalette: "Locked palette", lockPalette: "Lock this palette (the script stays)", promptFor: "Prompt for {{name}}", copyPrompt: "Copy prompt", sentTo: "Sent to",
    },
    result: {
      cancelled: "Cancelled.", cancelledBody: "Nothing more was generated after you pressed Cancel.",
      ready: "Your video is ready.", sim: "Finished as a simulation.", notFinished: "The run did not finish.",
      playing: "It’s playing on the right.", simBody: "The apps in this plan aren’t connected yet, so no video was produced.",
      failBody: "Open the plan above to retry a step or use a replacement app.",
      loadingVideo: "Loading the video…", downloadVideo: "Download video", scenes: "Scenes", reRender: "Re-render",
      newTake: "Make a new take", whatChange: "What should change? (optional)", scenePrompt: "Scene prompt",
      checkPassed: "AI check passed", checkFail: "AI check:",
    },
    edit: {
      hint: "Click any text on the video to edit it.", typing: "Type to change the text; drag ⠿ to move it.",
      textTools: "Text tools", flat: "Flat", smaller: "Smaller", bigger: "Bigger", color: "Colour", move: "Move",
      startHere: "Start here", endHere: "End here", add: "+ Text", revert: "Undo changes", save: "Save text",
      updating: "Updating the video…", download: "Download:", withText: "Video with text", clean: "Clean video",
      layerFile: "Text layer", newText: "Your text",
    },
    studio: {
      yourVideo: "Your video", making: "Making your video", stopping: "Stopping…", cancel: "Cancel", download: "Download",
      rendering: "Rendering…", starting: "Starting…", presenter: "Your presenter", step: "Step {{a}} of {{b}}",
      couldNotLoad: "The video could not be loaded.", simTitle: "Finished as a simulation", noVideo: "No video was produced",
      notConnected: "The apps in this plan aren’t connected yet.", retryHint: "Use “Retry this step” in the plan on the left.",
      scenes: "Scenes", clipsMade: "{{a}} of {{b}} clips made", imagesMade: "{{a}} of {{b}} images made", made: "{{a}} of {{b}} made",
    },
  },
  ko: {
    nav: {
      home: "VideoStudio 홈", create: "만들기", history: "기록", newChat: "새 채팅",
      settings: "설정", account: "계정", theme: "테마", light: "라이트", dark: "다크",
      language: "언어", userId: "사용자 ID", orgId: "조직 ID", role: "역할", copy: "복사", copied: "복사됨",
      toggleTheme: "{{mode}} 모드로 전환", toggleLang: "언어 전환",
    },
    hero: {
      badge: "AI 비디오 라우터", apps: "스튜디오 앱 {{n}}개",
      t1: "프롬프트 하나로", t2: "스튜디오 품질의", t3: "영상을", t4: "만드세요.",
      lead: "원하는 영상을 설명하고 이미지, 클립, 음악을 추가하세요. VideoStudio가 필요한 작업을 파악하고 알맞은 스튜디오 앱을 골라, 실행 전에 먼저 확인을 받습니다.",
      tryOne: "예시로 시작하기",
    },
    modes: {
      auto: "자동", autoHint: "AI가 꼼꼼히 살펴 앱 하나를 고르거나, 하나로 부족하면 여러 앱을 조합하고 계획을 검토합니다",
      storyboard: "스토리보드", storyboardHint: "Storyboard & Video 전용: 20-60초 멀티 장면 스토리와 광고",
      flow: "플로우 비디오", flowHint: "Flow Video 전용: 3초에서 120초까지의 AI 클립과 영상",
      motion: "모션 비디오", motionHint: "Motion Studio 전용: 애니메이션 텍스트, 차트, 그래픽",
      motion3d: "3D 모션 비디오", motion3dHint: "어떤 주제든 풀 3D 모션 그래픽: 사실적인 3D 장면 위에 편집 가능한 텍스트, 영상마다 새로운 스타일",
      explainer: "문서 설명 영상", explainerHint: "PDF, Word, TXT, Markdown 파일을 올리면 AI가 읽고 explainroo가 내레이션이 있는 설명 영상을 만듭니다",
      presenter: "프레젠터", presenterHint: "Presenter Studio 전용: 립싱크로 카메라를 보고 말하는 인물 (유튜브 설명, UGC 광고, 후기)",
    },
    stage: { upload: "요청을 읽는 중", perception: "파일을 살펴보는 중", enhancing: "프롬프트를 다듬는 중", understanding: "원하는 내용을 파악하는 중" },
    status: {
      draft: "초안", analyzing: "생각 중", needs_clarification: "답변 필요", awaiting_approval: "승인 대기",
      not_possible: "불가능", approved: "시작 중", executing: "실행 중", completed: "완료", failed: "실패",
      error: "오류", superseded: "대체됨", cancelled: "취소됨", running: "실행 중", succeeded: "완료", queued: "대기 중",
      planned: "예정", not_run: "실행 안 됨",
    },
    composer: {
      auto: "자동", describe: "영상 설명", message: "메시지",
      placeholder: "예: 밑창 분해도가 들어간 이 운동화의 20초 출시 영상",
      working: "작업 중…", answer: "답변을 입력하세요…",
      change: "변경을 요청하세요. 예: “9:16으로 바꿔 줘”, “자막 추가”…", followUp: "후속 질문이나 변경 사항을 입력하세요…",
      addFiles: "파일 추가", addFilesAria: "이미지, 영상, 오디오 추가", filesAdded: "파일 {{n}}개 추가됨",
      length: "길이", lengthHint: "영상 길이 (자동은 프롬프트에서 읽습니다)",
      format: "화면비", formatHint: "화면비 (자동은 프롬프트에서 읽습니다)", mode: "모드",
      portrait: "세로", landscape: "가로", square: "정사각형", tall: "세로형",
      sending: "보내는 중", uploading: "업로드 중 {{i}}/{{n}}", generate: "계획 만들기", send: "보내기",
      remove: "{{name}} 제거",
      dockFoot: "이전 메시지와 파일은 이 채팅에 남습니다. 기록에서 삭제할 수 있습니다.",
    },
    history: {
      empty: "만든 영상이 여기에 표시됩니다.", filesOnly: "파일만", open: "열기: {{name}}", turns: "{{n}}턴",
      stillRunning: "아직 실행 중입니다. 중지하고 삭제할까요?", confirm: "이 채팅을 삭제할까요?", deleting: "삭제 중…",
      delete: "삭제", cancel: "취소", preparing: "준비 중…", download: "다운로드",
      making: "영상을 만드는 중…", loadingPreview: "미리보기 불러오는 중",
    },
    chat: {
      loading: "채팅을 불러오는 중…", dismiss: "닫기", analysis: "분석", thinking: "생각 중",
      enhanced: "다듬은 프롬프트", fromWords: "요청 내용으로 작성됨", assumed: "가정한 내용",
      usedAs: "{{role}}(으)로 사용", unclear: "용도 불분명", cancel: "취소", replaced: "아래 후속 요청으로 대체되었습니다.",
      wrong: "문제가 발생했습니다:", tryAgain: "다시 시도", cancelledNothing: "취소되었습니다. 더 이상 생성되지 않았습니다.",
      noApp: "아직 이 작업을 할 수 있는 연결된 앱이 없습니다.", notInMode: "이 모드에서는 할 수 없습니다.",
      whyRuledOut: "각 앱이 제외된 이유", letAutoPick: "자동으로 앱 고르기",
      attachHint: "아래 클립 버튼으로 파일을 첨부해 보내세요.",
      pickOrType: "옵션을 고르거나 아래에 직접 답변을 입력하세요.", typeBelow: "아래에 답변을 입력하세요.",
      defaults: "기본값 사용", letAuto: "자동으로 결정", answered: "답변 완료.",
      changed: "변경됨:",
    },
    plan: {
      combine: "다음을 조합합니다:", use: "다음을 사용합니다:", running: "실행 중", effects: "효과 + 모션 디자인", clean: "깔끔한 마무리", documentary: "다큐멘터리 마무리",
      simulatedSuffix: " · 시뮬레이션", title: "계획 · 앱 {{n}}개", confidence: "신뢰도 {{n}}%", worth: "알아 둘 점",
      unsure: "이 방법이 최선인지 확실하지 않습니다.", unavailable: "{{a}}을(를) 사용할 수 없어 {{b}}을(를) 사용합니다.",
      accept: "이 변경 사항에 동의합니다", approve: "승인하고 실행", simulated: "시뮬레이션", real: "실제",
      noneConnected: "이 앱들은 아직 연결되지 않았습니다.", someConnected: "앱 {{b}}개 중 {{a}}개가 아직 연결되지 않았습니다.",
      allConnected: "모든 단계가 연결된 앱에서 실행됩니다.", orType: "또는 아래에 변경 사항을 입력하세요.",
      connected: "연결됨", unverified: "미검증 프로필", unverifiedHint: "앱 소유자가 확인하기 전까지 기능은 추정치입니다",
      gets: "{{x}} 받음 → 영상 생성", nothing: "없음", yourRequest: "요청", fromStep: "{{n}}단계 영상",
      retry: "이 단계 다시 시도", useApp: "{{name}} 사용", acceptPrefix: "동의:",
      recommended: "추천", selected: "선택됨", artDirection: "아트 디렉션", palette: "팔레트",
      lockedPalette: "고정된 팔레트", lockPalette: "이 팔레트로 고정 (스크립트는 유지)", promptFor: "{{name}}용 프롬프트", copyPrompt: "프롬프트 복사", sentTo: "전송 위치",
    },
    result: {
      cancelled: "취소됨.", cancelledBody: "취소를 누른 뒤에는 더 이상 생성되지 않았습니다.",
      ready: "영상이 준비되었습니다.", sim: "시뮬레이션으로 완료되었습니다.", notFinished: "실행이 완료되지 않았습니다.",
      playing: "오른쪽에서 재생 중입니다.", simBody: "이 계획의 앱들이 아직 연결되지 않아 영상이 만들어지지 않았습니다.",
      failBody: "위의 계획을 열어 단계를 다시 시도하거나 대체 앱을 사용하세요.",
      loadingVideo: "영상을 불러오는 중…", downloadVideo: "영상 다운로드", scenes: "장면", reRender: "다시 렌더링",
      newTake: "새 테이크 만들기", whatChange: "무엇을 바꿀까요? (선택)", scenePrompt: "장면 프롬프트",
      checkPassed: "AI 검사 통과", checkFail: "AI 검사:",
    },
    edit: {
      hint: "영상 위의 텍스트를 클릭해 편집하세요.", typing: "입력해서 텍스트를 바꾸고, ⠿를 드래그해 옮기세요.",
      textTools: "텍스트 도구", flat: "평면", smaller: "작게", bigger: "크게", color: "색상", move: "이동",
      startHere: "여기서 시작", endHere: "여기서 끝", add: "+ 텍스트", revert: "변경 취소", save: "텍스트 저장",
      updating: "영상 업데이트 중…", download: "다운로드:", withText: "텍스트 포함 영상", clean: "텍스트 없는 영상",
      layerFile: "텍스트 레이어", newText: "텍스트 입력",
    },
    studio: {
      yourVideo: "내 영상", making: "영상을 만드는 중", stopping: "중지하는 중…", cancel: "취소", download: "다운로드",
      rendering: "렌더링 중…", starting: "시작하는 중…", presenter: "프레젠터", step: "{{b}}단계 중 {{a}}단계",
      couldNotLoad: "영상을 불러올 수 없습니다.", simTitle: "시뮬레이션으로 완료됨", noVideo: "영상이 만들어지지 않았습니다",
      notConnected: "이 계획의 앱들은 아직 연결되지 않았습니다.", retryHint: "왼쪽 계획에서 “이 단계 다시 시도”를 사용하세요.",
      scenes: "장면", clipsMade: "{{b}}개 중 {{a}}개 클립 완료", imagesMade: "{{b}}개 중 {{a}}개 이미지 완료", made: "{{b}}개 중 {{a}}개 완료",
    },
  },
} satisfies TranslationResources

export type T = (key: string, values?: Record<string, string | number>, defaultValue?: string) => string

/** The app's translator, bound to the current Palette OS language. */
export function useT() {
  return usePluginTranslations(resources)
}

/** Example prompts per language (the SDK dictionary holds strings only, so lists live here). */
const EXAMPLES: Record<string, string[]> = {
  en: [
      "A 45-second cinematic ad for an electric car in 8 scenes, with an exploded view of the battery and the slogan over the final shot. 16:9, music, no voice-over.",
      "Join these clips into one 9:16 video in order, put the uploaded music under it, and add captions.",
      "Turn this product photo into a 6-second clip with a slow camera push-in. 9:16 for Reels.",
      "A 30-second UGC ad: a young woman holds this skincare bottle and tells the camera how it fixed her dry skin. 9:16.",
      "A 60-second YouTube explainer: a friendly host at a desk explains how compound interest works, with simple examples.",
  ],
  ko: [
      "전기차를 위한 45초 시네마틱 광고, 8개 장면. 배터리 분해도와 마지막 장면에 슬로건. 16:9, 음악 포함, 내레이션 없음.",
      "이 클립들을 순서대로 하나의 9:16 영상으로 합치고, 업로드한 음악을 깔고 자막을 넣어 주세요.",
      "이 제품 사진을 천천히 카메라가 다가가는 6초 클립으로 만들어 주세요. 릴스용 9:16.",
      "30초 UGC 광고: 젊은 여성이 이 스킨케어 병을 들고 건조한 피부가 어떻게 나아졌는지 카메라에 이야기합니다. 9:16.",
      "60초 유튜브 설명 영상: 책상에 앉은 친근한 진행자가 간단한 예시로 복리의 원리를 설명합니다.",
  ],
}
export function examplesFor(language: string): string[] {
  return EXAMPLES[language] || EXAMPLES.en
}

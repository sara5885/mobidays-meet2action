"""BoW 기반 반복 이슈 키워드 추출 (대시보드 위젯 3).

설계: 형태소 분석기 의존을 피하고(설치 부담↓), 한국어 토큰화 + 불용어 제거 +
빈도/문서빈도 기반 점수로 '여러 회의에 걸쳐 반복되는 이슈 키워드'를 뽑는다.

- token: 2글자 이상 한글/영문 단어 (조사 일부는 접미 제거)
- score: 전체 등장 빈도(tf) × 등장한 회의 수(df) → 여러 회의에서 반복될수록 가중
  (단일 회의에서만 많이 나온 단어보다, 여러 회의에 반복 등장하는 '구조적 이슈'를 강조)

데이터가 누적되면 임베딩 클러스터링으로 고도화 가능(README의 향후 확장 참고).
"""
from __future__ import annotations

import re
from collections import defaultdict

# 한글(2자+) 또는 영문 약어(GA, CTA 등)
_TOKEN_RE = re.compile(r"[A-Za-z]{2,}|[가-힣]{2,}")

# 화자 이름 (호칭으로 자주 등장 → 이슈 키워드 아님)
SPEAKER_NAMES = {"지훈", "수아", "채린"}

# 대명사/지시어/일반 동사 등 의미 없는 고빈도어
STOPWORDS = {
    "그게", "그거", "근데", "그건", "그럼", "그래서", "그리고", "일단", "이거", "저거",
    "우리", "지금", "정도", "한번", "다시", "조금", "어차피", "이번", "저번", "다들",
    "오늘", "내일", "이번주", "다음", "여기", "거의", "사실", "진짜", "그렇게", "이렇게",
    "같아요", "같은", "같이", "해야", "있어요", "없어요", "되는", "하는", "하고",
    "보고", "봐야", "보면", "위주", "건데", "거든요", "거예요", "걸로",
    "얘기", "말씀", "생각", "부분", "경우", "관련", "확인",
    "제가", "내가", "저는", "저도", "저희", "당신", "본인", "그게요",
    "무슨", "어디", "언제", "누가", "누구", "어떻게", "어떤", "정리", "공유",
    "같은데", "같고", "같은", "그런", "이런", "저런", "아직", "먼저", "그냥",
    "해요", "돼요", "할게요", "볼게요", "드릴게요", "합시다", "봅시다", "하죠",
    "있는", "없는", "그게요", "근데요", "맞아요", "좋아요", "알겠습니다",
}

# 뒤에 붙은 조사/호칭을 떼어 어근을 통일 (픽셀을/픽셀이/픽셀은 → 픽셀)
_JOSA = ("님이", "님", "이랑", "랑", "에서", "한테", "에게", "으로", "로", "에는",
         "에", "은", "는", "을", "를", "이", "가", "도", "만", "과", "와")


def _strip_josa(tok: str) -> str:
    for j in sorted(_JOSA, key=len, reverse=True):
        if tok.endswith(j) and len(tok) - len(j) >= 2:
            return tok[: -len(j)]
    return tok


def tokenize(text: str) -> list[str]:
    out = []
    for t in _TOKEN_RE.findall(text):
        if t.isascii():  # 영문 약어는 그대로
            if t not in STOPWORDS and len(t) >= 2:
                out.append(t)
            continue
        root = _strip_josa(t)
        if root in STOPWORDS or root in SPEAKER_NAMES or len(root) < 2:
            continue
        out.append(root)
    return out


def top_keywords(
    docs_by_meeting: dict[str, str], top_n: int = 15
) -> list[dict]:
    """returns [{keyword, tf(총빈도), df(회의수), score}] 점수 내림차순.

    docs_by_meeting: {meeting_id: 합쳐진 텍스트}
    """
    tf: dict[str, int] = defaultdict(int)
    df: dict[str, set] = defaultdict(set)
    for mid, text in docs_by_meeting.items():
        for tok in tokenize(text):
            tf[tok] += 1
            df[tok].add(mid)

    rows = []
    for kw, freq in tf.items():
        doc_freq = len(df[kw])
        # 반복 이슈 강조: 여러 회의에 걸쳐 등장할수록 가중 (df 보너스)
        score = freq * (1 + 0.5 * (doc_freq - 1))
        rows.append({"keyword": kw, "tf": freq, "df": doc_freq, "score": round(score, 1)})
    rows.sort(key=lambda r: (-r["score"], -r["df"], r["keyword"]))
    return rows[:top_n]

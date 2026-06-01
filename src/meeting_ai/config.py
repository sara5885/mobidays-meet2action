"""중앙 설정. 모든 경로/환경변수를 한 곳에서 관리한다."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 프로젝트 루트 = 이 파일 기준 2단계 위 (src/meeting_ai/config.py -> repo root)
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_DIR = DATA_DIR / "db"
DB_PATH = DB_DIR / "meeting.duckdb"

# LLM provider: "mock" | "gemini" | "ollama"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Ollama (로컬 LLM, 무료·무제한·온프레미스)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# 로컬 Whisper STT 모델 크기: tiny|base|small|medium (클수록 정확·느림)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")

# 검증/재시도
MAX_LLM_RETRIES = int(os.getenv("MAX_LLM_RETRIES", "2"))

DB_DIR.mkdir(parents=True, exist_ok=True)

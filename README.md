# APEX — Adaptive Predictive EXecution

**Elite cryptocurrency futures trading AI built exclusively for Bitget.**

APEX는 멀티 전략 합산(Confluence), 적응형 리스크 관리, 기관급 의사결정으로 시장을 분석하는 AI 트레이딩 시스템입니다.

---

## 아키텍처

```
apex/
├── __init__.py
├── config.py              # 전체 설정 (지표 파라미터, 리스크 한도, API 설정)
├── bitget_client.py       # Bitget API 클라이언트 (CCXT 기반)
├── data_manager.py        # OHLCV 데이터 fetch + TTL 캐시
├── risk_manager.py        # 포지션 사이징, 드로다운 추적, 하드스탑
├── signal_engine.py       # 4개 모듈 합산 → 트레이드 시그널 생성
├── signal_formatter.py    # 터미널/JSON 출력 포맷
├── scanner.py             # 멀티 페어 스캔 루프
└── modules/
    ├── __init__.py        # ModuleSignal / Vote 데이터클래스
    ├── trend_master.py    # EMA 21/55/200, ADX, HTF 바이어스
    ├── momentum_scanner.py # RSI, MACD, 볼륨 서지, StochRSI
    ├── smart_money.py     # 오더블록, FVG, 유동성 풀, 와이코프
    └── market_structure.py # BOS, CHoCH, S/R, 피보나치
```

---

## 4대 전략 모듈

### [1] Trend Master
- EMA 21 / 55 / 200 정렬 분석
- ADX 추세 강도 필터 (>25 = 강한 추세)
- HTF(4H/1D) 바이어스 정렬 필수

### [2] Momentum Scanner
- RSI 다이버전스 탐지 (일반 + 히든)
- MACD 히스토그램 모멘텀 전환
- 볼륨 서지 확인 (>1.5x 20기간 평균)
- Stochastic RSI 과매도/과매수 정밀도

### [3] Smart Money Tracker
- 오더블록 식별 (충격파 직전 마지막 캔들)
- Fair Value Gap(FVG) 매핑 — 가격 자석 구간
- 유동성 풀 탐지 (Equal Highs/Lows = 스탑헌트 구간)
- 와이코프 누적/분배 국면 인식

### [4] Market Structure Analyst
- BOS(구조 이탈) 확인
- CHoCH(성격 변화) 역전 탐지
- 멀티타임프레임 지지/저항 매핑
- 피보나치 되돌림 합류 구간 (0.382, 0.5, 0.618, 0.786)

---

## Confluence 규칙

| 합류 | 등급 | 포지션 크기 | 레버리지 |
|------|------|------------|---------|
| 4/4 모듈 동의 | A+ | 풀사이즈 | 최대 10x |
| 3/4 모듈 동의 | A  | 표준 크기 | 최대 7x  |
| 2/4 이하 | NO TRADE | — | — |

---

## 리스크 관리

| 파라미터 | 값 |
|---------|-----|
| 트레이드당 최대 리스크 | 계좌의 1% |
| 최대 동시 포지션 | 5개 |
| 일일 드로다운 하드스탑 | 3% |
| 주간 드로다운 하드스탑 | 7% |
| 절대 최대 레버리지 | 15x |
| 최소 R:R 비율 | 2:1 |

**분할 진입:** 1차 진입 70% + 2차 진입 30% (더 깊은 가격)

**분할 청산:**
- TP1 (40%) — 1:1.5 R:R → 손익분기 이동
- TP2 (35%) — 1:3 R:R → 수익 확보
- TP3 (25%) — 트레일링 스탑 → 수익 극대화

---

## 설치

```bash
pip install -r requirements.txt
```

API 키 설정 (`.env` 파일 생성):
```env
BITGET_API_KEY=your_api_key
BITGET_SECRET=your_secret
BITGET_PASSPHRASE=your_passphrase
```

---

## 사용법

### 전체 시장 스캔
```bash
python main.py scan
```

### 특정 페어 분석
```bash
python main.py signal BTC/USDT:USDT
python main.py signal ETH/USDT:USDT
```

### 리스크 현황 확인
```bash
python main.py status
python main.py status --sync   # 거래소에서 잔고 동기화
```

### 고급 옵션
```bash
# 상위 30개 페어 스캔
python main.py scan --top 30

# 특정 페어만 스캔
python main.py scan --pairs BTC/USDT:USDT ETH/USDT:USDT SOL/USDT:USDT

# NO TRADE 결과도 함께 출력
python main.py scan --show-all

# JSON 출력 추가
python main.py scan --json

# 계좌 잔고 지정
python main.py scan --balance 50000

# 컬러 출력 비활성화
python main.py scan --no-color
```

---

## 시그널 출력 예시

```
════════════════════════════════════════════════════════════════════
  ⚡ APEX SIGNAL  —  BTC/USDT  —  2026-04-07 09:00 UTC
════════════════════════════════════════════════════════════════════
  Direction  : LONG
  Grade      : A+  (4/4 confluence)
  Confidence : [████████████████░░░░] 82%
  Timeframe  : 1h  |  Condition: TRENDING
────────────────────────────────────────────────────────────────────
  ENTRY ZONE
    Primary   (70%) : 83,250.00
    Secondary (30%) : 82,900.00
────────────────────────────────────────────────────────────────────
  RISK LEVELS
    Stop Loss       : 81,430.00
    TP1 (40% @ 1.5R): 85,480.00
    TP2 (35% @ 3.0R): 88,740.00
    TP3 (25% trail) : 92,000.00
────────────────────────────────────────────────────────────────────
  TRADE PARAMETERS
    Leverage        : 10x
    R:R Ratio       : 3.00:1  (min: 2.0:1)
────────────────────────────────────────────────────────────────────
  MODULE VOTES
    Trend Master               ▲ LONG       [████████████] 87%
    Momentum Scanner           ▲ LONG       [██████████░░] 79%
    Smart Money Tracker        ▲ LONG       [████████░░░░] 75%
    Market Structure Analyst   ▲ LONG       [████████████] 85%
════════════════════════════════════════════════════════════════════
```

---

## 면책 조항

> **APEX는 교육 및 분석 목적의 AI입니다.**
> 모든 선물거래는 원금 손실 위험이 있으며, 최종 투자 결정의 책임은 사용자 본인에게 있습니다.
> 절대 감당할 수 없는 금액으로 거래하지 마세요.

# 여러 플라스틱의 업체 근거 검토

사업장·공정 데이터는 scrap-server가 소유합니다. 이 변경은 서버가 보낸 업체별 요약을 AI가 검토하는 부분만 확장합니다. 새 데이터셋을 AI 저장소에 복제하지 않습니다.

## 변경

- PP·PE·PET·알루미늄 외 ABS·PS/HIPS/GPPS·SAN·PVC·EPS·PA6·PA66·PC/ABS를 검토합니다.
- 경질·필름·파쇄품·플레이크·베일·펠릿·벽지·감용 인고트·어망을 구분합니다. 모델이 인용한 문장이 실제 요약에 있고 입력 수지와 형태가 일치해야 근거 확인 상태가 됩니다.
- PA6/PA66, PS/EPS, PE/PET, PC/ABS/단일 PC를 혼동하는 인용을 테스트합니다.
- 제품 목록과 제품 물성은 반입 조건이 아닙니다. 여러 공장 중 해당 라인이 불명확하면 보류합니다. `site_role_hypothesis`는 모델이 긍정하더라도 `needs_evidence`로 처리합니다.
- 공식 사이트의 HTTP 출처도 **인용 링크**로 받을 수 있습니다. 이 변경으로 URL을 가져오거나 외부 웹 검색을 수행하지 않습니다. 검색 도구의 HTTPS 제한은 유지합니다.
- 업체 최종 수락, 허가, 현재 용량은 판단하지 않습니다. `dispatch_approved=false`를 유지합니다.

## 검증 결과

전체 `pytest -q`: **503 passed, 72 skipped**, 24 subtests passed. 외부 연동 환경이 없는 기존 통합 테스트는 skip입니다. 관련 근거·상태·사진 테스트 59개 통과. Ruff 검사와 형식 검사 통과.

2026-11-14 로컬 AI 서비스에서 Azure 모델을 실제 호출했습니다. 입력은 합성 물량 조건과 우리 팀이 작성한 공개 자료 요약이며 기업 계정·사진·QR 토큰은 전송하지 않았습니다. 요청 3개 배치에 12개 후보를 넣었습니다.

| 결과 | 후보 |
| --- | --- |
| 요약의 문장 근거 확인 (`reviewed`) | 대운 PP 스크랩, 영신 PE 필름, 독산 PP 베일, 성지 PVC 벽지, 에코피에스 EPS 인고트 |
| 근거 부족 (`needs_evidence`) | 유일 양산 PET병, 창맥 ABS·PS·PA66·PC/ABS 분쇄품, 씨엔텍 진주 SAN 스크랩, 넷스파 PA6 폐어망 |
| 호출 실패 | 0개 |
| 자동 인수 승인 | 0개 |

이는 독립 전문가 정답셋이나 정확도 측정이 아닙니다. 같은 공개 자료에서도 사업장·수지·투입 형태가 한 문장으로 직접 확인되지 않으면 보수적으로 보류할 수 있습니다. `needs_evidence`는 해당 회사가 처리 불가능하다는 판정이 아니라 자료 보완 대상으로 해석합니다. 이 결과를 통과시키려고 회사 제품 목록을 반입 사실로 바꾸지 않았습니다.

[실호출 결과와 47개 서버 조회 기록](plastic-live-review-20261114.json)에 상태·인용 출처를 남겼습니다. 원문 전체, 환경변수, API 키는 포함하지 않습니다. 로컬 PostgreSQL 명부 9,604행은 유지됐습니다. 서버의 입력 조합 44개 후보 조회/3개 보류는 회수율이나 현장 적합도 점수가 아닙니다.

## 재현

```bash
uv run --extra dev pytest -q tests/unit/test_plastic_intake_evidence.py tests/unit/test_treatment_evidence_batch.py
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
```

실호출은 서버와 동일한 `SCRAP_INTAKE_SERVICE_TOKEN`, 유효한 Azure endpoint/key/deployment를 설정한 뒤 `/treatment/intake-reviews`에 공개 요약을 보냅니다. 한 요청 최대 5개, 서버는 현재 후보를 여러 배치로 나눠 누락 없이 보냅니다. 이 PR은 비용을 발생시키는 자동 스케줄이나 새 배포를 추가하지 않습니다.

# SCRAP 처리경로 엔진

문진에서 받은 분류번호에 재질과 현재 형태를 더해 공정 KG의 시작 상태를 찾습니다. `POST /kg/next-step`은 이미 완료한 공정의 순서를 확인한 뒤, 그 상태에서 바로 이어지는 공정·세부 작업·시설·확인 조건·근거만 반환합니다. W2RKG 원본의 공정 문자열은 문헌 후보이며, 실행 순서는 출처를 확인해 별도로 적재한 `planning-reference.json`의 상태 전이를 따릅니다.

## 로컬 실행

```bash
uv sync --extra dev
uv run resource-kg import
uv run uvicorn resource_ai.main:app --host 127.0.0.1 --port 8017
```

기본 모드는 Neo4j입니다. Neo4j가 없는 시연 환경에서는 실행 환경에 `SCRAP_KG_REFERENCE_DEMO=1`을 지정하면 검수된 참조 파일을 읽습니다. 응답에서 각각 `neo4j`와 `reference_demo`로 구분합니다.

```json
{
  "waste_code": "51-03-01",
  "material": "PP",
  "input_form": "scrap",
  "completed_transitions": [],
  "origin": {"latitude": 36.64, "longitude": 127.45}
}
```

예시의 출발지는 가상입니다. 첫 조회는 `pp-prepare`의 작업과 시설만 반환합니다. 파쇄를 마치면 `completed_transitions`에 `pp-prepare`를 추가하고 실제 다음 운송 출발지 좌표를 전달합니다. 이어지는 순서는 `pp-clean` → `pp-pelletise` → `pp-compound`입니다. 파쇄품이나 펠렛 상태에 이르면 그 산출물에서 추적을 마칠 수도 있습니다. 이어지지 않는 완료 순서는 `409 invalid-progress`입니다.

| 입력 범위 | 지원 소재 |
| --- | --- |
| `51-03-01` 일반 폐합성수지 | PP, PE, PET 중 검수된 형태 |
| `51-03-07` 플라스틱 폐포장재 | PP, PE, PET 중 검수된 형태 |
| `51-29-03` 폐금속캔류 | 사용 후 알루미늄 캔 |

시설 후보는 다음 공정에 연결된 사업장으로 제한합니다. 같은 사업장이 여러 공정에 등장해도 거리 순위는 한 번만 계산합니다. 공개 위치 좌표가 있는 시설을 대권거리 순으로 정렬해 최대 5곳에 `distance_rank`를 부여합니다. 좌표가 없으면 업체 근거와 함께 `거리 미확인`으로 남깁니다. 좌표는 공개 지도나 주소 점이며 운송 출입구는 현장 확인이 필요합니다. 도로거리로 운영하려면 서버의 경로 제공자와 현장 출입구 좌표를 연결해야 합니다.

웹은 동일 출처의 `POST /api/treatment/next-step`을 호출합니다. 운영 백엔드는 인증과 기업 권한을 확인한 뒤 이 요청을 `POST /kg/next-step`으로 전달합니다. 시연 중 완료 상태는 웹 브라우저에 저장하며, 실제 업체 연락·배차·인수와 법적 적합성은 별도 확인 사항입니다.

# excel-analyzer 설치와 실행

레포의 이 디렉터리가 skill 원본이다. Hermes 프로필 홈의 `skills/excel-analyzer/`가 실행 위치다.

## 설치

`HERMES_HOME`은 이번 검증 프로필이다. 기본 `~/.hermes`를 쓰지 않는다.

```bash
export HERMES_HOME=/home/raven323/hermes-excel-dev3
bash /home/raven323/Project/excel_ai_analyzer/hermes/excel-analyzer/install_skill.sh
```

실제 운영 설정은 레포 밖에 둔다. `operator.example.json`을 복사한 뒤 경로만 고친다.

```bash
cp /home/raven323/Project/excel_ai_analyzer/hermes/excel-analyzer/operator.example.json \
  "$HERMES_HOME/excel-analyzer-operator.json"
```

`"$HERMES_HOME/.env"`에 다음을 넣는다.

```bash
EXCEL_ANALYZER_OPERATOR_CONFIG=/home/raven323/hermes-excel-dev3/excel-analyzer-operator.json
```

## 직접 실행

```bash
export EXCEL_ANALYZER_OPERATOR_CONFIG="$HERMES_HOME/excel-analyzer-operator.json"
python3 "$HERMES_HOME/skills/excel-analyzer/connect.py" \
  --task summary \
  --file /home/raven323/Project/excel_ai_analyzer/docs/development/dev0/fixtures/sales.xlsx \
  --sheet 0 \
  --request-id manual-summary-1
```

`schema`와 `quality`는 `--task`만 바꾼다. 품질과 스키마 예시는 `docs/development/dev0/fixtures/quality.xlsx`다. 이 합성 파일은 다시 만들지 않는다.

## 셀과 범위 조회 (read_range, DEV-4)

skill 1.1.0에서 추가했다. 단일 셀과 직사각형 범위의 저장된 내용을 읽는다. 요약, 스키마, 품질을 대신하지 않는다.

```bash
export EXCEL_ANALYZER_OPERATOR_CONFIG="$HERMES_HOME/excel-analyzer-operator.json"
python3 "$HERMES_HOME/skills/excel-analyzer/connect.py" \
  --task read_range \
  --file /home/raven323/Project/excel_ai_analyzer/docs/development/dev0/fixtures/dev4-read-range/cells.xlsx \
  --sheet 0 \
  --address A4:C5 \
  --request-id manual-read-1
```

- `--address`는 `read_range`에만 쓰고 필수다. 없거나 공백뿐이면 `missing_address`, 종료 2다. `summary`, `schema`, `quality`에 붙이면 `forbidden_argument`, 종료 2다. 둘 다 래퍼를 실행하지 않는다.
- 주소는 받은 그대로 래퍼에 보낸다. 래퍼 요청은 `contract_version`, `request_id`, `operation: read_range`, `inputs`, `read.address`뿐이다.
- 설치본 `/home/raven323/hermes-excel-dev3/skills/excel-analyzer`는 2026-10-19에 저장소 skill 1.1.0과 같게 동기화했다. 변경 전 설치본은 `/home/raven323/Project/cursor-codex-trial-runtime/dev4-hermes-e2e/install-backup-20261019T050856Z`에 있다. 동기화 후 sha256은 `SKILL.md` `b4db53c10001a8f44cb8f3fa43dbde780e1c699aa1ba6234c3f4144dc6a555c7`, `connect.py` `0bf41c3922a02b1f8085994af0d7e0667e84fa65a274c53b94ade67039af11bc`, `README.md` `bd1337b7139b0db849b6fdd1eead800cfcf566799a225c90be69cf6140406d07`, `operator.example.json` `8eed8945f40a474973a2c06abf18f6c17983e9396d0bea6c3bc6084859bd0b45`다. 이후 skill 1.1.1로 다시 동기화했다. 아래 `covered 재검증`을 본다. 그 뒤 skill 1.1.2로 동기화했다. 아래 `결합 세션`을 본다.

응답에서 셀 읽기:

- 성공은 `status`가 `success`이고 `wrapper_exit_code`가 0일 때뿐이다.
- 셀은 `cells` 배열이다. 래퍼 결과를 그대로 옮기고 빈 칸도 남긴다. `text`와 `data`는 null이다.
- `source.display_name`, `source.sheet_name`, `requested.address`로 파일, 시트, 요청 주소를 확인한다.
- `formula`는 수식 문자열, `cached_value`는 저장 계산값이다. `cached_value_state`가 `absent`이면 저장값이 없고, `stored`이면 0도 값이다.
- `recalculated`는 false다. 저장값이 최신인지는 확인하지 않는다.
- `merge.role`이 `covered`인 칸의 값은 null이다. 기준 셀 값으로 채우지 않는다.
- `structural_state`는 `not_applicable`이다. 분석 검증 성공이 아니다.
- 실패는 래퍼의 `status`, `error.code`, `error.message`, 종료 코드를 그대로 낸다. 셀은 null이다.

경로 비공개: `read_range` 공개 JSON에는 입력 절대 경로, 작업 사본 경로, 작업 공간 경로가 없다. `input_integrity.files`에는 `display_name`, 해시, 상태만 있다. 래퍼 응답에 들어 있으면 `path_exposure_blocked`, 종료 1로 막는다. 래퍼를 부르기 전 실패에서는 경로가 든 `request_id`를 null로 두고, 경로가 든 오류 메시지를 경로 없는 문장으로 바꾼다. `error.code`와 종료 코드 2는 그대로다. `summary`, `schema`, `quality`의 `input_integrity.files[].path`는 지금처럼 남는다.

모델 없는 검사 명령:

```bash
cd /home/raven323/Project/excel_ai_analyzer
python -m pytest tests/test_hermes_excel_bridge.py -q \
  --deselect tests/test_hermes_excel_bridge.py::test_direct_summary_schema_and_quality
python -m pytest tests/test_application_read_range.py tests/test_cell_reader.py -q
```

`test_direct_summary_schema_and_quality`는 실제 엔진 모델을 부르므로 이 검사에서만 뺀다.

합성 파일 `cells.xlsx`는 테스트가 그 경로에 파일이 없을 때만 만든다. `B5`는 `<v>` 요소가 없는 수식 칸이다. 이미 있는 파일은 해시 누락, 해시 불일치, 칸 구조 불일치 중 어느 경우에도 덮어쓰지 않고, 해시가 다르면 테스트가 실패한다. `expected.json`은 테스트가 쓰지 않는다. 파일이 없거나 JSON이 아니거나 `sha256`이 64자 hex가 아니면 테스트는 두 파일을 만들거나 고치지 않고 빠진 항목을 밝혀 실패한다. 자세한 경위는 `docs/development/hermes.md`와 로컬 브랜치 `wip/hermes-skill`의 원본 기록에 있다.

skill 연결 작업의 범위는 skill 연결과 모델 없는 검사까지였다. 위 검사는 작성한 작업자 환경에서 Shell이 거절되어 작업자가 직접 실행하지 못했다. 자세한 내용은 `docs/development/hermes.md`에 있다.

### read_range Hermes 종단 (2026-10-19)

DEV-4 전체 완료가 아니다. 근거는 `/home/raven323/Project/cursor-codex-trial-runtime/dev4-hermes-e2e/evidence.json`과 그 세션 로그다.

실제 Hermes 세션은 2회다. 상한은 4회였고 세 번째와 네 번째는 실행하지 않았다. 프로필의 `skills.auto_load`는 `excel-analyzer` 그대로다. 자동 로드는 자동 발견 성공이 아니다.

- B2, 세션 `20261019_140921_a69e1d`. 설치본 `connect.py`의 `read_range`, Data 시트, 주소 `B2`, 요청 ID `dev4-e2e-b2`. 래퍼 `success`, 종료 0, 값 10, `input_integrity` `unchanged`. skill 본문은 저장된 시스템 프롬프트에 있었고 `skill_view`는 없었다. 최종 답변은 B2가 10이라고 말했다. 기존 자동 답변 검증기로 판정하지 않았고 값 대조는 수동이다.
- A4:C5, 세션 `20261019_141057_abe5b1`. 주소 `A4:C5`, 요청 ID `dev4-e2e-range`. 모델이 `skill_view`로 skill 본문을 읽었다. 래퍼는 `success`, 종료 0이고 B4는 `value` null, 빈 칸, `merge.role` `covered`다. 최종 답변은 B4의 내용이 A4에서 온다고 말했다. 답변은 래퍼 결과와 일치하지 않는다. Hermes 종료 코드 0은 이 사례의 성공이 아니다.

`A5:B5` 수식 사례와 `C5:A4` 역방향 범위 사례는 실행하지 않았다. 역방향 범위의 래퍼 오류는 Hermes 종단에서 확인하지 않았다.

세션 뒤에도 `cells.xlsx` sha256은 `587a43b518f4d3de9e85cff2af8252abf049627697cd8f600843b7951b6022e7`, `expected.json` sha256은 `a62b19cafa1819d32edc1fa349f66e3b629eac6844066c671505de55cfd6ea2a`다.

read_range의 네 사례 종단, 반복 실행 안정성, 자동 발견, 자동 답변 검증은 확인하지 않았다. 수식 재계산이나 저장값의 최신성은 확인하지 않는다.

### covered 답변 지침 보완 (skill 1.1.1)

저장소 skill은 covered 답변 지침만 1.1.1로 보완했다. `merge.role`이 `covered`인 칸은 `value` null, `value_type` `empty`를 그대로 말하고, 병합 관계는 `merge.role`, `merge.anchor`, `merge.range`로 설명하며, 기준 셀의 값을 그 칸의 값이나 내용으로 말하지 않는다. 수식, 캐시, 일반 셀, 호출 예시 문장은 바꾸지 않았다.

- 이 작업은 설치본을 동기화하지 않았고 Hermes를 실행하지 않았다. 그때 설치본은 위에 기록한 1.1.0 그대로였다. 이후 1.1.1로 동기화한 기록은 아래 `covered 재검증`에 있다.
- 모델 없는 문구 검사를 `tests/test_hermes_excel_bridge.py`에 추가했다. 문구 검사는 모델 동작 검증이 아니다.
- 세션 `20261019_141057_abe5b1`의 실패 판정은 유지한다. 이 보완으로 그 사례가 성공으로 바뀌지 않는다.
- DEV-4 전체 완료가 아니다.

### covered 재검증 (skill 1.1.1, 2026-10-19)

DEV-4 전체 완료가 아니다. 근거는 `/home/raven323/Project/cursor-codex-trial-runtime/dev4-hermes-e2e/evidence-covered.json`이다.

- 저장소 skill 1.1.1을 설치본에 동기화했다. 변경 전 설치본은 `/home/raven323/Project/cursor-codex-trial-runtime/dev4-hermes-e2e/install-backup-20261019T053952Z`다. 동기화 후 `SKILL.md` sha256은 `10e63eae85833e228ef7d0396052d06bc1f47980c008073d7ec27f68758ae11d`다. `connect.py`와 `operator.example.json` 해시는 이전과 같다.
- 프로필 `config.yaml`과 운영 JSON은 바꾸지 않았다.
- 이번 세션은 2회다. 누적 4회이고 상한은 5회다. 이전 B2 세션과 실패 세션 `20261019_141057_abe5b1`은 다시 실행하지 않았다.
- A4:C5 재검증, 세션 `20261019_144011_b8d194`. 래퍼 `success`, 종료 0. B4는 `value` null, `value_type` `empty`, `merge.role` `covered`, `merge.anchor` `A4`다. 최종 답변은 B4가 비어 있고 값이 null이며 기준 셀이 A4라고 말했다. A4 값을 B4 값으로 말하지 않았다. `input_integrity`는 `unchanged`다.
- `A5:B5`, 세션 `20261019_144120_78b383`. 래퍼 `success`, 종료 0, `recalculated` false. A5는 수식 `=1-1`, 저장 계산값 0, `cached_value_state` `stored`다. B5는 수식 `=2+2`, 저장 계산값 없음, `cached_value_state` `absent`다. 최종 답변은 그 수식과 저장값은 말했다. 재계산하지 않았다는 말과 저장값의 최신성을 보증하지 않는다는 말이 없다. 이 사례는 실패다. Hermes 종료 코드 0은 성공이 아니다.
- `C5:A4` 역방향 범위는 실행하지 않았다. 래퍼의 `reversed_range` 경로는 Hermes 종단에서 확인하지 않았다.
- fixture `cells.xlsx`와 `expected.json` 해시는 세션 전후가 같다.
- 자동 발견, 반복 실행 안정성, 완전 자동 답변 검증은 확인하지 않았다. 문구 검사는 모델 동작 검증이 아니다.
- 세션 `20261019_141057_abe5b1`의 실패 판정은 유지한다. 재검증 세션의 통과로 그 사례가 성공으로 바뀌지 않는다.

### 수식 답변 안내 보완 (skill 1.1.2)

저장소 skill은 수식 답변 안내만 1.1.2로 보완했다. `recalculated`가 false일 때 수식이나 저장 계산값을 답하면 최종 답변에 “수식을 재계산하지 않았으며, 파일에 저장된 계산값이 최신인지는 확인할 수 없습니다.”를 그대로 넣는다. 수식과 저장값을 구분하는 문장, covered 문장, 호출 예시는 바꾸지 않았다.

- 이 작업은 설치본을 동기화하지 않았고 Hermes를 실행하지 않았다. 설치본은 위 `covered 재검증`에 기록한 1.1.1 그대로다. 이후 1.1.2로 동기화한 기록은 아래 `결합 세션`에 있다.
- 세션 `20261019_144120_78b383`의 실패 판정은 유지한다. 이 보완으로 그 사례가 성공으로 바뀌지 않고, 그 세션을 다시 실행하지 않았다.
- `tests/test_hermes_excel_bridge.py`의 문구 검사를 1.1.2 기대 파일에 맞췄다. 문구 검사는 모델 동작 검증이 아니다.
- DEV-4 전체 완료가 아니다.

### 결합 세션 (skill 1.1.2, 2026-10-19)

DEV-4 전체 완료가 아니다. 근거는 `/home/raven323/Project/cursor-codex-trial-runtime/dev4-hermes-e2e/evidence-combined.json`이다.

- 저장소 skill 1.1.2를 설치본에 동기화했다. 변경 전 설치본은 `/home/raven323/Project/cursor-codex-trial-runtime/dev4-hermes-e2e/install-backup-20261019T055105Z`다. 동기화 후 `SKILL.md` sha256은 `98a9e64cb8d91b34bcbc6eed1eedd32c2cb81a984106c61c75c8fba6fa81a190`다.
- 남은 세션 1회만 사용했다. 누적 5회이고 상한은 5회다.
- 세션 `20261019_145114_aace91`은 `A5:B5`와 `C5:A4`를 한 세션에서 처리한 결합 시나리오다. 독립 세션 두 번이 아니다.
- skill 본문은 저장된 시스템 프롬프트에 있었다. `skill_view`는 없다. 자동 로드는 자동 발견이 아니다.
- `A5:B5` 호출. 주소 `A5:B5`, 요청 ID `dev4-e2e-formula-2`. 래퍼 `success`, 종료 0, `recalculated` false. A5는 수식 `=1-1`, 저장 계산값 0, `cached_value_state` `stored`다. B5는 수식 `=2+2`, 저장 계산값 없음, `cached_value_state` `absent`다.
- 최종 답변은 두 셀의 수식과 저장값을 말했고 “수식을 재계산하지 않았으며, 파일에 저장된 계산값이 최신인지는 확인할 수 없습니다.”를 넣었다. B5에 “데이터가 비어있을 가능성이 있습니다”라는 부가 문장이 있다. 그 문장은 B5의 수식 `=2+2`와 저장 계산값 없음을 바꾸지 않는다.
- `C5:A4` 호출. 주소 `C5:A4`를 그대로 보냈고 `reversed_range`, 종료 2다. 답변은 주소를 보정하지 않았고 그 조회를 성공으로 말하지 않았다.
- fixture `cells.xlsx`와 `expected.json` 해시는 세션 전후가 같다.
- 한정된 DEV-4 종단 완료의 범위는 기존 B2 성공(세션 `20261019_140921_a69e1d`), skill 1.1.1의 A4:C5 성공(세션 `20261019_144011_b8d194`), 이 결합 세션이다. 자동 발견, 반복 실행 안정성, 완전 자동 답변 검증은 포함하지 않는다.
- 문구 검사는 모델 동작 검증이 아니다. 세션 `20261019_141057_abe5b1`과 세션 `20261019_144120_78b383`의 실패 판정은 유지한다. 이 결합 세션으로 그 사례가 성공으로 바뀌지 않는다.
- DEV-4 전체 완료가 아니다.

## 표 조회 (read_table, DEV-5)

skill 1.2.0에서 추가했다. 사용자가 지정한 직사각형 범위와 헤더 행으로 표 하나를 읽는다. 칸 조회는 `read_range`, 요약·스키마·품질은 기존 작업이다.

DEV-5는 skill과 `connect.py` 연결만 끝났다. 실제 Hermes 종단은 이 작업에서 실행하지 않았다. 설치본은 동기화하지 않았고 1.1.2 그대로다.

```bash
export EXCEL_ANALYZER_OPERATOR_CONFIG="$HERMES_HOME/excel-analyzer-operator.json"
python3 "$HERMES_HOME/skills/excel-analyzer/connect.py" \
  --task read_table \
  --file /home/raven323/Project/excel_ai_analyzer/docs/development/dev0/fixtures/dev5-read-table/tables.xlsx \
  --sheet Codes \
  --address A3:C4 \
  --header 3 \
  --request-id manual-table-1
```

- `--address`는 `read_range`와 `read_table`에만 쓴다. 없거나 공백뿐이면 `missing_address`, 종료 2다.
- `--header`는 `read_table`에만 쓴다. 다른 작업에 붙이면 `forbidden_argument`, 종료 2다.
- 헤더 행은 원본 시트 행 번호다. `--header 3`은 `{"rows": [3]}`, `--header 3,4`는 `{"rows": [3, 4]}`다. 쉼표 앞뒤 공백은 허용한다. `--header none`만 `{"rows": []}`다.
- `--header`를 빼면 헤더 없음이 아니다. 래퍼 요청의 `read`에 `header` 키를 넣지 않아 래퍼가 `missing_header`, 종료 2를 낸다.
- 정수로 읽을 수 없는 헤더 토큰은 래퍼를 부르기 전에 `invalid_arguments`, 종료 2다. 범위 밖이나 역순 같은 의미상 잘못된 행 번호는 그대로 보내 래퍼의 `invalid_header`를 받는다.
- 래퍼 요청은 `contract_version`, `request_id`, `operation: read_table`, `inputs`, `read`뿐이다. `user_prompt`, `profile_name`, `analysis_mode`는 넣지 않는다. 범위와 헤더는 받은 그대로 보낸다.
- 공개 응답은 `values_from`, `freshness`, `source`, `requested`, `table`을 래퍼 결과 그대로 옮긴다. `table.columns`와 `table.rows`를 자르지 않고 `cells`나 `preview_records`로 바꾸지 않는다. `text`와 `data`는 null, `recalculated`는 false, `structural_state`는 `not_applicable`이다.
- 실패는 래퍼의 `status`, `error.code`, `error.message`, 종료 코드를 그대로 내고 표 필드는 null이다.
- 경로 비공개는 `read_range`와 같다. 입력 절대 경로, 작업 사본 경로, 작업 공간 경로가 공개 응답에 있으면 `path_exposure_blocked`, 종료 1이다. 래퍼를 부르기 전 실패에서는 경로가 든 `request_id`와 오류 메시지를 가린다.

전용 fixture는 `docs/development/dev0/fixtures/dev5-read-table/`의 `tables.xlsx`와 `expected.json`이다. `tests/test_hermes_read_table.py`가 두 파일이 모두 없을 때만 만든다. 하나라도 있으면 쓰지 않고 해시와 기대값을 대조하며, 다르면 실패한다. 기대값은 명세에서 정한 테스트 상수다. 자세한 내용은 `docs/development/hermes.md`에 있다.

모델 없는 검사 명령:

```bash
cd /home/raven323/Project/excel_ai_analyzer
python -m pytest tests/test_hermes_read_table.py -q
```

문구 검사 `test_skill_covered_wording_matches_expected_file_not_model_behavior`는 저장소 밖 기대 파일 `/home/raven323/Project/cursor-codex-trial-runtime/dev5-skill-expected.md`와 `SKILL.md`의 바이트를 비교한다. 문구 검사는 모델 동작 검증이 아니다.

## 자유 질문 분석 (analyze)

skill 1.3.0에서 추가했고 1.4.0에서 답변 본문(`answer`)을, 1.4.1에서 비교·순위 판단 금지 규칙을 더했다. 사용자 질문 원문을 엔진에 그대로 보내 계산한다.

```bash
export EXCEL_ANALYZER_OPERATOR_CONFIG="$HERMES_HOME/excel-analyzer-operator.json"
python3 "$HERMES_HOME/skills/excel-analyzer/connect.py" \
  --task analyze \
  --file /home/raven323/Project/excel_ai_analyzer/docs/development/dev0/fixtures/sales.xlsx \
  --sheet 0 \
  --question '지역별 매출 합계를 구해줘' \
  --request-id manual-analyze-1
```

- `--question`은 `analyze`에만 쓴다. 없거나 공백뿐이면 `missing_question`, 500자를 넘으면 `question_too_long`, 제어 문자가 있으면 `invalid_question`이다. 모두 종료 2이고 래퍼를 부르지 않는다.
- 분석 프로필은 운영 설정의 `analysis_profile`이다. 없으면 `generic`이다. 에이전트 인자로는 바꿀 수 없다. 래퍼의 `allowed_profiles`에도 있어야 한다. 이 값은 `summary`, `schema`, `quality`에도 적용된다.
- 공개 응답에는 공통 필드에 더해 `question_sent`, `warnings`(code와 message만), `fallback_unverified`, `answer_table`, `answer`가 있다.
- `answer_table`은 `data.preview_records`를 마크다운 표로 바꾼 것이다. 천 단위 쉼표를 넣고, 정수인 실수는 정수로, 그 밖에는 1 이상은 소수 둘째 자리, 1 미만은 넷째 자리로 표시한다. 열 이름이나(행·열을 바꾼 표는) 행 이름에 `(%)`가 있으면 소수 둘째 자리로 고정한다. 앞부분만 담았으면 `(전체 N행 중 앞 M행)`을 붙인다. 원본 값은 `data`에 그대로 있다.
- `answer`는 에이전트가 그대로 전할 한국어 답변이다. 성공이면 "분석 결과입니다."와 표, 검증 없는 대체 경로 결과면 맨 앞에 확인 문구가 붙는다. `cannot_plan`이면 엔진의 되묻는 문장을 인용한다. 그 밖의 상태에서는 null이다.

### Hermes 종단 (2026-11-13)

실제 예산 파일(저장소 밖)로 PPT 예제 7개와 집행률 질문 2개를 Hermes 세션으로 실행했다. 에이전트 모델은 `gemma4-excel-hermes`, 운영 설정 `analysis_profile`은 `budget`이다.

- 9개 모두 질문 원문이 그대로 전달됐다.
- 마지막 실행(skill 1.4.0, 수정한 `profile-soul.md`)에서 9개 중 8개가 표를 포함한 한국어 답변이었다. 1개는 표 없이 한 문장만 답했다.
- PPT 예제 7개의 엔진 결과는 원본과 대조해 맞았다. `generic` 프로필에서는 7개 중 3~4개만 맞았다.
- `profile-soul.md`의 Hermes 기본 문구 "끝낸 작업은 바꾼 것·확인한 것·남은 것을 보고한다" 때문에 분석 답변 대신 작업 보고서를 내는 경우가 있어, 데이터 질문에는 답 자체를 주라는 문장으로 바꿨다.
- 에이전트가 표 뒤에 덧붙인 설명 문장은 틀릴 수 있다. 한 실행에서 가장 높은 집행률을 잘못 짚었다. skill 1.4.1에서 비교·순위 판단을 덧붙이지 않는 규칙을 더했고, 그 4개 질문(1.4.0에서 표가 빠진 질문 포함)을 다시 돌려 4개 모두 표가 있고 비교 문장이 없음을 확인했다.
- "대비" 없이 "비목분류별 집행률"만 물으면 엔진이 분모 열을 스스로 고르고, 결과가 백분율이 아닌 비율(0~1)로 나온다.

## Hermes 종단 실행

에이전트 모델과 Excel 엔진 모델은 다르다. 이번 검증의 에이전트는 로컬 Ollama `gemma4-excel-hermes`(`gemma4:latest`에서 `num_ctx 64000`만 지정한 파생 태그)이고, 엔진은 운영 설정의 `qwen2.5:7b`다. Hermes는 컨텍스트가 64000 미만이면 세션을 시작하지 않는다.

이 프로필에서는 기존 skill 파일을 지우지 않고 `config.yaml`의 `skills.disabled`로 묶음 skill을 비활성화했다. `hermes skills opt-out`은 이후 묶음 skill 재설치를 멈춘다.

2026-10-15 종단 확인은 `skills.auto_load`에 `excel-analyzer`가 있는 이 프로필에서 했다. 자동 로드는 자동 발견 성공이 아니다.

환경:

```bash
export HERMES_HOME=/home/raven323/hermes-excel-dev3
export PATH="$HOME/.local/bin:$PATH"
export EXCEL_ANALYZER_OPERATOR_CONFIG="$HERMES_HOME/excel-analyzer-operator.json"
```

- `hermes --version`: 이 `HERMES_HOME`에서 `Hermes Agent v0.22.0+119.g0e427d7`
- 실행 파일: `/home/raven323/.local/bin/hermes`
- 에이전트 모델: `gemma4-excel-hermes`
- 엔진: 운영 설정의 `qwen2.5:7b`, `http://127.0.0.1:11434`

허용 입력 폴더는 `/home/raven323/Project/excel_ai_analyzer/docs/development/dev0/fixtures` 하나뿐이다. `{파일}`은 그 폴더 안의 절대 경로로 쓴다.

요청 예시는 검증에 쓴 문장이다.

- 요약: `{파일} 파일을 시트 0 기준으로 요약해줘. 요청 ID는 {요청ID}로 해줘.`
- 스키마: `{파일} 파일의 컬럼 목록을 시트 0 기준으로 알려줘. 요청 ID는 {요청ID}로 해줘.`
- 품질: `{파일} 파일의 데이터 품질을 시트 0 기준으로 진단해줘. 요청 ID는 {요청ID}로 해줘.`

요청 문장을 파일에 넣고 실행한다.

```bash
hermes chat --oneshot --yolo --reasoning none -t terminal,skills --max-turns 12 \
  --format stream-json --query-file /절대/경로/요청.txt
```

`--yolo`는 이 검증 프로필에서 터미널 승인 프롬프트를 넘기기 위한 공식 옵션이다.

결과 확인:

- stream-json의 마지막 답변이 사용자에게 보이는 문장이다.
- 래퍼 JSON은 터미널 도구 출력의 한 줄이다. 성공은 `status`가 `success`이고 `wrapper_exit_code`가 0이다.
- 스키마 공개 파일은 `artifacts`의 `path`다. 루트는 `/home/raven323/hermes-excel-dev3/excel-publish`다. `exists`, `size_bytes`, `sha256`을 그 파일과 비교한다.
- 오류는 `error.code`와 `wrapper_exit_code`로 본다. 없는 파일은 `missing_file`과 2다.
- 존재하는 입력은 `input_integrity.state`가 `unchanged`이고 before와 after 해시가 같을 때 무결성이 확인된 것이다. `unconfirmed`는 무결성 성공이 아니다.
- 세션 본문은 `$HERMES_HOME/state.db`에 있다.

## 제한

아래 완료 문장과 미검증 목록은 DEV-3의 요약, 스키마, 품질, 없는 파일 사례다. `read_range`와 `read_table`에는 해당하지 않는다.

완료 범위: 자동 로드 프로필에서 네 사례 종단 확인 완료.

2026-10-15에 `skills.auto_load`에 `excel-analyzer`가 있는 이 프로필에서 스키마, 요약, 품질, 없는 파일 네 사례를 각 1회 실행했다. 스키마, 요약, 품질은 `status`가 `success`이고 `wrapper_exit_code`가 0이다. 없는 파일은 `missing_file`과 종료 코드 2가 기대한 실패 처리다. 그 사례의 `input_integrity.state`는 `unconfirmed`이고 무결성 성공이 아니다. skill 본문은 자동 로드로 모델 요청에 들어갔다. 자동 로드는 자동 발견 성공이 아니다.

미검증으로 남긴 것:

- 자동 발견. `skills.auto_load` 없이 모델이 `skill_view`로 `excel-analyzer`를 찾는지는 확인하지 않았다.
- 반복 실행 안정성. 네 사례는 각 1회 실행이고 반복 실행은 확인하지 않았다.
- 완전 자동 답변 검증. 네 답변 중 스키마와 없는 파일만 기존 답변 검사를 자동 통과했다. 요약과 품질 답변은 래퍼 `text`와 별도로 대조했으므로 완전 자동 답변 검증은 확인하지 않았다.

그 밖의 제한:

- 허용 입력 폴더는 위 fixture 폴더 하나뿐이다.
- 같은 Linux 호스트의 절대 경로만 지원한다.
- 다중 파일 통합, 편집, 수식 재계산은 이 skill의 기능이 아니다. 자유 계산은 `analyze`로 한 번에 시트 하나, 질문 하나만 한다.
- 자동 재시도는 없다.
- `read_range`는 `.xlsx`만, 한 번에 2000칸까지 읽는다. Hermes 자연어 종단은 누적 5회이고 상한 5회에 도달했다. 첫 범위 사례의 답변은 래퍼 결과와 다르다. skill 1.1.1 재검증에서 범위 사례는 통과했고 `A5:B5` 수식 사례는 실패했다. skill 1.1.2 결합 세션에서 `A5:B5`와 `C5:A4`를 한 세션으로 처리했고 필수 기준을 충족했다. 독립 세션 두 번이 아니다. 한정된 DEV-4 종단 완료는 B2, 1.1.1의 A4:C5, 이 결합 세션까지이고 DEV-4 전체 완료가 아니다. 위 `read_range Hermes 종단 (2026-10-19)`, `covered 재검증`, `결합 세션`을 본다.
- `read_table`은 `.xlsx`만, 헤더 포함 2000칸까지 읽는다. 헤더는 범위 첫 행에서 시작하는 두 줄까지다. Hermes 자연어 종단은 실행하지 않았다. 위 `표 조회 (read_table, DEV-5)`를 본다.
- 통합 매출 0은 별도 미해결 결함이다.

구조 요약과 다음 단계는 `docs/development/hermes.md`에 있다. 세션 ID와 단계별 원본 기록은 로컬 브랜치 `wip/hermes-skill`에 있다.

## 제거

skill만 지운다. Hermes 설치 전체는 지우지 않는다.

```bash
export HERMES_HOME=/home/raven323/hermes-excel-dev3
bash /home/raven323/Project/excel_ai_analyzer/hermes/excel-analyzer/uninstall_skill.sh
```

Hermes 자체를 지울 때는 공식 제거 절차를 쓰고, 제거 전에 `HERMES_HOME`이 이 프로필인지 확인한다.

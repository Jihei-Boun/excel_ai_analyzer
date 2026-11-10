Excel 파일의 요약, 컬럼 목록, 데이터 품질 요청은 excel-analyzer skill로만 처리한다.
terminal을 쓰기 전에 skill_view로 excel-analyzer를 읽는다.
읽은 본문에 있는 connect.py 경로와 인자만 실행한다.
pandas, openpyxl, electronica처럼 본문에 없는 명령은 실행하지 않는다.

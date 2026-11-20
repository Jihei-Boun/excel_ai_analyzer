"""Browser check of the upload / sidebar / preview flows in the running app.

Unit tests cannot see Streamlit widget state: a st.rerun() before the uploader is
drawn drops its files, and that broke the app twice (PR #15, #16). Run this after
any change under ui/ and before asking anyone to check by hand.

Playwright is not a project dependency. Install it in a separate venv:

  python3 -m venv /tmp/pw && /tmp/pw/bin/pip install playwright
  /tmp/pw/bin/python -m playwright install chromium

Start a separate instance (never on port 8502), then run:

  EXCEL_ANALYZER_STRUCTURE=off .venv/bin/streamlit run app.py \
      --server.port 8504 --server.headless true --server.address 127.0.0.1
  /tmp/pw/bin/python scripts/ui_upload_check.py http://127.0.0.1:8504

All files are synthetic and are written to a temporary folder.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from openpyxl import Workbook
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8504"
WORK = Path(tempfile.mkdtemp(prefix="ui_upload_check_"))
SP = str(WORK)
A, B = "multi_sheet_sales.xlsx", "staff_list.xlsx"
C, D = "branch_quarters.xlsx", "dept_kinds.xlsx"


def _write_files() -> None:
    (WORK / "ui").mkdir()
    wb = Workbook()
    wb.active.title = "1월"
    for i, month in enumerate(("1월", "2월", "3월")):
        ws = wb.active if i == 0 else wb.create_sheet(month)
        ws.append(["지점", "품목", "매출"])
        for j, branch in enumerate(("수원", "천안", "청주", "원주", "강릉")):
            ws.append([branch, "A", 100 * (i + 1) + j])
    wb.save(WORK / "ui" / A)
    wb = Workbook()
    ws = wb.active
    ws.append(["이름", "부서", "입사일", "연차"])
    for k in range(8):
        ws.append([f"직원{k}", "가나다"[k % 3], f"2024-0{k % 9 + 1}-01", k])
    wb.save(WORK / "ui" / B)
    wb = Workbook()
    ws = wb.active
    ws.append(["지점", "1분기", "2분기", "3분기", "4분기", "연간목표"])
    for name, base in (("수원지점", 1400), ("천안지점", 1000), ("청주지점", 800), ("원주지점", 600)):
        ws.append([name, base, base + 10, base + 20, base + 30, base * 4])
    wb.save(WORK / "ui" / C)
    wb = Workbook()
    ws = wb.active
    ws.append(["부서", "구분", "금액"])
    for dept, plan in (("기획부", 480), ("영업부", 920), ("생산부", 1350), ("총무부", 370)):
        ws.append([dept, "계획", plan])
        ws.append([dept, "집행", plan - 50])
    wb.save(WORK / "ui" / D)


_write_files()
results = []


def settle(page, t=1.2):
    time.sleep(t)
    page.wait_for_function(
        "() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=90000
    )
    time.sleep(1.0)


def look(page):
    lines = page.inner_text("body").splitlines()
    return {
        "files": next((l for l in lines if l.startswith("업로드된 파일")), ""),
        "preview": next((l for l in lines if l.startswith("미리보기 · ")), ""),
        "header": next((l for l in lines if "원본 df" in l), ""),
        "has_table": any("데이터 미리보기" in l for l in lines),
    }


def check(name, cond, page):
    st = look(page)
    ok = bool(cond(st))
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name, "|", st["files"][:12], "|", st["preview"], "|", st["header"])


def fresh(browser):
    page = browser.new_page(viewport={"width": 1600, "height": 1200})
    page.goto(URL)
    page.wait_for_selector("input[type=file]", state="attached", timeout=60000)
    settle(page, 3)
    return page


def upload(page, names):
    page.set_input_files("input[type=file]", [f"{SP}/ui/{n}" for n in names])
    settle(page, 4)


def chip_x(page, name):
    page.locator(f'[data-testid=stFileChip]:visible [aria-label="Remove {name}"]').first.click()
    settle(page, 2)


def side_pick(page, name):
    page.locator("[data-testid=stSidebar]").get_by_text(name, exact=True).first.click()
    settle(page)


def click(page, label):
    btn = page.get_by_role("button", name=label)
    if btn.count():
        btn.first.click()
        settle(page)


with sync_playwright() as p:
    browser = p.chromium.launch()

    # S1: two files at once -> single -> switch the analysis file four times
    page = fresh(browser)
    upload(page, [A, B])
    check("S1a two files uploaded at once", lambda s: "2개" in s["files"] and s["has_table"], page)
    click(page, "단일 모드")
    check("S1b single mode: preview is the analysis file",
          lambda s: s["has_table"] and s["preview"].split("· ")[-1] in s["header"], page)
    for name in (A, B, A, B):
        side_pick(page, name)
        check(f"S1c sidebar -> {name[:10]}",
              lambda s, n=name: "2개" in s["files"] and n in s["preview"] and n in s["header"], page)

    # S5: change the sheet selection of the multi-sheet file
    side_pick(page, A)
    ms = page.locator("[data-testid=stSidebar] [data-testid=stMultiSelect]")
    if ms.count():
        ms.first.click()
        time.sleep(0.8)
        opts = page.locator("[role=option]")
        if opts.count():
            opts.first.click()
            settle(page)
            page.keyboard.press("Escape")
            settle(page)
        check("S5 sheet selection change keeps files", lambda s: "2개" in s["files"] and s["has_table"], page)
    else:
        print("SKIP S5 no sheet selector found")

    # S6: the reset button keeps the files
    click(page, "초기화")
    check("S6 reset keeps files", lambda s: "2개" in s["files"] and s["has_table"], page)

    # S2: X on one of two files keeps the other
    chip_x(page, B)
    check("S2 X one of two keeps the other", lambda s: "1개" in s["files"] and s["has_table"] and B not in s["header"], page)

    # S3: X on the last file clears; a new upload lands in one try
    chip_x(page, A)
    check("S3a X on the last file clears list and preview", lambda s: s["files"] == "" and not s["has_table"], page)
    upload(page, [C])
    check("S3b new upload lands in one try", lambda s: "1개" in s["files"] and C in s["header"], page)

    # S4: delete button, then X on its chip, then upload the same name again
    upload(page, [D])
    check("S4a second file added", lambda s: "2개" in s["files"], page)
    page.get_by_role("button", name="삭제").last.click()
    settle(page)
    check("S4b delete button removes one", lambda s: "1개" in s["files"], page)
    if page.locator(f'[data-testid=stFileChip]:visible [aria-label="Remove {D}"]').count():
        chip_x(page, D)
    upload(page, [D])
    check("S4c same name can be uploaded again", lambda s: "2개" in s["files"], page)
    visible = page.locator(f'[data-testid=stFileChip]:visible [aria-label="Remove {D}"]').count()
    print("   visible D chips after re-upload:", visible)
    page.get_by_role("button", name="삭제").last.click()
    settle(page)
    check("S4d delete the re-uploaded file again", lambda s: "1개" in s["files"], page)
    upload(page, [D])
    check("S4e upload it a third time", lambda s: "2개" in s["files"], page)
    page.locator(f'[data-testid=stFileChip]:visible [aria-label="Remove {D}"]').last.click()
    settle(page, 2)
    check("S4f X on the visible copy removes it", lambda s: "1개" in s["files"] and C in s["header"], page)

    # S7: multi mode -> single -> switch
    page2 = fresh(browser)
    upload(page2, [C, D])
    click(page2, "동시 분석")
    check("S7a multi mode with two files", lambda s: "2개" in s["files"], page2)
    click(page2, "단일 모드")
    side_pick(page2, C)
    side_pick(page2, D)
    check("S7b back to single and switch",
          lambda s: "2개" in s["files"] and D in s["preview"] and D in s["header"], page2)

    # S8: a long file name (shortened on screen) is hidden after delete and can come back
    page3 = fresh(browser)
    upload(page3, [A, C])
    page3.get_by_role("button", name="삭제").first.click()
    settle(page3)
    hidden_ok = page3.locator(f'[data-testid=stFileChip]:visible [aria-label="Remove {A}"]').count() == 0
    check("S8a long name: deleted chip is hidden", lambda s: hidden_ok and "1개" in s["files"], page3)
    upload(page3, [A])
    check("S8b long name: uploaded again", lambda s: "2개" in s["files"], page3)

    browser.close()

print("SUMMARY", sum(ok for _, ok in results), "/", len(results))
sys.exit(0 if all(ok for _, ok in results) else 1)

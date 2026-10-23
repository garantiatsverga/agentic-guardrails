"""Генератор тестовых PDF для Document Agent (спринт 1)."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]

def _register_font() -> str:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            pdfmetrics.registerFont(TTFont("DejaVu", path))
            return "DejaVu"
    raise RuntimeError("Не найден DejaVuSans.ttf. sudo apt install fonts-dejavu")


FULL_APPLICATION = [
    ("Название компании", "ООО Ромашка"),
    ("ИНН", "1234567890"),
    ("Запрашиваемая сумма", "1000000"),
    ("Срок (мес.)", "24"),
    ("Выручка за последний год", "5000000"),
    ("Прибыль за последний год", "350000"),
    ("Количество сотрудников", "15"),
    ("Описание бизнеса", "Производство и продажа мебели"),
]
PARTIAL_APPLICATION = [r for r in FULL_APPLICATION if r[0] != "ИНН"]
ESCALATE_APPLICATION = [
    (k, "10000000" if k == "Запрашиваемая сумма" else v) for k, v in FULL_APPLICATION
]

# Пакет одной заявки: анкета (application) + финотчётность (financial)
PACKAGE_ANKETA = [
    ("Название компании", "ООО Ромашка"),
    ("ИНН", "1234567890"),
    ("Запрашиваемая сумма", "1000000"),
    ("Срок (мес.)", "24"),
    ("Количество сотрудников", "15"),
]
PACKAGE_FINANCIAL = [
    ("Выручка за последний год", "5000000"),
    ("Прибыль за последний год", "350000"),
]

# Конфликт: сумма в анкете 1 млн, в фин 10 млн
CONFLICT_ANKETA = [
    ("Название компании", "ООО Ромашка"),
    ("ИНН", "1234567890"),
    ("Запрашиваемая сумма", "1000000"),
    ("Срок (мес.)", "24"),
]
CONFLICT_FINANCIAL = [
    ("Запрашиваемая сумма", "10000000"),
    ("Выручка за последний год", "5000000"),
]


def _render(path: Path, rows: list[tuple[str, str]], title: str) -> None:
    font = _register_font()
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    y = height - 60
    c.setFont(font, 14)
    c.drawString(50, y, title)
    y -= 30
    c.setFont(font, 11)
    for key, value in rows:
        c.drawString(50, y, f"{key}: {value}")
        y -= 22
    c.save()


def main() -> None:
    out = Path(__file__).resolve().parent

    _render(out / "case1_success.pdf", FULL_APPLICATION, "Анкета заёмщика")
    _render(out / "case2_partial.pdf", PARTIAL_APPLICATION, "Анкета заёмщика (без ИНН)")
    _render(out / "case3_escalate.pdf", ESCALATE_APPLICATION, "Анкета заёмщика (крупная сумма)")

    valid = out / "case1_success.pdf"
    broken = out / "case4_failed.pdf"
    data = valid.read_bytes()
    broken.write_bytes(data[: len(data) // 3])

    # Пакет одной заявки: 2 файла, поля не пересекаются
    _render(out / "package_anketa.pdf", PACKAGE_ANKETA, "Анкета заёмщика")
    _render(out / "package_financial.pdf", PACKAGE_FINANCIAL, "Финансовая отчётность")

    # Конфликт: сумма 1 млн в анкете vs 10 млн в финотчётности
    _render(out / "conflict_anketa.pdf", CONFLICT_ANKETA, "Анкета заёмщика")
    _render(out / "conflict_financial.pdf", CONFLICT_FINANCIAL, "Финансовая отчётность")

    print("Готово:")
    for p in sorted(out.glob("*.pdf")):
        print(f"  {p.name}: {p.stat().st_size} байт")


if __name__ == "__main__":
    main()

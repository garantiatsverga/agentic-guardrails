"""Генератор тестовых PDF для Document Agent (спринт 1).

Генерирует:
1. 4 базовых кейса §12 (case1..case4) — для тестов.
2. Пакет (анкета + финотчётность) — для проверки слияния.
3. Конфликт — для проверки field_conflict.
4. real_docs/ — 6 документов «как в жизни» (текстовые PDF вместо сканов).
   Упрощение: в спринте 1 работаем с текстовым слоем PDF.
   OCR для реальных сканов/фото — спринт 2.

Запуск:
    python tests/fixtures/make_fixtures.py
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor

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


# ==== Данные для базовых кейсов ====
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


# ============================================================================
# Базовый рендер (для кейсов §12)
# ============================================================================

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


# ============================================================================
# Красивый рендер (для real_docs — имитация реальных документов)
# ============================================================================

def _render_doc(path: Path, title: str, header_lines: list[str], body_lines: list[str]) -> None:
    """Рисует документ с шапкой и телом. Всё — текстом (для парсинга)."""
    font = _register_font()
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4

    # Шапка
    y = height - 40
    c.setFont(font, 8)
    c.setFillColor(HexColor("#666666"))
    for line in header_lines:
        c.drawRightString(width - 40, y, line)
        y -= 12
    y -= 10

    # Заголовок
    c.setFillColor(HexColor("#000000"))
    c.setFont(font, 16)
    c.drawCentredString(width / 2, y, title)
    y -= 30

    # Тело
    c.setFont(font, 10)
    for line in body_lines:
        if not line:
            y -= 10
            continue
        if line.startswith("##"):
            c.setFont(font, 12)
            c.drawString(50, y, line[2:].strip())
            c.setFont(font, 10)
        else:
            c.drawString(50, y, line)
        y -= 16
        if y < 50:
            c.showPage()
            c.setFont(font, 10)
            y = height - 40

    c.save()


def make_real_docs(out_dir: Path) -> None:
    """Создает 6 документов «как в жизни» (текстовые PDF)."""
    out_dir.mkdir(exist_ok=True)

    # 1. Анкета (уже есть как case1_success.pdf, но для полноты)
    _render(
        out_dir / "01_anketa.pdf",
        FULL_APPLICATION,
        "Анкета-заявление ИП",
    )

    # 2. Паспорт
    _render_doc(
        out_dir / "02_passport.pdf",
        "ПАСПОРТ ГРАЖДАНИНА РОССИЙСКОЙ ФЕДЕРАЦИИ",
        ["Федеральная миграционная служба", "Серия 4500 № 123456"],
        [
            "## Сведения о владельце",
            "Фамилия: Иванов",
            "Имя: Иван",
            "Отчество: Иванович",
            "Пол: Мужской",
            "Дата рождения: 01.01.1975",
            "Место рождения: г. Москва",
            "",
            "## ИНН (для сверки)",
            "ИНН: 772747550002",
            "СНИЛС: 123-456-789 00",
            "",
            "Паспортные данные замаскированы в соответствии с 152-ФЗ.",
            "Полный доступ — только у андеррайтера после MFA.",
        ],
    )

    # 3. Выписка банка
    _render_doc(
        out_dir / "03_vypiska.pdf",
        "ВЫПИСКА ПО ЛИЧНОМУ СЧЕТУ",
        ["ПАО «Банк»", "БИК 044525187", "К/с 30101810145250000745"],
        [
            "## Реквизиты счета",
            "Клиент: Иванов Иван Иванович",
            "ИНН: 772747550002",
            "Счет: 40817810099910004312",
            "Период: с 01.01.2025 по 31.12.2025",
            "",
            "## Обороты за период",
            "Дебитовый оборот: 3 636,03 руб.",
            "Кредитовый оборот: 50 000,00 руб.",
            "",
            "## Детализация операций",
            "01.01.2025 | Зачисление заработной платы | 50 000.00",
            "15.01.2025 | Оплата товаров | 1 026,03",
            "20.01.2025 | Оплата услуг | 2 570,00",
            "31.01.2025 | Торговая выручка | 2 570,00",
            "",
            "## Сводка для Document Agent",
            "Выручка за последний год: 11205000",
            "Среднемесячный оборот: 940000",
        ],
    )

    # 4. Книга учета доходов (УСН)
    _render_doc(
        out_dir / "04_kud.pdf",
        "КНИГА УЧЕТА ДОХОДОВ И РАСХОДОВ",
        ["Форма по ОКУД 0500000", "Приложение № 1 к приказу Минфина"],
        [
            "## Налогоплательщик",
            "ФИО: Иванов Иван Иванович",
            "ИНН: 772747550002",
            "ОГРНИП: 323774600074762",
            "Объект налогообложения: доходы",
            "Единица измерения: руб.",
            "",
            "## Показатели за год",
            "Выручка за последний год: 11205000",
            "Прибыль за последний год: 350000",
            "Уплаченные налоги: 625000",
            "Количество сотрудников: 8",
            "",
            "Данные извлечены из текстового слоя PDF.",
            "OCR для сканов — спринт 2.",
        ],
    )

    # 5. Выписка ЕГРИП
    _render_doc(
        out_dir / "05_egrul.pdf",
        "ВЫПИСКА ИЗ ЕГРИП",
        ["ФНС России", "Дата: 29.03.2023", "№ ИЭ9965-23-27837384"],
        [
            "## Сведения о предпринимателе",
            "ФИО: Иванов Иван Иванович",
            "ИНН: 772747550002",
            "ОГРНИП: 323774600074762",
            "Дата регистрации: 03.02.2023",
            "",
            "## Сведения о регистрирующем органе",
            "Межрайонная инспекция ФНС № 46 по г. Москве",
            "Адрес: 125373, г. Москва, Походный проезд, д. 3, стр. 2",
            "",
            "## Основной ОКВЭД",
            "47.11.3 (Розничная торговля в неспециализированных магазинах)",
            "",
            "Статус: Действующий",
        ],
    )

    # 6. ПТС (Паспорт транспортного средства)
    _render_doc(
        out_dir / "06_pts.pdf",
        "ПАСПОРТ ТРАНСПОРТНОГО СРЕДСТВА",
        ["Серия 77 ОХ", "Дубликат", "Выдан 13.12.2013"],
        [
            "## Идентификационные данные",
            "VIN: НЕ УСТАНОВЛЕНО",
            "Марка, модель: TOYOTA LAND CRUISER PRADO",
            "Год выпуска: 2010",
            "Цвет: ЧЕРНЫЙ",
            "Мощность двигателя: 134,6 л.с.",
            "",
            "## Собственник",
            "ФИО: Иванов Иван Иванович",
            "ИНН: 772747550002",
            "Адрес: г. Москва, ул. Новая, д.16",
            "",
            "## Залоговые данные (для Document Agent)",
            "Рыночная стоимость: 3000000",
            "Тип залога: Транспортное средство",
            "Собственник: Заемщик",
        ],
    )


# ============================================================================
# main
# ============================================================================

def main() -> None:
    out = Path(__file__).resolve().parent

    # 1. Базовые кейсы
    _render(out / "case1_success.pdf", FULL_APPLICATION, "Анкета заёмщика")
    _render(out / "case2_partial.pdf", PARTIAL_APPLICATION, "Анкета заёмщика (без ИНН)")
    _render(out / "case3_escalate.pdf", ESCALATE_APPLICATION, "Анкета заёмщика (крупная сумма)")

    # case4 — обрезанный PDF (битый)
    valid = out / "case1_success.pdf"
    broken = out / "case4_failed.pdf"
    data = valid.read_bytes()
    broken.write_bytes(data[: len(data) // 3])

    # 2. Пакет
    _render(out / "package_anketa.pdf", PACKAGE_ANKETA, "Анкета заёмщика")
    _render(out / "package_financial.pdf", PACKAGE_FINANCIAL, "Финансовая отчётность")

    # 3. Конфликт
    _render(out / "conflict_anketa.pdf", CONFLICT_ANKETA, "Анкета заёмщика")
    _render(out / "conflict_financial.pdf", CONFLICT_FINANCIAL, "Финансовая отчётность")

    # 4. Реальные документы (для демо)
    make_real_docs(out / "real_docs")

    print("Готово:")
    for p in sorted(out.glob("*.pdf")):
        print(f"  {p.name}: {p.stat().st_size} байт")
    print("\nreal_docs/:")
    for p in sorted((out / "real_docs").glob("*.pdf")):
        print(f"  {p.name}: {p.stat().st_size} байт")


if __name__ == "__main__":
    main()

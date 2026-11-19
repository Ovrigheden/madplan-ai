"""Genererer en printklar A4-PDF med ugens madplan og indkøbsliste."""

from __future__ import annotations

import base64
import html
import io
from datetime import date

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from src.models import MealDay, WeeklyMealPlan

_TEXT_REPLACEMENTS: dict[str, str] = {
    "–": "-",
    "—": "-",
    "’": "'",
    "‘": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    "•": "-",
    "€": "EUR",
    "\u00a0": " ",
}


def _clean_text(text: str) -> str:
    """Renser tekst til latin-1 (understøtter dansk æ, ø, å og fjerner emojis)."""
    cleaned = text
    for old, new in _TEXT_REPLACEMENTS.items():
        cleaned = cleaned.replace(old, new)
    return cleaned.encode("latin-1", errors="ignore").decode("latin-1").strip()


class _MealPlanPDF(FPDF):
    """A4-PDF med diskret dansk sidefod."""

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", size=8)
        self.set_text_color(120, 120, 120)
        printed_date = date.today().strftime("%d-%m-%Y")
        footer_text = f"Madplan AI  |  Udskrevet {printed_date}  |  Side {self.page_no()}/{{nb}}"
        self.cell(w=self.epw, h=6, text=footer_text, align="C")


def _draw_section_header(
    pdf: _MealPlanPDF,
    title: str,
    subtitle: str | None = None,
) -> None:
    """Tegner hovedoverskrift og en tynd vandret skillelinje."""
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", style="B", size=18)
    pdf.set_text_color(25, 32, 42)
    pdf.cell(
        w=pdf.epw,
        h=9,
        text=_clean_text(title),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    if subtitle:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", size=9.5)
        pdf.set_text_color(100, 110, 125)
        pdf.cell(
            w=pdf.epw,
            h=5.5,
            text=_clean_text(subtitle),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
    pdf.set_draw_color(200, 206, 214)
    y = pdf.get_y() + 1.5
    pdf.line(pdf.l_margin, y, pdf.l_margin + pdf.epw, y)
    pdf.set_y(y + 4)


def _draw_banner_bar(pdf: _MealPlanPDF, text: str) -> None:
    """Tegner en lys grå overskriftsbjælke til en dag eller en butik."""
    pdf.set_x(pdf.l_margin)
    pdf.set_fill_color(236, 240, 245)
    pdf.set_text_color(25, 32, 42)
    pdf.set_font("Helvetica", style="B", size=11)
    pdf.multi_cell(
        w=pdf.epw,
        h=7,
        text=_clean_text(text),
        fill=True,
        padding=(1.2, 2.5),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_x(pdf.l_margin)


def _draw_labeled_items(pdf: _MealPlanPDF, label: str, items: list[str]) -> None:
    """Skriver en fed label efterfulgt af kommaseparerede ingredienser."""
    cleaned_items = [_clean_text(item) for item in items if _clean_text(item)]
    if not cleaned_items:
        return
    pdf.set_x(pdf.l_margin)
    pdf.set_text_color(30, 35, 42)
    pdf.set_font("Helvetica", style="B", size=9)
    pdf.write(h=4.8, text=f"{_clean_text(label)}: ")
    pdf.set_font("Helvetica", size=9)
    pdf.write(h=4.8, text=", ".join(cleaned_items))
    pdf.ln(5.2)
    pdf.set_x(pdf.l_margin)


def _draw_day_details(pdf: _MealPlanPDF, day: MealDay) -> None:
    """Tegner beskrivelse og ingredienslister fra aktuel venstremargin."""
    if _clean_text(day.description):
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", size=9.5)
        pdf.set_text_color(45, 52, 62)
        pdf.multi_cell(
            w=pdf.epw,
            h=4.8,
            text=_clean_text(day.description),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.ln(1.0)

    _draw_labeled_items(pdf, "Tilbudsvarer", day.offer_ingredients)
    _draw_labeled_items(pdf, "Øvrige varer", day.extra_ingredients)
    _draw_labeled_items(pdf, "Basisvarer", day.pantry_staples)


def _draw_meal_day(
    pdf: _MealPlanPDF,
    day: MealDay,
    include_recipes: bool,
    image_bytes: bytes | None = None,
) -> None:
    """Tegner én ugedag med titel, evt. foto, varer og eventuelle tilberedningstrin."""
    min_space = 55 if image_bytes else 40
    if pdf.get_y() > pdf.h - pdf.b_margin - min_space:
        pdf.add_page()

    badge = "Restedag" if day.is_leftover_day else f"ca. {day.prep_time_minutes} min"
    _draw_banner_bar(pdf, f"{day.day}: {day.dish_name}  ({badge})")
    pdf.ln(1.5)

    if image_bytes:
        orig_l_margin = pdf.l_margin
        y_top = pdf.get_y()
        start_page = pdf.page_no()
        img_w = 42.0
        img_h = 31.5

        pdf.image(io.BytesIO(image_bytes), x=orig_l_margin, y=y_top, w=img_w, h=img_h)
        pdf.set_draw_color(215, 220, 226)
        pdf.rect(orig_l_margin, y_top, img_w, img_h)

        text_x = orig_l_margin + img_w + 4.0
        try:
            pdf.set_left_margin(text_x)
            pdf.set_xy(text_x, y_top)
            _draw_day_details(pdf, day)
        finally:
            pdf.set_left_margin(orig_l_margin)
            if pdf.page_no() == start_page:
                pdf.set_y(max(pdf.get_y(), y_top + img_h + 2.0))
            pdf.set_x(orig_l_margin)
    else:
        _draw_day_details(pdf, day)

    if include_recipes and day.recipe_steps:
        steps = [_clean_text(s) for s in day.recipe_steps if _clean_text(s)]
        if steps:
            if pdf.get_y() > pdf.h - pdf.b_margin - 16:
                pdf.add_page()
            pdf.ln(0.8)
            pdf.set_x(pdf.l_margin)
            pdf.set_font("Helvetica", style="B", size=9)
            pdf.set_text_color(30, 35, 42)
            pdf.cell(
                w=pdf.epw,
                h=4.8,
                text="Fremgangsmåde:",
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            pdf.set_font("Helvetica", size=9)
            pdf.set_text_color(45, 52, 62)
            for idx, step in enumerate(steps, start=1):
                pdf.set_x(pdf.l_margin + 4)
                pdf.multi_cell(
                    w=pdf.epw - 4,
                    h=4.6,
                    text=f"{idx}. {step}",
                    new_x=XPos.LMARGIN,
                    new_y=YPos.NEXT,
                )
            pdf.set_x(pdf.l_margin)

    pdf.ln(4)


def _draw_checkbox_item(pdf: _MealPlanPDF, text: str) -> None:
    """Tegner ét punkt på indkøbslisten med et afkrydsningsfelt."""
    clean = _clean_text(text)
    if not clean:
        return
    if pdf.get_y() > pdf.h - pdf.b_margin - 10:
        pdf.add_page()

    y = pdf.get_y()
    pdf.set_draw_color(90, 90, 90)
    pdf.rect(x=pdf.l_margin + 1, y=y + 1.0, w=3.4, h=3.4)
    pdf.set_xy(pdf.l_margin + 7, y)
    pdf.set_font("Helvetica", size=9.5)
    pdf.set_text_color(30, 30, 30)
    pdf.multi_cell(
        w=pdf.epw - 7,
        h=5.5,
        text=clean,
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_x(pdf.l_margin)
    pdf.ln(0.5)


def _draw_shopping_group(pdf: _MealPlanPDF, title: str, items: list[str]) -> None:
    """Tegner en butiksgruppe eller ekstra kategori med afkrydsningsfelter."""
    cleaned = [_clean_text(item) for item in items if _clean_text(item)]
    if not cleaned:
        return
    if pdf.get_y() > pdf.h - pdf.b_margin - 24:
        pdf.add_page()

    _draw_banner_bar(pdf, title)
    pdf.ln(1.5)
    for item in cleaned:
        _draw_checkbox_item(pdf, item)
    pdf.ln(3)


def build_meal_plan_pdf(
    plan: WeeklyMealPlan,
    include_recipes: bool = True,
    meal_images: dict[int, bytes] | None = None,
    include_images: bool = True,
) -> bytes:
    """Bygger en komplet, printklar A4-PDF med madplan og indkøbsliste."""
    pdf = _MealPlanPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(left=15, top=15, right=15)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.alias_nb_pages()

    # --- DEL 1: UGENS MADPLAN ---
    pdf.add_page()
    _draw_section_header(pdf, "Ugens Madplan")

    summary_lines: list[str] = []
    if _clean_text(plan.summary):
        summary_lines.append(f"Overblik: {_clean_text(plan.summary)}")
    if _clean_text(plan.estimated_offer_savings_note):
        summary_lines.append(f"Tilbudsfokus: {_clean_text(plan.estimated_offer_savings_note)}")

    if summary_lines:
        pdf.set_x(pdf.l_margin)
        pdf.set_fill_color(244, 246, 249)
        pdf.set_draw_color(220, 224, 230)
        pdf.set_text_color(40, 46, 56)
        pdf.set_font("Helvetica", size=9.5)
        pdf.multi_cell(
            w=pdf.epw,
            h=5.0,
            text="\n".join(summary_lines),
            border=1,
            fill=True,
            padding=3,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.ln(4)

    for idx, day in enumerate(plan.days):
        image_bytes = (
            meal_images.get(idx) if (include_images and meal_images) else None
        )
        _draw_meal_day(
            pdf,
            day,
            include_recipes=include_recipes,
            image_bytes=image_bytes,
        )

    # --- DEL 2: INDKØBSLISTE (NY SIDE) ---
    pdf.add_page()
    _draw_section_header(
        pdf,
        "Indkøbsliste",
        subtitle="Afkrydsningsliste opdelt efter butik",
    )

    for store_group in plan.shopping_list_by_store:
        _draw_shopping_group(pdf, store_group.store_name, store_group.items)

    _draw_shopping_group(
        pdf,
        "Øvrige indkøb (valgfri butik)",
        plan.extra_items_to_buy,
    )
    _draw_shopping_group(
        pdf,
        "Basisvarer fra køkkenskabet (tjek derhjemme)",
        plan.pantry_staples_needed,
    )

    return bytes(pdf.output())


def _html_labeled_items(label: str, items: list[str]) -> str:
    """Returnerer HTML-afsnit for en ingrediensliste hvis der findes elementer."""
    cleaned = [html.escape(item.strip(), quote=True) for item in items if item.strip()]
    if not cleaned:
        return ""
    return (
        f'<p class="meta"><strong>{html.escape(label, quote=True)}:</strong> '
        f'{", ".join(cleaned)}</p>'
    )


def _html_shopping_group(title: str, items: list[str]) -> str:
    """Returnerer HTML-gruppe for én butik eller indkøbskategori med afkrydsningsbokse."""
    cleaned = [html.escape(item.strip(), quote=True) for item in items if item.strip()]
    if not cleaned:
        return ""
    rows = "".join(
        f'<li><span class="chk"></span><span>{item}</span></li>' for item in cleaned
    )
    return (
        '<div class="shopping-group">'
        f'<div class="bar">{html.escape(title, quote=True)}</div>'
        f'<ul class="chk-list">{rows}</ul>'
        "</div>"
    )


def build_direct_print_html(
    plan: WeeklyMealPlan,
    include_recipes: bool = True,
    meal_images: dict[int, bytes] | None = None,
    include_images: bool = True,
) -> str:
    """Bygger en printknap med skjult A4-printlayout til direkte browser-udskrift."""
    printed_date = date.today().strftime("%d-%m-%Y")

    summary_parts: list[str] = []
    if plan.summary.strip():
        summary_parts.append(
            f"<p><strong>Overblik:</strong> {html.escape(plan.summary.strip(), quote=True)}</p>"
        )
    if plan.estimated_offer_savings_note.strip():
        summary_parts.append(
            "<p><strong>Tilbudsfokus:</strong> "
            f"{html.escape(plan.estimated_offer_savings_note.strip(), quote=True)}</p>"
        )
    summary_html = (
        f'<div class="summary-box">{"".join(summary_parts)}</div>' if summary_parts else ""
    )

    days_blocks: list[str] = []
    for idx, day in enumerate(plan.days):
        badge = "Restedag" if day.is_leftover_day else f"ca. {day.prep_time_minutes} min"
        title_text = html.escape(f"{day.day}: {day.dish_name}  ({badge})", quote=True)
        desc_html = (
            f'<p class="desc">{html.escape(day.description.strip(), quote=True)}</p>'
            if day.description.strip()
            else ""
        )
        details_html = (
            desc_html
            + _html_labeled_items("Tilbudsvarer", day.offer_ingredients)
            + _html_labeled_items("Øvrige varer", day.extra_ingredients)
            + _html_labeled_items("Basisvarer", day.pantry_staples)
        )

        img_bytes = meal_images.get(idx) if (include_images and meal_images) else None
        if img_bytes:
            b64 = base64.b64encode(img_bytes).decode("ascii")
            top_html = (
                '<div class="day-top">'
                f'<img class="dish-img" src="data:image/jpeg;base64,{b64}" '
                f'alt="{html.escape(day.dish_name, quote=True)}" '
                'loading="eager" decoding="sync">'
                f'<div class="day-details">{details_html}</div>'
                "</div>"
            )
        else:
            top_html = f'<div class="day-details">{details_html}</div>'

        recipes_html = ""
        if include_recipes and day.recipe_steps:
            steps = [
                f"<li>{html.escape(step.strip(), quote=True)}</li>"
                for step in day.recipe_steps
                if step.strip()
            ]
            if steps:
                recipes_html = (
                    '<div class="recipes">'
                    '<p class="recipes-title">Fremgangsmåde:</p>'
                    f'<ol>{"".join(steps)}</ol>'
                    "</div>"
                )

        days_blocks.append(
            f'<section class="day-card"><div class="bar">{title_text}</div>{top_html}{recipes_html}</section>'
        )

    shopping_blocks = [
        _html_shopping_group(group.store_name, group.items)
        for group in plan.shopping_list_by_store
    ]
    shopping_blocks.append(
        _html_shopping_group("Øvrige indkøb (valgfri butik)", plan.extra_items_to_buy)
    )
    shopping_blocks.append(
        _html_shopping_group(
            "Basisvarer fra køkkenskabet (tjek derhjemme)",
            plan.pantry_staples_needed,
        )
    )

    return f"""<!DOCTYPE html>
<html lang="da">
<head>
<meta charset="utf-8">
<style>
  @media screen {{
    html, body {{
      margin: 0;
      padding: 0;
      overflow: hidden;
      background: transparent;
      font-family: "Source Sans Pro", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    #print-doc {{
      display: none;
    }}
    .print-btn {{
      width: 100%;
      height: 38.4px;
      background: #2E6F40;
      color: #fff;
      border: 1px solid #2E6F40;
      border-radius: 10px;
      font-size: 0.95rem;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.35rem;
    }}
    .print-btn:hover {{
      background: #245933;
      border-color: #245933;
    }}
  }}
  @media print {{
    @page {{
      size: A4 portrait;
      margin: 14mm;
    }}
    html, body {{
      margin: 0 !important;
      padding: 0 !important;
      height: auto !important;
      overflow: visible !important;
      background: #ffffff !important;
      color: #19202a !important;
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 9.5pt;
      line-height: 1.38;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }}
    .no-print {{
      display: none !important;
    }}
    #print-doc {{
      display: block !important;
    }}
    .doc-header {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      border-bottom: 1px solid #c8ced6;
      padding-bottom: 2.5mm;
      margin-bottom: 4mm;
    }}
    .doc-header h1 {{
      margin: 0;
      font-size: 18pt;
      color: #19202a;
    }}
    .doc-header .subtitle {{
      font-size: 9pt;
      color: #646e7d;
    }}
    .summary-box {{
      background: #f4f6f9;
      border: 1px solid #dce0e6;
      padding: 3mm 3.5mm;
      margin-bottom: 5mm;
      border-radius: 2px;
    }}
    .summary-box p {{
      margin: 0 0 1.2mm 0;
    }}
    .summary-box p:last-child {{
      margin-bottom: 0;
    }}
    .day-card {{
      margin-bottom: 4.5mm;
    }}
    .bar {{
      background: #ecf0f5;
      color: #19202a;
      font-weight: 700;
      font-size: 10.5pt;
      padding: 1.6mm 2.5mm;
      margin-bottom: 2mm;
      break-after: avoid;
      page-break-after: avoid;
    }}
    .day-top {{
      display: flex;
      gap: 4mm;
      align-items: flex-start;
      break-inside: avoid;
      page-break-inside: avoid;
    }}
    .dish-img {{
      width: 42mm;
      height: 31.5mm;
      object-fit: cover;
      border: 1px solid #d7dce2;
      border-radius: 2px;
      flex-shrink: 0;
    }}
    .day-details {{
      flex: 1;
      min-width: 0;
    }}
    .desc {{
      margin: 0 0 1.5mm 0;
      color: #2d343e;
    }}
    .meta {{
      margin: 0 0 1mm 0;
      font-size: 9pt;
    }}
    .recipes {{
      margin-top: 1.5mm;
    }}
    .recipes-title {{
      margin: 0 0 1mm 0;
      font-weight: 700;
      font-size: 9pt;
    }}
    .recipes ol {{
      margin: 0;
      padding-left: 5mm;
      font-size: 9pt;
      color: #2d343e;
    }}
    .recipes li {{
      margin-bottom: 0.7mm;
    }}
    .shopping-section {{
      page-break-before: always;
      break-before: page;
    }}
    .shopping-group {{
      margin-bottom: 4mm;
      break-inside: avoid;
      page-break-inside: avoid;
    }}
    .chk-list {{
      list-style: none;
      margin: 0;
      padding: 0 0 0 1mm;
    }}
    .chk-list li {{
      display: flex;
      align-items: flex-start;
      gap: 2.5mm;
      margin-bottom: 1.4mm;
      font-size: 9.5pt;
    }}
    .chk {{
      width: 3.4mm;
      height: 3.4mm;
      border: 1px solid #555;
      display: inline-block;
      margin-top: 0.6mm;
      flex-shrink: 0;
    }}
  }}
</style>
</head>
<body>
  <button type="button" class="print-btn no-print" onclick="window.print()">🖨️ Udskriv direkte</button>
  <div id="print-doc">
    <div class="doc-header">
      <h1>Ugens Madplan</h1>
      <span class="subtitle">Udskrevet {printed_date}</span>
    </div>
    {summary_html}
    {"".join(days_blocks)}
    <div class="shopping-section">
      <div class="doc-header">
        <h1>Indkøbsliste</h1>
        <span class="subtitle">Afkrydsningsliste opdelt efter butik</span>
      </div>
      {"".join(shopping_blocks)}
    </div>
  </div>
</body>
</html>"""


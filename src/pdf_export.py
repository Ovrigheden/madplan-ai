"""Genererer en printklar A4-PDF med ugens madplan og indkøbsliste."""

from __future__ import annotations

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

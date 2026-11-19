"""Gemini API-integration til generering af madplan og udskiftning af enkelte retter."""

from __future__ import annotations

from google import genai
from google.genai import types

from src.models import MealDay, Offer, WeeklyMealPlan

DEFAULT_MODEL = "gemini-3.8-flash"


def _format_offers_for_prompt(offers: list[Offer]) -> str:
    """Formaterer valgte tilbud grupperet efter kategori til prompten."""
    by_cat: dict[str, list[str]] = {}
    for offer in offers:
        cat = offer.category or "Øvrige madvarer"
        by_cat.setdefault(cat, []).append(f"- {offer.to_prompt_line()}")

    sections: list[str] = []
    for cat, lines in by_cat.items():
        sections.append(f"### {cat}\n" + "\n".join(lines))
    return "\n\n".join(sections)


def generate_weekly_plan(
    api_key: str,
    offers: list[Offer],
    adults: int = 2,
    children: int = 0,
    leftover_days: int = 1,
    preferences: str = "",
    model_name: str = DEFAULT_MODEL,
) -> WeeklyMealPlan:
    """Genererer en komplet 7-dages madplan og indkøbsliste via Gemini API."""
    if not api_key.strip():
        raise ValueError("Mangler Gemini API-nøgle.")
    if not offers:
        raise ValueError("Der skal vælges mindst ét tilbud for at generere en madplan.")

    offers_text = _format_offers_for_prompt(offers)
    household_str = (
        f"{adults} voksne og {children} børn" if children > 0 else f"{adults} voksne"
    )
    pref_block = (
        f"\n- Særlige ønsker / allergier / begrænsninger: {preferences.strip()}"
        if preferences.strip()
        else ""
    )

    prompt = f"""Du er en erfaren dansk kok og økonomisk madplanlægger.
Din opgave er at lave en varieret, familievenlig og realistisk aftensmadsplan for hele ugen (Mandag til Søndag) baseret på ugens aktuelle tilbud.

### Husstand og krav
- Husstand: {household_str}.
- Antal restedage: {leftover_days} (hvis >= 1, så planlæg en ret dagen før, der er nem at lave i dobbelt portion, f.eks. kødsovs, chili con carne, lasagne, suppe eller simreret).{pref_block}

### Regler for planlægning
1. **Udnyt tilbuddene effektivt:** Byg retterne op omkring ugens tilbudsvarer for at holde budgettet nede. Angiv tydeligt hvilken butik og pris hver tilbudsvare kommer fra.
2. **Minimér madspild:** Hvis der f.eks. købes et helt hvidkål, spidskål, en stor pose gulerødder eller en stor bakke kød, skal resten genbruges i en anden ret senere på ugen.
3. **Variation:** Bland mellem kød, fisk/fjerkræ og grønt/pasta/ris i løbet af ugen.
4. **Indkøbsliste:** Saml ALLE indkøb fra tilbuddene i `shopping_list_by_store` opdelt pr. butikskæde (kun de butikker der faktisk bruges). Læg øvrige friske varer (der ikke er på tilbud) i `extra_items_to_buy`, og almindelige basisvarer (olie, salt, peber, mel, krydderier osv.) i `pantry_staples_needed`.
5. **Sprog:** Alt indhold skal være på naturligt dansk.

### Ugens udvalgte tilbud
{offers_text}
"""

    client = genai.Client(api_key=api_key.strip())
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=WeeklyMealPlan,
            temperature=0.7,
        ),
    )

    if response.parsed is not None:
        return response.parsed
    return WeeklyMealPlan.model_validate_json(response.text)


def replace_single_day(
    api_key: str,
    current_plan: WeeklyMealPlan,
    day_name: str,
    offers: list[Offer],
    adults: int = 2,
    children: int = 0,
    preferences: str = "",
    custom_note: str = "",
    model_name: str = DEFAULT_MODEL,
) -> MealDay:
    """Udskifter én bestemt dags ret med et nyt forslag, der passer til resten af ugen."""
    if not api_key.strip():
        raise ValueError("Mangler Gemini API-nøgle.")

    other_dishes = "\n".join(
        f"- {d.day}: {d.dish_name}"
        for d in current_plan.days
        if d.day.lower() != day_name.lower()
    )
    offers_text = _format_offers_for_prompt(offers)
    household_str = (
        f"{adults} voksne og {children} børn" if children > 0 else f"{adults} voksne"
    )
    note_line = f"\n- Brugerens ønske til den nye ret: {custom_note.strip()}" if custom_note.strip() else ""
    pref_line = f"\n- Generelle præferencer/allergier: {preferences.strip()}" if preferences.strip() else ""

    prompt = f"""Du er en erfaren dansk kok. Brugeren ønsker at udskifte aftensmaden for **{day_name}** i sin ugeplan.

### Husstand og kontekst
- Husstand: {household_str}.{pref_line}{note_line}
- Øvrige retter i ugen (undgå gentagelser):
{other_dishes}

### Krav til den nye ret for {day_name}
- Retten skal være ny og anderledes end de øvrige dage.
- Udnyt gerne ugens tilbud nedenfor og angiv butik + pris på tilbudsvarer.
- Svar udelukkende på dansk i det angivne JSON-format for denne ene dag (`day` skal være "{day_name}").

### Tilgængelige tilbud
{offers_text}
"""

    client = genai.Client(api_key=api_key.strip())
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=MealDay,
            temperature=0.85,
        ),
    )

    if response.parsed is not None:
        return response.parsed
    return MealDay.model_validate_json(response.text)

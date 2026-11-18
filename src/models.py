"""Datamodeller for tilbud, madplan og indkøbsliste."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Offer(BaseModel):
    """Et enkelt dagligvaretilbud fra eTilbudsavis.dk."""

    id: str
    heading: str
    description: str = ""
    price: float
    unit_size: str = ""
    store_name: str
    valid_from: str = ""
    valid_until: str = ""
    category: str = ""
    selected: bool = True

    def to_prompt_line(self) -> str:
        """Returnerer en kompakt linje til brug i AI-prompten."""
        parts = [f"{self.store_name}: {self.heading}"]
        if self.unit_size:
            parts.append(f"({self.unit_size})")
        parts.append(f"- {self.price:.2f} kr.")
        if self.description:
            short_desc = self.description.replace("\n", " ").strip()[:80]
            if short_desc and short_desc.lower() not in self.heading.lower():
                parts.append(f"[{short_desc}]")
        return " ".join(parts)


class MealDay(BaseModel):
    """En enkelt dags aftensmad i ugeplanen."""

    day: str = Field(description="Ugedag, f.eks. Mandag, Tirsdag, ..., Søndag")
    dish_name: str = Field(description="Navn på retten")
    description: str = Field(
        description="Kort appetitvækkende beskrivelse og hvordan evt. rester/tilbudsvarer udnyttes"
    )
    is_leftover_day: bool = Field(
        default=False,
        description="True hvis dagen primært består af rester fra en tidligere dag",
    )
    prep_time_minutes: int = Field(
        description="Anslået tilberedningstid i minutter"
    )
    offer_ingredients: list[str] = Field(
        default_factory=list,
        description="Ingredienser fra ugens tilbud med butik og pris, f.eks. 'Hakket oksekød 500g (Netto - 25 kr.)'",
    )
    extra_ingredients: list[str] = Field(
        default_factory=list,
        description="Øvrige friske varer der skal købes ud over tilbuddene",
    )
    pantry_staples: list[str] = Field(
        default_factory=list,
        description="Basisvarer som forventes at være derhjemme (olie, mel, krydderier osv.)",
    )
    recipe_steps: list[str] = Field(
        default_factory=list,
        description="3-6 korte og konkrete trin til tilberedning",
    )


class ShoppingStoreGroup(BaseModel):
    """Indkøbsliste grupperet for én bestemt butik."""

    store_name: str = Field(description="Navn på butikken, f.eks. Netto eller Rema 1000")
    items: list[str] = Field(
        default_factory=list,
        description="Varer der skal købes i denne butik, inkl. mængde og evt. tilbudspris",
    )


class WeeklyMealPlan(BaseModel):
    """Den samlede 7-dages madplan og indkøbsliste fra Gemini."""

    summary: str = Field(
        description="Kort opsummering af ugens madplan og hvordan madspild er minimeret"
    )
    estimated_offer_savings_note: str = Field(
        description="Kort vurdering af hvilke tilbud der bærer ugens plan"
    )
    days: list[MealDay] = Field(description="Liste med 7 dage fra Mandag til Søndag")
    shopping_list_by_store: list[ShoppingStoreGroup] = Field(
        default_factory=list,
        description="Tilbudsvarer opdelt efter hvilken butik de skal købes i",
    )
    extra_items_to_buy: list[str] = Field(
        default_factory=list,
        description="Øvrige indkøb der ikke er på tilbud (kan købes i valgfri butik)",
    )
    pantry_staples_needed: list[str] = Field(
        default_factory=list,
        description="Samlet liste over basisvarer fra køkkenskabet der bruges i ugens løb",
    )

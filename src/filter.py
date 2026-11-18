"""Filtrering og kategorisering af dagligvaretilbud, så non-food sorteres fra."""

from __future__ import annotations

import re
from collections.abc import Iterable

from src.models import Offer

# Nøgleord for varer der typisk IKKE hører hjemme i en aftensmads-madplan
NON_FOOD_KEYWORDS = {
    "vaskepulver", "skyllemiddel", "opvasketabs", "opvaskemiddel", "rengøring",
    "toiletrulle", "toiletpapir", "køkkenrulle", "bleer", "vådservietter",
    "tandpasta", "tandbørste", "shampoo", "balsam", "deodorant", "sæbe",
    "hundefoder", "kattefoder", "kattegrus", "dyrefoder",
    "havemøbler", "blomsterjord", "gødning", "plæneklipper", "værktøj",
    "stearinlys", "servietter", "bagepapir", "stanniol", "fryseposer",
    "strømper", "sokker", "t-shirt", "bukser", "jakke", "sko", "legetøj",
    "batterier", "pære", "el-pære", "motorolie", "sprinklervæske",
    "cigaret", "snus", "nikotin",
}

SNACK_DRINK_KEYWORDS = {
    "slik", "vingummi", "lakrids", "chokoladebar", "bolsjer", "chips",
    "sodavand", "cola", "coca-cola", "pepsi", "fanta", "sprite", "faxe kondi",
    "tuborg squash", "energidrik", "red bull", "monster", "ex. pant", "ekskl. pant",
    "øl", "pilsner", "tuborg", "carlsberg", "vin", "rødvin", "hvidvin", "rosé",
    "gin", "vodka", "whisky", "rom", "snaps", "akvavit", "is ", "sodavandsis",
    "kage", "småkager", "kiks",
}

FOOD_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Kød & Fisk": (
        "oksekød", "svinekød", "hakket", "kylling", "kalkun", "and ", "flæskesteg",
        "kotelet", "mørbrad", "bøf", "entrecote", "culotte", "pølse", "bacon",
        "medister", "frikadelle", "laks", "torsk", "rødspætte", "rejer", "fisk",
        "tun", "sild", "makrel", "skinke", "leverpostej", "pålæg", "salami",
    ),
    "Frugt & Grønt": (
        "kartoffel", "kartofler", "gulerod", "gulerødder", "løg", "hvidløg",
        "tomat", "agurk", "peberfrugt", "salat", "kål", "hvidkål", "spidskål",
        "rødkål", "broccoli", "blomkål", "squash", "aubergine", "avocado",
        "champignon", "svampe", "spinat", "porre", "selleri", "æble", "pære",
        "banan", "appelsin", "citron", "lime", "bær", "jordbær", "blåbær",
        "vindruer", "melon", "grønt", "majs", "ærter", "bønner", "linser",
    ),
    "Mejeri & Æg": (
        "mælk", "skummetmælk", "letmælk", "sødmælk", "havredrik", "smør",
        "lurpak", "kærgården", "ost", "mozzarella", "parmesan", "cheddar",
        "feta", "fløde", "piskefløde", "madlavningsfløde", "creme fraiche",
        "yoghurt", "skyr", "ymer", "æg", "kærnemælk",
    ),
    "Brød & Basis": (
        "pasta", "spaghetti", "penne", "lasagne", "ris", "jasminris", "basmatiris",
        "nudler", "mel", "hvedemel", "rugmel", "havregryn", "brød", "rugbrød",
        "boller", "pitabrød", "tortilla", "wraps", "pizza", "dej",
        "hakkede tomater", "tomatpuré", "kokosmælk", "bouillon", "olie",
        "olivenolie", "rapsolie", "pesto", "soja", "karry", "krydderi",
        "ketchup", "sennep", "mayonnaise", "remoulade",
    ),
}


def classify_offer(offer: Offer) -> str:
    """Bestemmer kategori for et tilbud eller returnerer 'Frasorteret' for non-food."""
    text = f"{offer.heading} {offer.description}".lower()

    for kw in NON_FOOD_KEYWORDS:
        if kw in text:
            return "Frasorteret (Non-food)"

    for kw in SNACK_DRINK_KEYWORDS:
        if kw in text:
            return "Frasorteret (Slik/Drikke)"

    for category, keywords in FOOD_CATEGORIES.items():
        for kw in keywords:
            if re.search(rf"(^|\b){re.escape(kw.strip())}", text):
                return category

    return "Øvrige madvarer"


def filter_food_offers(
    offers: Iterable[Offer],
    include_other_food: bool = True,
    max_offers: int = 240,
) -> list[Offer]:
    """Renser tilbudslisten, frasorterer non-food/slik og balancerer pr. butik."""
    seen: set[tuple[str, str, float]] = set()
    by_store: dict[str, list[Offer]] = {}

    for offer in offers:
        category = classify_offer(offer)
        if category.startswith("Frasorteret"):
            continue
        if not include_other_food and category == "Øvrige madvarer":
            continue

        dedup_key = (
            offer.store_name.lower().strip(),
            offer.heading.lower().strip(),
            round(offer.price, 2),
        )
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        updated = offer.model_copy(update={"category": category, "selected": True})
        by_store.setdefault(updated.store_name, []).append(updated)

    # Round-robin på tværs af butikker så alle valgte kæder bliver repræsenteret
    cleaned: list[Offer] = []
    store_queues = list(by_store.values())
    idx = 0
    while len(cleaned) < max_offers:
        added_any = False
        for queue in store_queues:
            if idx < len(queue):
                cleaned.append(queue[idx])
                added_any = True
                if len(cleaned) >= max_offers:
                    break
        if not added_any:
            break
        idx += 1

    return cleaned

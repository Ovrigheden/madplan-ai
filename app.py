"""Streamlit-webapp til AI-madplan baseret på ugens tilbud fra eTilbudsavis.dk."""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from src.filter import filter_food_offers
from src.models import Offer, WeeklyMealPlan
from src.offers import (
    ALL_STORES,
    DEFAULT_POSTAL_CODE,
    DEFAULT_STORES,
    fetch_offers_for_stores,
    geocode_postal_code,
)
from src.planner import DEFAULT_MODEL, generate_weekly_plan, replace_single_day

load_dotenv()

st.set_page_config(
    page_title="Madplan AI – Ugens Tilbud",
    page_icon="🛒",
    layout="wide",
)


def _get_secret(key: str, default: str = "") -> str:
    """Læser konfiguration fra miljøvariabel/.env eller Streamlit Secrets."""
    val = os.getenv(key, "")
    if val:
        return val
    try:
        secret_val = st.secrets.get(key, default)
        return str(secret_val) if secret_val is not None else default
    except Exception:
        return default


def _require_password_if_configured() -> None:
    """Kræver adgangskode hvis APP_PASSWORD er sat i .env eller Streamlit Secrets."""
    expected = _get_secret("APP_PASSWORD").strip()
    if not expected:
        return
    if st.session_state.get("authenticated"):
        return

    st.title("🔒 Madplan AI")
    pwd = st.text_input("Indtast adgangskode", type="password")
    if st.button("Log ind", type="primary"):
        if pwd == expected:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Forkert adgangskode.")
    st.stop()


def _init_state() -> None:
    if "offers" not in st.session_state:
        st.session_state["offers"] = []
    if "offer_warnings" not in st.session_state:
        st.session_state["offer_warnings"] = []
    if "location_label" not in st.session_state:
        st.session_state["location_label"] = "Herning"
    if "meal_plan" not in st.session_state:
        st.session_state["meal_plan"] = None


def _plan_to_markdown(plan: WeeklyMealPlan) -> str:
    """Konverterer madplan og indkøbsliste til en læsbar Markdown-fil."""
    lines = [
        "# Ugens Madplan & Indkøbsliste",
        "",
        f"**Overblik:** {plan.summary}",
        f"**Tilbuds-fokus:** {plan.estimated_offer_savings_note}",
        "",
        "## Madplan",
        "",
    ]
    for day in plan.days:
        tag = " *(Restedag)*" if day.is_leftover_day else ""
        lines.append(f"### {day.day}: {day.dish_name}{tag} (~{day.prep_time_minutes} min)")
        lines.append(day.description)
        if day.offer_ingredients:
            lines.append("- **Tilbudsvarer:** " + ", ".join(day.offer_ingredients))
        if day.extra_ingredients:
            lines.append("- **Øvrige varer:** " + ", ".join(day.extra_ingredients))
        if day.recipe_steps:
            lines.append("- **Fremgangsmåde:**")
            for idx, step in enumerate(day.recipe_steps, start=1):
                lines.append(f"  {idx}. {step}")
        lines.append("")

    lines.append("## Indkøbsliste")
    lines.append("")
    for group in plan.shopping_list_by_store:
        lines.append(f"### {group.store_name}")
        for item in group.items:
            lines.append(f"- [ ] {item}")
        lines.append("")

    if plan.extra_items_to_buy:
        lines.append("### Øvrige varer (valgfri butik)")
        for item in plan.extra_items_to_buy:
            lines.append(f"- [ ] {item}")
        lines.append("")

    if plan.pantry_staples_needed:
        lines.append("### Basisvarer fra køkkenskabet")
        for item in plan.pantry_staples_needed:
            lines.append(f"- {item}")

    return "\n".join(lines)


_init_state()
st.markdown(
    "<style>[data-testid='InputInstructions'] { display: none !important; }</style>",
    unsafe_allow_html=True,
)
_require_password_if_configured()

st.title("🛒 Madplan AI – Ugens Tilbud")
st.caption(
    "Henter aktuelle tilbud fra eTilbudsavis.dk og bygger en madplan + indkøbsliste med Gemini API."
)

# --- SIDEPANEL ---
with st.sidebar:
    st.header("⚙️ Indstillinger")

    env_key = _get_secret("GEMINI_API_KEY")
    if env_key == "din_gemini_api_noegle_her":
        env_key = ""

    if env_key:
        st.success("🔒 Gemini API-nøgle er indlæst sikkert på serveren.")
        custom_key = st.text_input(
            "Skift API-nøgle (valgfrit)",
            value="",
            type="password",
            help="Lad feltet stå tomt for at bruge serverens nøgle.",
        )
        api_key = custom_key.strip() or env_key
    else:
        api_key = st.text_input(
            "Gemini API-nøgle",
            value="",
            type="password",
            help="Indtast din Gemini API-nøgle fra Google AI Studio.",
        )
    st.caption("🤖 AI-model: Gemini 3.8 Flash")

    st.divider()
    st.subheader("📍 Lokation & Butikker")
    postal_code = st.text_input("Postnummer", value=DEFAULT_POSTAL_CODE, max_chars=4)
    radius_km = st.slider("Søgeradius (km)", min_value=5, max_value=40, value=15, step=5)

    selected_stores = st.multiselect(
        "Dagligvarekæder",
        options=list(ALL_STORES.keys()),
        default=list(DEFAULT_STORES.keys()),
    )

    include_other_food = st.checkbox(
        "Inkludér 'Øvrige madvarer' (ud over hovedkategorier)",
        value=True,
    )

    if st.button("1. Hent ugens tilbud", type="primary", use_container_width=True):
        if not selected_stores:
            st.error("Vælg mindst én butikskæde.")
        else:
            with st.spinner("Henter og renser tilbud fra eTilbudsavis.dk..."):
                lat, lng, loc_name = geocode_postal_code(postal_code)
                raw_offers, warnings = fetch_offers_for_stores(
                    store_names=selected_stores,
                    lat=lat,
                    lng=lng,
                    radius_meters=radius_km * 1000,
                )
                cleaned = filter_food_offers(
                    raw_offers,
                    include_other_food=include_other_food,
                )
                st.session_state["offers"] = cleaned
                st.session_state["offer_warnings"] = warnings
                st.session_state["location_label"] = loc_name
                st.success(
                    f"Hentede {len(cleaned)} rensede madtilbud omkring {loc_name}!"
                )

    st.divider()
    st.subheader("👨‍👩‍👧‍👦 Husstand & Præferencer")
    col_a, col_b = st.columns(2)
    adults = col_a.number_input("Voksne", min_value=1, max_value=10, value=2)
    children = col_b.number_input("Børn", min_value=0, max_value=10, value=0)
    leftover_days = st.slider("Antal restedage", min_value=0, max_value=3, value=1)
    max_stores_options: dict[str, int | None] = {
        "Højst 2 butikker (anbefalet)": 2,
        "Højst 1 butik": 1,
        "Højst 3 butikker": 3,
        "Alle valgte butikker (billigst)": None,
    }
    max_stores_label = st.selectbox(
        "Saml indkøb i antal butikker",
        options=list(max_stores_options.keys()),
        index=0,
        help="Saml så vidt muligt hele ugens indkøb i få butikker, så du ikke skal køre i 5 butikker for at spare 2 kr.",
    )
    max_stores = max_stores_options[max_stores_label]
    preferences = st.text_input(
        "Allergier eller ønsker (valgfrit)",
        placeholder="F.eks. hjertevenligt, uden fisk, maks 30 min på hverdage...",
    )


# --- HOVEDINDHOLD ---
offers: list[Offer] = st.session_state["offers"]
warnings: list[str] = st.session_state["offer_warnings"]

for w in warnings:
    st.warning(w)

tab_plan, tab_shop, tab_offers = st.tabs(
    ["📅 Ugens Madplan", "🛍️ Indkøbsliste", f"🏷️ Hentede Tilbud ({len(offers)})"]
)

with tab_plan:
    active_offers = [o for o in offers if o.selected]

    col_btn, col_info = st.columns([1, 2])
    with col_btn:
        generate_clicked = st.button(
            "2. Generér madplan med Gemini",
            type="primary",
            disabled=(len(active_offers) == 0),
            use_container_width=True,
        )
    with col_info:
        if not offers:
            st.info("Start med at trykke **1. Hent ugens tilbud** i sidepanelet til venstre.")
        else:
            st.info(
                f"Klar med **{len(active_offers)}** udvalgte madtilbud fra "
                f"**{st.session_state['location_label']}**."
            )

    if generate_clicked:
        if not api_key.strip():
            st.error("Indtast din Gemini API-nøgle i sidepanelet først.")
        else:
            with st.spinner("Gemini planlægger ugens madplan og indkøbsliste..."):
                try:
                    plan = generate_weekly_plan(
                        api_key=api_key,
                        offers=active_offers,
                        adults=int(adults),
                        children=int(children),
                        leftover_days=int(leftover_days),
                        preferences=preferences,
                        max_stores=max_stores,
                        model_name=DEFAULT_MODEL,
                    )
                    st.session_state["meal_plan"] = plan
                    st.rerun()
                except Exception as exc:
                    st.error(f"Fejl under generering af madplan: {exc}")

    plan: WeeklyMealPlan | None = st.session_state["meal_plan"]
    if plan is not None:
        st.success(plan.summary)
        st.caption(f"💡 {plan.estimated_offer_savings_note}")

        for idx, day in enumerate(plan.days):
            with st.container(border=True):
                header_col, action_col = st.columns([3, 1])
                badge = "♻️ Restedag" if day.is_leftover_day else f"⏱️ {day.prep_time_minutes} min"
                header_col.subheader(f"{day.day}: {day.dish_name}  ·  {badge}")
                header_col.write(day.description)

                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown("**Tilbudsvarer:**")
                    for item in day.offer_ingredients or ["Ingen specifikke"]:
                        st.markdown(f"- {item}")
                with c2:
                    st.markdown("**Øvrige varer:**")
                    for item in day.extra_ingredients or ["Ingen"]:
                        st.markdown(f"- {item}")
                with c3:
                    st.markdown("**Basisvarer:**")
                    for item in day.pantry_staples or ["Standard krydderier"]:
                        st.markdown(f"- {item}")

                if day.recipe_steps:
                    with st.expander("Vis tilberedning"):
                        for step_i, step in enumerate(day.recipe_steps, start=1):
                            st.markdown(f"{step_i}. {step}")

                with action_col:
                    if st.button("🔄 Udskift ret", key=f"replace_{idx}", use_container_width=True):
                        if not api_key.strip():
                            st.error("Indtast Gemini API-nøgle i sidepanelet.")
                        else:
                            with st.spinner(f"Finder ny ret til {day.day}..."):
                                try:
                                    new_day = replace_single_day(
                                        api_key=api_key,
                                        current_plan=plan,
                                        day_name=day.day,
                                        offers=active_offers,
                                        adults=int(adults),
                                        children=int(children),
                                        preferences=preferences,
                                        max_stores=max_stores,
                                        model_name=DEFAULT_MODEL,
                                    )
                                    plan.days[idx] = new_day
                                    st.session_state["meal_plan"] = plan
                                    st.rerun()
                                except Exception as exc:
                                    st.error(f"Kunne ikke udskifte ret: {exc}")

with tab_shop:
    plan = st.session_state["meal_plan"]
    if plan is None:
        st.info("Generér en madplan først for at se den samlede indkøbsliste.")
    else:
        st.download_button(
            label="⬇️ Download madplan & indkøbsliste (.md)",
            data=_plan_to_markdown(plan),
            file_name="ugens_madplan.md",
            mime="text/markdown",
        )
        st.divider()

        if plan.shopping_list_by_store:
            st.subheader("Tilbudsvarer opdelt pr. butik")
            cols = st.columns(min(len(plan.shopping_list_by_store), 3))
            for idx, store_group in enumerate(plan.shopping_list_by_store):
                with cols[idx % len(cols)]:
                    with st.container(border=True):
                        st.markdown(f"### 🏬 {store_group.store_name}")
                        for item_i, item in enumerate(store_group.items):
                            st.checkbox(
                                item,
                                key=f"shop_{idx}_{item_i}_{item}",
                            )

        col_extra, col_pantry = st.columns(2)
        with col_extra:
            with st.container(border=True):
                st.markdown("### 🧺 Øvrige indkøb (valgfri butik)")
                if plan.extra_items_to_buy:
                    for i, item in enumerate(plan.extra_items_to_buy):
                        st.checkbox(item, key=f"extra_{i}_{item}")
                else:
                    st.write("Ingen øvrige indkøb.")

        with col_pantry:
            with st.container(border=True):
                st.markdown("### 🧂 Basisvarer fra køkkenskabet")
                if plan.pantry_staples_needed:
                    for item in plan.pantry_staples_needed:
                        st.markdown(f"- {item}")
                else:
                    st.write("Ingen angivet.")

with tab_offers:
    if not offers:
        st.info("Ingen tilbud hentet endnu. Tryk på **1. Hent ugens tilbud** i sidepanelet.")
    else:
        st.write(
            "Her kan du gennemse de rensede tilbud og evt. fjerne flueben fra varer, "
            "du ikke ønsker med i madplanen."
        )
        df = pd.DataFrame(
            [
                {
                    "Brug": o.selected,
                    "Butik": o.store_name,
                    "Kategori": o.category,
                    "Vare": o.heading,
                    "Mængde": o.unit_size,
                    "Pris (kr.)": o.price,
                }
                for o in offers
            ]
        )
        edited_df = st.data_editor(
            df,
            hide_index=True,
            use_container_width=True,
            disabled=["Butik", "Kategori", "Vare", "Mængde", "Pris (kr.)"],
        )
        for idx, is_selected in enumerate(edited_df["Brug"].tolist()):
            offers[idx].selected = bool(is_selected)

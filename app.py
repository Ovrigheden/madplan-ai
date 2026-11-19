"""Streamlit-webapp til AI-madplan baseret på ugens tilbud fra eTilbudsavis.dk."""

from __future__ import annotations

import hashlib
import html
import os

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from src.filter import filter_food_offers
from src.images import generate_dish_image, generate_plan_images
from src.models import Offer, WeeklyMealPlan
from src.offers import (
    ALL_STORES,
    DEFAULT_POSTAL_CODE,
    DEFAULT_STORES,
    fetch_offers_for_stores,
    geocode_postal_code,
)
from src.pdf_export import build_direct_print_html, build_meal_plan_pdf
from src.planner import DEFAULT_MODEL, generate_weekly_plan, replace_single_day
from src.storage import (
    LoadedPlan,
    PlanStorage,
    SavedPlanMeta,
    create_plan_storage,
    format_danish_timestamp,
    make_extra_item_id,
    make_store_item_id,
)

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


def _inject_custom_css() -> None:
    """Tilføjer nordisk Stil A-styling med bløde kort, afrundede fotos og pille-badges."""
    st.markdown(
        """
        <style>
        [data-testid='InputInstructions'] { display: none !important; }
        [data-testid="stImage"] img { border-radius: 12px !important; object-fit: cover; }
        .stButton > button, .stDownloadButton > button {
            border-radius: 10px !important;
            font-weight: 600 !important;
        }
        [data-testid="stSidebar"] { border-right: 1px solid #E6E0D6; }
        .hero-banner {
            background: linear-gradient(135deg, #FFFFFF 0%, #F4F0E8 100%);
            border: 1px solid #E6E0D6;
            border-radius: 16px;
            padding: 1.15rem 1.4rem;
            margin-bottom: 1rem;
            box-shadow: 0 2px 10px rgba(31, 41, 51, 0.04);
        }
        .hero-title {
            margin: 0;
            font-size: 1.75rem;
            font-weight: 700;
            color: #1F2933;
            letter-spacing: -0.02em;
        }
        .hero-sub {
            margin: 0.25rem 0 0 0;
            font-size: 0.95rem;
            color: #52606D;
        }
        .summary-card {
            background: #FFFFFF;
            border: 1px solid #E6E0D6;
            border-left: 4px solid #2E6F40;
            border-radius: 12px;
            padding: 0.95rem 1.15rem;
            margin: 0.75rem 0 1rem 0;
            box-shadow: 0 1px 6px rgba(31, 41, 51, 0.03);
            color: #1F2933;
        }
        .summary-note {
            margin-top: 0.4rem;
            font-size: 0.88rem;
            color: #52606D;
        }
        .pill-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.4rem;
            align-items: center;
            margin-bottom: 0.35rem;
        }
        .pill {
            display: inline-block;
            padding: 0.18rem 0.65rem;
            border-radius: 999px;
            font-size: 0.76rem;
            font-weight: 600;
            letter-spacing: 0.02em;
        }
        .pill-day { background: #E6F2EA; color: #215330; text-transform: uppercase; }
        .pill-time { background: #EFEAE1; color: #4A5560; }
        .pill-leftover { background: #FEF3C7; color: #92400E; }
        .dish-title {
            margin: 0.15rem 0 0.45rem 0;
            font-size: 1.22rem;
            font-weight: 700;
            color: #1F2933;
            line-height: 1.25;
        }
        .dish-desc {
            margin: 0;
            font-size: 0.94rem;
            color: #4A5560;
            line-height: 1.45;
        }
        .login-brand {
            text-align: center;
            padding: 0.5rem 0 0.75rem 0;
        }
        .login-brand h2 {
            margin: 0.2rem 0 0.25rem 0;
            font-size: 1.45rem;
            color: #1F2933;
        }
        .login-brand p {
            margin: 0;
            font-size: 0.9rem;
            color: #52606D;
        }
        .progress-label {
            font-weight: 600;
            color: #1F2933;
            margin-bottom: 0.25rem;
        }
        .active-plan-bar {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            align-items: center;
            margin: 0.35rem 0 0.65rem 0;
        }
        .pill-active {
            background: #2E6F40;
            color: #FFFFFF;
        }
        .archive-summary {
            margin: 0.35rem 0 0 0;
            font-size: 0.9rem;
            color: #52606D;
            line-height: 1.4;
        }
        .archive-dishes {
            margin: 0.2rem 0 0 0;
            font-size: 0.92rem;
            font-weight: 600;
            color: #215330;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _require_password_if_configured() -> None:
    """Kræver adgangskode hvis APP_PASSWORD er sat i .env eller Streamlit Secrets."""
    expected = _get_secret("APP_PASSWORD").strip()
    if not expected:
        return
    if st.session_state.get("authenticated"):
        return

    _, mid_col, _ = st.columns([1, 1.35, 1])
    with mid_col:
        with st.container(border=True):
            st.markdown(
                '<div class="login-brand">'
                '<div style="font-size: 2rem;">🛒</div>'
                "<h2>Madplan AI</h2>"
                "<p>Log ind for at se ugens tilbud og madplan</p>"
                "</div>",
                unsafe_allow_html=True,
            )
            pwd = st.text_input("Adgangskode", type="password")
            if st.button("Log ind", type="primary", use_container_width=True):
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
    if "meal_images" not in st.session_state:
        st.session_state["meal_images"] = {}
    if "image_warnings" not in st.session_state:
        st.session_state["image_warnings"] = []
    if "pdf_include_recipes" not in st.session_state:
        st.session_state["pdf_include_recipes"] = True
    if "pdf_include_images" not in st.session_state:
        st.session_state["pdf_include_images"] = True
    if "storage_initialized" not in st.session_state:
        st.session_state["storage_initialized"] = False
    if "active_plan_id" not in st.session_state:
        st.session_state["active_plan_id"] = None
    if "active_plan_title" not in st.session_state:
        st.session_state["active_plan_title"] = ""
    if "checked_items" not in st.session_state:
        st.session_state["checked_items"] = set()
    if "saved_plans_index" not in st.session_state:
        st.session_state["saved_plans_index"] = []
    if "confirm_delete_plan_id" not in st.session_state:
        st.session_state["confirm_delete_plan_id"] = None
    if "storage_error" not in st.session_state:
        st.session_state["storage_error"] = None
    if "save_notice" not in st.session_state:
        st.session_state["save_notice"] = None


@st.cache_resource
def _get_storage_and_warning() -> tuple[PlanStorage, str | None]:
    """Opretter og cacher lagringsklient (Firebase Firestore eller lokalt lager)."""
    secrets_mapping: dict[str, object] | None = None
    try:
        secrets_mapping = {key: st.secrets[key] for key in st.secrets.keys()}
    except Exception:
        secrets_mapping = None
    return create_plan_storage(secrets_mapping)


def _clear_shopping_widget_keys() -> None:
    """Fjerner indkøbs-widget-keys så checkboxes læser frisk fra checked_items."""
    for key in [k for k in st.session_state.keys() if str(k).startswith("chk_")]:
        del st.session_state[key]


def _apply_loaded_plan(loaded: LoadedPlan | None) -> None:
    """Opdaterer session state med en indlæst madplan (eller nulstiller hvis None)."""
    _clear_shopping_widget_keys()
    st.session_state["image_warnings"] = []
    if loaded is None:
        st.session_state["meal_plan"] = None
        st.session_state["meal_images"] = {}
        st.session_state["checked_items"] = set()
        st.session_state["active_plan_id"] = None
        st.session_state["active_plan_title"] = ""
        return

    st.session_state["meal_plan"] = loaded.plan
    st.session_state["meal_images"] = dict(loaded.images)
    st.session_state["checked_items"] = set(loaded.checked_items)
    st.session_state["active_plan_id"] = loaded.meta.id
    st.session_state["active_plan_title"] = loaded.meta.title


def _refresh_from_storage(
    storage: PlanStorage,
    *,
    load_active: bool = True,
    target_plan_id: str | None = None,
) -> None:
    """Henter arkivliste og evt. aktiv madplan fra skylager/lokalt lager."""
    try:
        plans_index = storage.list_plans()
        st.session_state["saved_plans_index"] = plans_index

        if load_active:
            desired_id = target_plan_id or storage.get_active_plan_id()
            loaded = storage.load_plan(desired_id) if desired_id else None
            if loaded is None and plans_index:
                fallback_id = plans_index[0].id
                loaded = storage.load_plan(fallback_id)
                if loaded is not None:
                    storage.set_active_plan_id(fallback_id)
            _apply_loaded_plan(loaded)

        st.session_state["storage_error"] = None
    except Exception as exc:
        st.session_state["storage_error"] = (
            f"Kunne ikke hente gemte madplaner fra {storage.backend_label}: {exc}"
        )


def _sync_local_checked_count(plan_id: str | None, checked_count: int) -> None:
    """Opdaterer checked_count i den cachede arkivliste i session_state."""
    if not plan_id:
        return
    for meta in st.session_state["saved_plans_index"]:
        if meta.id == plan_id:
            meta.checked_count = checked_count
            break


def _on_toggle_shopping_item(
    storage: PlanStorage,
    plan_id: str | None,
    item_id: str,
    widget_key: str,
) -> None:
    """Gemmer afkrydsning på indkøbslisten med det samme i session state og lager."""
    checked_set: set[str] = set(st.session_state.get("checked_items") or set())
    if st.session_state.get(widget_key):
        checked_set.add(item_id)
    else:
        checked_set.discard(item_id)
    st.session_state["checked_items"] = checked_set

    if not plan_id:
        return

    try:
        storage.update_checked_items(plan_id, checked_set)
        _sync_local_checked_count(plan_id, len(checked_set))
        st.session_state["storage_error"] = None
    except Exception as exc:
        st.session_state["storage_error"] = (
            f"Afkrydsning blev gemt lokalt i browseren, men kunne ikke synkroniseres til {storage.backend_label}: {exc}"
        )


def _shopping_widget_key(plan_id: str | None, item_id: str) -> str:
    """Danner en unik Streamlit widget-nøgle bundet til plan_id og vare-ID."""
    digest = hashlib.md5(item_id.encode("utf-8")).hexdigest()[:12]
    return f"chk_{plan_id or 'tmp'}_{digest}"


def _sync_checkbox(state_key: str, widget_key: str) -> None:
    st.session_state[state_key] = bool(st.session_state[widget_key])


def _render_export_bar(plan: WeeklyMealPlan, key_prefix: str) -> None:
    """Viser boks med valg til PDF-udskrift samt download-knapper til PDF og Markdown."""
    recipes_key = f"{key_prefix}_pdf_recipes"
    images_key = f"{key_prefix}_pdf_images"
    st.session_state[recipes_key] = st.session_state["pdf_include_recipes"]
    st.session_state[images_key] = st.session_state["pdf_include_images"]

    with st.container(border=True):
        c_opt1, c_opt2 = st.columns(2)
        with c_opt1:
            include_recipes = st.checkbox(
                "Medtag tilberedningstrin (opskrifter) i udskrift / PDF",
                key=recipes_key,
                on_change=_sync_checkbox,
                args=("pdf_include_recipes", recipes_key),
            )
        with c_opt2:
            include_images = st.checkbox(
                "Medtag madfotos i udskrift / PDF",
                key=images_key,
                on_change=_sync_checkbox,
                args=("pdf_include_images", images_key),
            )

        col_print, col_pdf, col_md = st.columns([1.4, 1.3, 1.0])
        with col_print:
            components.html(
                build_direct_print_html(
                    plan,
                    include_recipes=include_recipes,
                    meal_images=st.session_state["meal_images"],
                    include_images=include_images,
                ),
                height=42,
                scrolling=False,
            )
        with col_pdf:
            st.download_button(
                label="📄 Download PDF (A4)",
                data=build_meal_plan_pdf(
                    plan,
                    include_recipes=include_recipes,
                    meal_images=st.session_state["meal_images"],
                    include_images=include_images,
                ),
                file_name="ugens_madplan_og_indkoeb.pdf",
                mime="application/pdf",
                use_container_width=True,
                key=f"{key_prefix}_dl_pdf",
            )
        with col_md:
            st.download_button(
                label="⬇️ Tekst (.md)",
                data=_plan_to_markdown(plan),
                file_name="ugens_madplan.md",
                mime="text/markdown",
                use_container_width=True,
                key=f"{key_prefix}_dl_md",
            )
        st.caption(
            "💡 Tryk 'Udskriv direkte' for at åbne browserens printvindue uden at downloade PDF. "
            "Indkøbslisten starter automatisk på sin egen side."
        )


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
_inject_custom_css()
_require_password_if_configured()

storage, storage_init_warning = _get_storage_and_warning()
if not st.session_state["storage_initialized"]:
    _refresh_from_storage(storage, load_active=True)
    st.session_state["storage_initialized"] = True

st.markdown(
    '<div class="hero-banner">'
    '<h1 class="hero-title">🛒 Madplan AI – Ugens Tilbud</h1>'
    '<p class="hero-sub">Aktuelle lokale tilbud fra eTilbudsavis.dk forvandlet til hjertevenlige madplaner og indkøbslister</p>'
    "</div>",
    unsafe_allow_html=True,
)

# --- SIDEPANEL ---
with st.sidebar:
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

    st.divider()
    env_key = _get_secret("GEMINI_API_KEY")
    if env_key == "din_gemini_api_noegle_her":
        env_key = ""

    with st.expander("⚙️ Avanceret & API", expanded=not bool(env_key)):
        if env_key:
            st.caption("🔒 Gemini API-nøgle er indlæst sikkert på serveren.")
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
        st.caption("🤖 Tekstmodel: Gemini 3.8 Flash")
        st.caption("📸 Billedmodel: Gemini 3.1 Flash Image")
        if storage.backend_name == "firebase":
            st.caption("☁️ Skylager: Firebase Firestore")
        else:
            st.caption("💾 Lager: Lokalt (Firebase ej tilsluttet)")


# --- HOVEDINDHOLD ---
offers: list[Offer] = st.session_state["offers"]
warnings: list[str] = st.session_state["offer_warnings"]
saved_plans: list[SavedPlanMeta] = st.session_state["saved_plans_index"]

for w in warnings:
    st.warning(w)

tab_plan, tab_shop, tab_archive, tab_offers = st.tabs(
    [
        "📅 Ugens Madplan",
        "🛍️ Indkøbsliste",
        f"📚 Gemte planer ({len(saved_plans)})",
        f"🏷️ Hentede Tilbud ({len(offers)})",
    ]
)

with tab_plan:
    if st.session_state.get("save_notice"):
        st.success(st.session_state["save_notice"])
        st.session_state["save_notice"] = None
    if storage_init_warning:
        st.warning(storage_init_warning)
    if st.session_state.get("storage_error"):
        st.warning(st.session_state["storage_error"])

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
                    with st.spinner("Genererer madfotos til ugens retter..."):
                        images, img_warnings = generate_plan_images(
                            api_key=api_key,
                            plan=plan,
                        )
                    st.session_state["meal_images"] = images
                    st.session_state["image_warnings"] = img_warnings
                    _clear_shopping_widget_keys()
                    st.session_state["checked_items"] = set()
                    st.session_state["confirm_delete_plan_id"] = None

                    try:
                        saved_meta = storage.save_new_plan(
                            plan=plan,
                            images=images,
                            location_label=st.session_state["location_label"],
                        )
                        st.session_state["active_plan_id"] = saved_meta.id
                        st.session_state["active_plan_title"] = saved_meta.title
                        st.session_state["saved_plans_index"] = storage.list_plans()
                        st.session_state["storage_error"] = None
                        st.session_state["save_notice"] = (
                            "☁️ Madplanen er automatisk gemt i Firebase Firestore."
                            if storage.backend_name == "firebase"
                            else "💾 Madplanen er automatisk gemt lokalt."
                        )
                    except Exception as save_exc:
                        st.session_state["active_plan_id"] = None
                        st.session_state["active_plan_title"] = "Ikke gemt i skyen"
                        st.session_state["storage_error"] = (
                            f"Madplanen blev genereret, men kunne ikke gemmes i {storage.backend_label}: {save_exc}"
                        )
                    st.rerun()
                except Exception as exc:
                    st.error(f"Fejl under generering af madplan: {exc}")

    plan: WeeklyMealPlan | None = st.session_state["meal_plan"]
    if plan is not None:
        backend_badge = (
            "☁️ Gemt i Firebase Firestore"
            if storage.backend_name == "firebase"
            else "💾 Gemt lokalt"
        )
        plan_title_display = st.session_state.get("active_plan_title") or "Aktiv ugeplan"
        st.markdown(
            '<div class="active-plan-bar">'
            f'<span class="pill pill-active">⭐ {html.escape(plan_title_display, quote=True)}</span>'
            f'<span class="pill pill-time">{html.escape(backend_badge, quote=True)}</span>'
            "</div>",
            unsafe_allow_html=True,
        )

        m1, m2, m3 = st.columns(3)
        m1.metric("🏬 Brugte butikker", len(plan.shopping_list_by_store))
        if active_offers:
            m2.metric("🏷️ Udvalgte tilbud", len(active_offers))
        else:
            plan_offer_count = sum(len(d.offer_ingredients) for d in plan.days)
            m2.metric("🏷️ Tilbudsvarer i planen", plan_offer_count)
        m3.metric(
            "♻️ Restedage",
            sum(1 for d in plan.days if d.is_leftover_day),
        )

        st.markdown(
            '<div class="summary-card">'
            f'<div><strong>Ugens overblik:</strong> {html.escape(plan.summary, quote=True)}</div>'
            f'<div class="summary-note">💡 {html.escape(plan.estimated_offer_savings_note, quote=True)}</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        _render_export_bar(plan, key_prefix="plan")

        for img_w in st.session_state["image_warnings"]:
            st.warning(img_w)

        for idx, day in enumerate(plan.days):
            with st.container(border=True):
                img_bytes = st.session_state["meal_images"].get(idx)
                if img_bytes:
                    img_col, header_col, action_col = st.columns([1.2, 2.8, 1])
                    img_col.image(img_bytes, use_container_width=True)
                else:
                    header_col, action_col = st.columns([3, 1])

                status_pill = (
                    '<span class="pill pill-leftover">♻️ Restedag</span>'
                    if day.is_leftover_day
                    else f'<span class="pill pill-time">⏱️ {day.prep_time_minutes} min</span>'
                )
                header_col.markdown(
                    '<div class="pill-row">'
                    f'<span class="pill pill-day">{html.escape(day.day, quote=True)}</span>'
                    f"{status_pill}"
                    "</div>"
                    f'<h3 class="dish-title">{html.escape(day.dish_name, quote=True)}</h3>'
                    f'<p class="dish-desc">{html.escape(day.description, quote=True)}</p>',
                    unsafe_allow_html=True,
                )

                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown("**🟢 Tilbudsvarer**")
                    for item in day.offer_ingredients or ["Ingen specifikke"]:
                        st.markdown(f"- {item}")
                with c2:
                    st.markdown("**🧺 Øvrige varer**")
                    for item in day.extra_ingredients or ["Ingen"]:
                        st.markdown(f"- {item}")
                with c3:
                    st.markdown("**🧂 Basisvarer**")
                    for item in day.pantry_staples or ["Standard krydderier"]:
                        st.markdown(f"- {item}")

                if day.recipe_steps:
                    with st.expander("📖 Vis tilberedning"):
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
                                    try:
                                        st.session_state["meal_images"][idx] = generate_dish_image(
                                            api_key=api_key,
                                            day=new_day,
                                        )
                                        st.session_state["image_warnings"] = [
                                            w
                                            for w in st.session_state["image_warnings"]
                                            if not w.startswith(f"{new_day.day}:")
                                        ]
                                    except Exception as img_exc:
                                        st.session_state["meal_images"].pop(idx, None)
                                        st.session_state["image_warnings"].append(
                                            f"{new_day.day}: Kunne ikke generere nyt foto ({img_exc})"
                                        )

                                    active_id = st.session_state.get("active_plan_id")
                                    if active_id:
                                        try:
                                            storage.update_plan_and_day_image(
                                                plan_id=active_id,
                                                plan=plan,
                                                day_idx=idx,
                                                image_bytes=st.session_state["meal_images"].get(idx),
                                            )
                                            st.session_state["saved_plans_index"] = storage.list_plans()
                                            st.session_state["storage_error"] = None
                                            st.session_state["save_notice"] = (
                                                f"☁️ {new_day.day}s nye ret er opdateret i Firebase Firestore."
                                                if storage.backend_name == "firebase"
                                                else f"💾 {new_day.day}s nye ret er opdateret lokalt."
                                            )
                                        except Exception as save_exc:
                                            st.session_state["storage_error"] = (
                                                f"Kunne ikke opdatere den gemte plan i {storage.backend_label}: {save_exc}"
                                            )
                                    st.rerun()
                                except Exception as exc:
                                    st.error(f"Kunne ikke udskifte ret: {exc}")

with tab_shop:
    plan = st.session_state["meal_plan"]
    if plan is None:
        st.info("Generér en madplan først (eller åbn en fra **📚 Gemte planer**) for at se indkøbslisten.")
    else:
        _render_export_bar(plan, key_prefix="shop")
        progress_slot = st.empty()
        active_plan_id: str | None = st.session_state.get("active_plan_id")
        checked_items_set: set[str] = st.session_state["checked_items"]
        total_items = 0
        checked_count = 0

        if plan.shopping_list_by_store:
            st.subheader("Tilbudsvarer opdelt pr. butik")
            cols = st.columns(min(len(plan.shopping_list_by_store), 3))
            for idx, store_group in enumerate(plan.shopping_list_by_store):
                with cols[idx % len(cols)]:
                    with st.container(border=True):
                        st.markdown(f"### 🏬 {store_group.store_name}")
                        for item_i, item in enumerate(store_group.items):
                            total_items += 1
                            item_id = make_store_item_id(store_group.store_name, item_i, item)
                            wkey = _shopping_widget_key(active_plan_id, item_id)
                            if st.checkbox(
                                item,
                                value=(item_id in checked_items_set),
                                key=wkey,
                                on_change=_on_toggle_shopping_item,
                                args=(storage, active_plan_id, item_id, wkey),
                            ):
                                checked_count += 1

        col_extra, col_pantry = st.columns(2)
        with col_extra:
            with st.container(border=True):
                st.markdown("### 🧺 Øvrige indkøb (valgfri butik)")
                if plan.extra_items_to_buy:
                    for i, item in enumerate(plan.extra_items_to_buy):
                        total_items += 1
                        item_id = make_extra_item_id(i, item)
                        wkey = _shopping_widget_key(active_plan_id, item_id)
                        if st.checkbox(
                            item,
                            value=(item_id in checked_items_set),
                            key=wkey,
                            on_change=_on_toggle_shopping_item,
                            args=(storage, active_plan_id, item_id, wkey),
                        ):
                            checked_count += 1
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

        if total_items > 0:
            with progress_slot.container(border=True):
                pct = checked_count / total_items
                if checked_count == total_items:
                    st.markdown(
                        f'<div class="progress-label">🎉 Alle {total_items} varer er lagt i kurven!</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div class="progress-label">🛍️ Lagt i kurven: {checked_count} af {total_items} varer</div>',
                        unsafe_allow_html=True,
                    )
                st.progress(pct)

                btn_col1, btn_col2, _ = st.columns([1.15, 1.15, 2.7])
                with btn_col1:
                    if st.button("🔄 Synk fra skyen", key="btn_sync_shop", use_container_width=True):
                        _refresh_from_storage(
                            storage,
                            load_active=True,
                            target_plan_id=active_plan_id,
                        )
                        st.rerun()
                with btn_col2:
                    if checked_count > 0 and st.button(
                        "🧹 Nulstil kurv",
                        key="btn_reset_shop",
                        use_container_width=True,
                    ):
                        st.session_state["checked_items"] = set()
                        _clear_shopping_widget_keys()
                        if active_plan_id:
                            try:
                                storage.update_checked_items(active_plan_id, set())
                                _sync_local_checked_count(active_plan_id, 0)
                                st.session_state["storage_error"] = None
                            except Exception as exc:
                                st.session_state["storage_error"] = (
                                    f"Kunne ikke nulstille kurven i {storage.backend_label}: {exc}"
                                )
                        st.rerun()

with tab_archive:
    with st.container(border=True):
        col_st, col_rf = st.columns([3.2, 1.0])
        with col_st:
            if storage.backend_name == "firebase":
                st.markdown(
                    "**☁️ Synkroniseret med Firebase Firestore** — Dine madplaner, fotos og "
                    "afkrydsninger gemmes fast i skyen på tværs af pc og mobil."
                )
            else:
                st.markdown(
                    "**💾 Gemmes lokalt på denne maskine** — Tilføj Firebase i Secrets for "
                    "at synkronisere dine gemte planer og indkøbsliste mellem pc og mobil."
                )
        with col_rf:
            if st.button("🔄 Opdatér arkiv", key="btn_refresh_archive", use_container_width=True):
                _refresh_from_storage(storage, load_active=False)
                st.rerun()

    if st.session_state.get("storage_error"):
        st.warning(st.session_state["storage_error"])

    archive_plans: list[SavedPlanMeta] = st.session_state["saved_plans_index"]
    if not archive_plans:
        st.info(
            "Ingen gemte madplaner endnu. Hent tilbud og tryk **2. Generér madplan med Gemini** "
            "for at oprette og gemme din første ugeplan."
        )
    else:
        current_active_id = st.session_state.get("active_plan_id")
        confirm_del_id = st.session_state.get("confirm_delete_plan_id")

        for meta in archive_plans:
            is_active = meta.id == current_active_id
            ts_label = format_danish_timestamp(meta.created_at)

            pills: list[str] = []
            if is_active:
                pills.append('<span class="pill pill-active">⭐ Aktiv ugeplan</span>')
            if ts_label:
                pills.append(
                    f'<span class="pill pill-time">🕒 {html.escape(ts_label, quote=True)}</span>'
                )
            pills.append(
                f'<span class="pill pill-day">🏬 {meta.store_count} butikker</span>'
            )
            pills.append(
                f'<span class="pill pill-time">📸 {meta.image_count} fotos</span>'
            )
            pills.append(
                f'<span class="pill pill-time">🛍️ {meta.checked_count}/{meta.total_shop_items} i kurven</span>'
            )

            with st.container(border=True):
                info_col, btn_col = st.columns([3.3, 1.1])
                with info_col:
                    dish_html = (
                        f'<div class="archive-dishes">🍽️ {html.escape(meta.dish_preview, quote=True)}</div>'
                        if meta.dish_preview
                        else ""
                    )
                    summary_html = (
                        f'<p class="archive-summary">{html.escape(meta.summary, quote=True)}</p>'
                        if meta.summary
                        else ""
                    )
                    st.markdown(
                        '<div class="pill-row">' + "".join(pills) + "</div>"
                        f'<h3 class="dish-title">{html.escape(meta.title, quote=True)}</h3>'
                        f"{dish_html}"
                        f"{summary_html}",
                        unsafe_allow_html=True,
                    )

                with btn_col:
                    if not is_active:
                        if st.button(
                            "📂 Åbn som aktiv",
                            key=f"open_{meta.id}",
                            type="primary",
                            use_container_width=True,
                        ):
                            try:
                                loaded = storage.load_plan(meta.id)
                                if loaded is None:
                                    st.session_state["storage_error"] = (
                                        "Madplanen blev ikke fundet i arkivet."
                                    )
                                    _refresh_from_storage(storage, load_active=False)
                                else:
                                    storage.set_active_plan_id(meta.id)
                                    _apply_loaded_plan(loaded)
                                    st.session_state["confirm_delete_plan_id"] = None
                                    st.session_state["save_notice"] = (
                                        f"📂 Åbnede '{loaded.meta.title}' som aktiv ugeplan."
                                    )
                            except Exception as exc:
                                st.session_state["storage_error"] = (
                                    f"Kunne ikke åbne madplanen: {exc}"
                                )
                            st.rerun()
                    else:
                        if st.button(
                            "🔄 Genindlæs",
                            key=f"reload_{meta.id}",
                            use_container_width=True,
                        ):
                            _refresh_from_storage(
                                storage,
                                load_active=True,
                                target_plan_id=meta.id,
                            )
                            st.session_state["confirm_delete_plan_id"] = None
                            st.rerun()

                    if confirm_del_id == meta.id:
                        if st.button(
                            "⚠️ Bekræft sletning",
                            key=f"confirm_del_{meta.id}",
                            type="primary",
                            use_container_width=True,
                        ):
                            was_active = meta.id == st.session_state.get("active_plan_id")
                            try:
                                next_active_id = storage.delete_plan(meta.id)
                                st.session_state["confirm_delete_plan_id"] = None
                                if was_active:
                                    _refresh_from_storage(
                                        storage,
                                        load_active=True,
                                        target_plan_id=next_active_id,
                                    )
                                else:
                                    _refresh_from_storage(storage, load_active=False)
                            except Exception as exc:
                                st.session_state["storage_error"] = (
                                    f"Kunne ikke slette madplanen fra {storage.backend_label}: {exc}"
                                )
                            st.rerun()
                        if st.button(
                            "Annullér",
                            key=f"cancel_del_{meta.id}",
                            use_container_width=True,
                        ):
                            st.session_state["confirm_delete_plan_id"] = None
                            st.rerun()
                    else:
                        if st.button(
                            "🗑️ Slet",
                            key=f"del_{meta.id}",
                            use_container_width=True,
                        ):
                            st.session_state["confirm_delete_plan_id"] = meta.id
                            st.rerun()

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

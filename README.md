# Madplan AI – Ugens Tilbud

Interaktiv Streamlit-webapp der henter aktuelle dagligvaretilbud fra **eTilbudsavis.dk** og bruger **Google Gemini API** til at lave en ugentlig madplan og indkøbsliste.

## Standardkæder (Herning / 7400)
- REMA 1000
- Netto
- 365discount
- ABC Lavpris
- MENY
- Bilka

## Kør lokalt på Windows

1. *(Valgfrit)* Kopiér `.env.example` til `.env` og indsæt din Gemini API-nøgle (eller indtast nøglen direkte i webappen).
2. Dobbeltklik på `start_app.bat` (starter appen skjult i baggrunden og åbner browseren på `http://localhost:8501`).
3. Dobbeltklik på `stop_app.bat`, når du vil lukke baggrundsserveren igen.

## Brug på mobil (Pixel 10 Pro / Android) via Streamlit Community Cloud

1. Opret et (gerne **privat**) repository på [github.com](https://github.com) og upload projektets filer (`.gitignore` sørger for, at `.venv` og `.env` ikke kommer med).
2. Log ind på [share.streamlit.io](https://share.streamlit.io) med din GitHub-konto og klik **Create app**.
3. Vælg dit repository, branch `main` og main file `app.py`.
4. Klik på **Advanced settings → Secrets** og indsæt:
   ```toml
   GEMINI_API_KEY = "din_gemini_api_noegle_her"
   APP_PASSWORD = "et_valgfrit_kodeord_til_familien"
   ```
5. Når appen er klar, åbner du dens `.streamlit.app`-link i **Chrome** på din Pixel 10 Pro → tryk på **⋮** → **Føj til startskærm**.

## Permanent lagring i skyen med Firebase Firestore (gratis Spark-plan)

For at gemme dine madplaner, madfotos, indkøbsafkrydsninger og arkiv permanent på tværs af pc og mobil:

1. Gå til [console.firebase.google.com](https://console.firebase.google.com/) og opret et nyt projekt (på den gratis **Spark**-plan uden betalingskort).
2. Klik på **Build → Firestore Database → Create database**, vælg **Standard edition**, en europæisk placering (f.eks. `eur3` eller `europe-west1`), og vælg **Start in production mode** (appen tilgår databasen sikkert fra Python-serveren via en Service Account).
3. Klik på tandhjulet **⚙️ Project settings → Service accounts → Generate new private key**, og hent `.json`-filen ned på din pc (del aldrig denne fil og læg den ikke på GitHub).
4. Åbn din app på [share.streamlit.io](https://share.streamlit.io) → **Manage app → Settings → Secrets**, og indsæt hele indholdet af `.json`-filen mellem tre anførselstegn under dine øvrige secrets:
   ```toml
   GEMINI_API_KEY = "din_gemini_api_noegle_her"
   APP_PASSWORD = "et_valgfrit_kodeord_til_familien"

   FIREBASE_SERVICE_ACCOUNT_JSON = '''
   {
     "type": "service_account",
     "project_id": "...",
     ...
   }
   '''
   ```
5. Gem Secrets og tryk **Reboot app**. (Kører du lokalt på pc uden Firebase, falder appen automatisk tilbage på lokalt fil-lager i `data/saved_plans/`.)

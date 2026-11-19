"""Permanent lagring af madplaner, madfotos og indkøbsafkrydsninger (Firebase Firestore + lokal fallback)."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from src.models import WeeklyMealPlan

_SAFE_PLAN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_FIRESTORE_SCOPES = ("https://www.googleapis.com/auth/datastore",)
_MAX_FIRESTORE_IMAGE_BYTES = 650_000
_DEFAULT_LOCAL_DIR = Path(__file__).resolve().parent.parent / "data" / "saved_plans"

_META_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "created_at",
    "updated_at",
    "location_label",
    "summary",
    "dish_preview",
    "store_count",
    "total_shop_items",
    "checked_count",
    "image_count",
)


class SavedPlanMeta(BaseModel):
    """Metadata til arkivoversigt uden tunge plan- og billedbytes."""

    id: str
    title: str
    created_at: str
    updated_at: str
    location_label: str = ""
    summary: str = ""
    dish_preview: str = ""
    store_count: int = 0
    total_shop_items: int = 0
    checked_count: int = 0
    image_count: int = 0


@dataclass
class LoadedPlan:
    """Komplet gemt madplan indlæst fra lager."""

    meta: SavedPlanMeta
    plan: WeeklyMealPlan
    images: dict[int, bytes]
    checked_items: set[str]


def make_store_item_id(store_name: str, item_index: int, item_text: str) -> str:
    """Bygger et stabilt ID for en butiks-indkøbsvare."""
    return f"store:{store_name}|{item_index}|{item_text}"


def make_extra_item_id(item_index: int, item_text: str) -> str:
    """Bygger et stabilt ID for en øvrig indkøbsvare."""
    return f"extra:{item_index}|{item_text}"


def count_total_shop_items(plan: WeeklyMealPlan) -> int:
    """Tæller samlet antal varer der skal købes på indkøbslisten."""
    store_items = sum(len(group.items) for group in plan.shopping_list_by_store)
    return store_items + len(plan.extra_items_to_buy)


def build_dish_preview(plan: WeeklyMealPlan, max_dishes: int = 4) -> str:
    """Samler de første 3-4 unikke retter adskilt af ' · ' til arkivkort."""
    dishes: list[str] = []
    seen: set[str] = set()
    for day in plan.days:
        name = day.dish_name.strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        dishes.append(name)
        if len(dishes) >= max_dishes:
            break
    return " · ".join(dishes)


def _copenhagen_now(now: datetime | None = None) -> datetime:
    """Returnerer aktuel (eller konverteret) tid i dansk tidszone med sikker fallback."""
    try:
        cph_tz = ZoneInfo("Europe/Copenhagen")
    except Exception:
        cph_tz = None

    if now is None:
        return datetime.now(cph_tz) if cph_tz is not None else datetime.now()
    if cph_tz is None:
        return now
    if now.tzinfo is None:
        return now.replace(tzinfo=cph_tz)
    return now.astimezone(cph_tz)


def _utc_now_iso(now: datetime | None = None) -> str:
    """Returnerer ISO-8601 UTC-tidsstempel."""
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def build_default_plan_title(location_label: str, now: datetime | None = None) -> str:
    """Danner dansk standardtitel, f.eks. 'Uge 47 · Herning (19/11)'."""
    dt = _copenhagen_now(now)
    week_num = dt.isocalendar().week
    date_part = dt.strftime("%d/%m")
    loc = location_label.strip()
    if loc:
        return f"Uge {week_num} · {loc} ({date_part})"
    return f"Uge {week_num} ({date_part})"


def format_danish_timestamp(iso_str: str) -> str:
    """Formaterer et ISO-tidsstempel til dansk visning, f.eks. '19/11 kl. 23:15'."""
    raw = (iso_str or "").strip()
    if not raw:
        return ""
    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(normalized)
        dt_local = _copenhagen_now(dt)
        return f"{dt_local.strftime('%d/%m')} kl. {dt_local.strftime('%H:%M')}"
    except Exception:
        return raw


def generate_plan_id(now: datetime | None = None) -> str:
    """Genererer et sikkert unikt plan-ID matchende ^[A-Za-z0-9_-]+$."""
    dt = _copenhagen_now(now)
    iso = dt.isocalendar()
    ts = dt.strftime("%Y%m%d_%H%M%S")
    return f"{iso.year}-W{iso.week:02d}_{ts}_{uuid4().hex[:6]}"


def _validate_plan_id(plan_id: str) -> str:
    """Sikrer at plan-ID ikke kan bruges til path traversal eller ugyldige stier."""
    cleaned = (plan_id or "").strip()
    if not _SAFE_PLAN_ID_RE.match(cleaned):
        raise ValueError(f"Ugyldigt plan-ID: {plan_id!r}")
    return cleaned


def _to_plain_value(value: Any) -> Any:
    """Konverterer Streamlit AttrDict/Mapping rekursivt til almindelige Python-typer."""
    if isinstance(value, Mapping):
        return {str(k): _to_plain_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain_value(v) for v in value]
    return value


def _normalize_and_validate_service_account(
    raw_info: Mapping[str, Any], source_label: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Normaliserer private_key og tjekker påkrævede Firebase Service Account-felter."""
    info = _to_plain_value(raw_info)
    if not isinstance(info, dict):
        return None, f"Firebase-konfiguration i {source_label} skal være et JSON-objekt."

    private_key = info.get("private_key")
    if isinstance(private_key, str) and "\\n" in private_key and "\n" not in private_key:
        info["private_key"] = private_key.replace("\\n", "\n")

    required = ("project_id", "client_email", "private_key")
    missing = [
        key for key in required if not isinstance(info.get(key), str) or not info[key].strip()
    ]
    if missing:
        return (
            None,
            f"Firebase Service Account fra {source_label} mangler påkrævede felter: {', '.join(missing)}.",
        )
    return info, None


def load_firebase_credentials_dict(
    secrets_mapping: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Indlæser Firebase Service Account fra miljøvariabler eller Streamlit Secrets.

    Returnerer (credentials_dict, fejlbesked). Hvis intet er konfigureret,
    returneres (None, None).
    """
    env_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if env_json:
        try:
            parsed = json.loads(env_json)
        except json.JSONDecodeError as exc:
            return None, f"Ugyldig JSON i FIREBASE_SERVICE_ACCOUNT_JSON: {exc}"
        if not isinstance(parsed, Mapping):
            return None, "FIREBASE_SERVICE_ACCOUNT_JSON skal indeholde et JSON-objekt."
        return _normalize_and_validate_service_account(parsed, "FIREBASE_SERVICE_ACCOUNT_JSON")

    env_file = os.getenv("FIREBASE_SERVICE_ACCOUNT_FILE", "").strip()
    if env_file:
        try:
            raw_text = Path(env_file).read_text(encoding="utf-8")
        except OSError as exc:
            return None, f"Kunne ikke læse FIREBASE_SERVICE_ACCOUNT_FILE ({env_file}): {exc}"
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            return None, f"Ugyldig JSON i {env_file}: {exc}"
        if not isinstance(parsed, Mapping):
            return None, f"Filen {env_file} skal indeholde et JSON-objekt."
        return _normalize_and_validate_service_account(parsed, env_file)

    if secrets_mapping:
        if "FIREBASE_SERVICE_ACCOUNT_JSON" in secrets_mapping:
            raw_val = secrets_mapping["FIREBASE_SERVICE_ACCOUNT_JSON"]
            if isinstance(raw_val, Mapping):
                return _normalize_and_validate_service_account(
                    raw_val, "Secrets[FIREBASE_SERVICE_ACCOUNT_JSON]"
                )
            if isinstance(raw_val, str) and raw_val.strip():
                try:
                    parsed = json.loads(raw_val.strip())
                except json.JSONDecodeError as exc:
                    return (
                        None,
                        f"Ugyldig JSON i Secrets[FIREBASE_SERVICE_ACCOUNT_JSON]: {exc}",
                    )
                if not isinstance(parsed, Mapping):
                    return (
                        None,
                        "Secrets[FIREBASE_SERVICE_ACCOUNT_JSON] skal indeholde et JSON-objekt.",
                    )
                return _normalize_and_validate_service_account(
                    parsed, "Secrets[FIREBASE_SERVICE_ACCOUNT_JSON]"
                )

        for section_key in ("firebase_service_account", "firebase"):
            if section_key in secrets_mapping:
                section_val = secrets_mapping[section_key]
                if isinstance(section_val, Mapping) and section_val:
                    return _normalize_and_validate_service_account(
                        section_val, f"Secrets[{section_key}]"
                    )
                if section_val:
                    return None, f"Secrets[{section_key}] skal være en TOML-sektion/tabel."

    return None, None


def load_firebase_database_id(secrets_mapping: Mapping[str, Any] | None = None) -> str:
    """Læser valgfri Firestore database-ID med '(default)' som standard."""
    env_val = os.getenv("FIREBASE_DATABASE_ID", "").strip()
    if env_val:
        return env_val
    if secrets_mapping and "FIREBASE_DATABASE_ID" in secrets_mapping:
        sec_val = str(secrets_mapping["FIREBASE_DATABASE_ID"] or "").strip()
        if sec_val:
            return sec_val
    return "(default)"


def _build_meta_from_plan(
    *,
    plan_id: str,
    title: str,
    created_at: str,
    updated_at: str,
    location_label: str,
    plan: WeeklyMealPlan,
    checked_count: int,
    image_count: int,
) -> SavedPlanMeta:
    """Bygger et ensartet SavedPlanMeta-objekt ud fra madplan og status."""
    return SavedPlanMeta(
        id=plan_id,
        title=title,
        created_at=created_at,
        updated_at=updated_at,
        location_label=location_label.strip(),
        summary=plan.summary,
        dish_preview=build_dish_preview(plan),
        store_count=len(plan.shopping_list_by_store),
        total_shop_items=count_total_shop_items(plan),
        checked_count=checked_count,
        image_count=image_count,
    )


def _meta_from_raw_dict(doc_id: str, data: Mapping[str, Any]) -> SavedPlanMeta:
    """Konverterer et råt dokument-dict til SavedPlanMeta med sikre standardværdier."""
    return SavedPlanMeta(
        id=str(data.get("id") or doc_id),
        title=str(data.get("title") or doc_id),
        created_at=str(data.get("created_at") or ""),
        updated_at=str(data.get("updated_at") or data.get("created_at") or ""),
        location_label=str(data.get("location_label") or ""),
        summary=str(data.get("summary") or ""),
        dish_preview=str(data.get("dish_preview") or ""),
        store_count=int(data.get("store_count") or 0),
        total_shop_items=int(data.get("total_shop_items") or 0),
        checked_count=int(data.get("checked_count") or 0),
        image_count=int(data.get("image_count") or 0),
    )


class PlanStorage(ABC):
    """Fælles interface for permanent lagring af madplaner, fotos og afkrydsninger."""

    backend_name: str
    backend_label: str

    @abstractmethod
    def get_active_plan_id(self) -> str | None:
        """Returnerer ID'et på den aktuelt aktive ugeplan, eller None."""

    @abstractmethod
    def set_active_plan_id(self, plan_id: str | None) -> None:
        """Gemmer hvilket plan-ID der er aktivt."""

    @abstractmethod
    def list_plans(self) -> list[SavedPlanMeta]:
        """Returnerer alle gemte planer sorteret nyeste created_at først."""

    @abstractmethod
    def load_plan(self, plan_id: str) -> LoadedPlan | None:
        """Indlæser en fuld gemt plan inkl. madplan, billeder og afkrydsninger."""

    @abstractmethod
    def save_new_plan(
        self,
        plan: WeeklyMealPlan,
        images: dict[int, bytes],
        location_label: str = "",
        title: str | None = None,
        set_active: bool = True,
    ) -> SavedPlanMeta:
        """Gemmer en ny madplan og tilhørende billeder."""

    @abstractmethod
    def update_plan_and_day_image(
        self,
        plan_id: str,
        plan: WeeklyMealPlan,
        day_idx: int,
        image_bytes: bytes | None,
    ) -> None:
        """Opdaterer madplanen og ét dagsfoto efter udskiftning af ret."""

    @abstractmethod
    def update_checked_items(
        self,
        plan_id: str,
        checked_items: set[str] | list[str],
    ) -> None:
        """Gemmer afkrydsede varer på den angivne madplan."""

    @abstractmethod
    def delete_plan(self, plan_id: str) -> str | None:
        """Sletter en plan og returnerer det nye active_plan_id (hvis relevant)."""


def _encode_fs_value(value: Any) -> dict[str, Any]:
    """Koder en Python-værdi til Firestore REST API v1 Value-format."""
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, (bytes, bytearray)):
        return {"bytesValue": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, Mapping):
        return {
            "mapValue": {
                "fields": {str(k): _encode_fs_value(v) for k, v in value.items()}
            }
        }
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [_encode_fs_value(v) for v in value]}}
    raise TypeError(f"Ikke-understøttet Firestore-værdi: {type(value).__name__}")


def _encode_fs_fields(data: Mapping[str, Any]) -> dict[str, Any]:
    """Koder et felt-dict til Firestore REST API v1 'fields'-map."""
    return {str(k): _encode_fs_value(v) for k, v in data.items()}


def _decode_fs_value(raw: Mapping[str, Any]) -> Any:
    """Dekoder en Firestore REST API v1 Value tilbage til almindelig Python-værdi."""
    if "nullValue" in raw:
        return None
    if "booleanValue" in raw:
        return bool(raw["booleanValue"])
    if "integerValue" in raw:
        return int(raw["integerValue"])
    if "doubleValue" in raw:
        return float(raw["doubleValue"])
    if "stringValue" in raw:
        return str(raw["stringValue"])
    if "timestampValue" in raw:
        return str(raw["timestampValue"])
    if "bytesValue" in raw:
        return base64.b64decode(raw["bytesValue"])
    if "mapValue" in raw:
        fields = (raw.get("mapValue") or {}).get("fields") or {}
        return {str(k): _decode_fs_value(v) for k, v in fields.items()}
    if "arrayValue" in raw:
        values = (raw.get("arrayValue") or {}).get("values") or []
        return [_decode_fs_value(v) for v in values]
    return None


def _decode_fs_doc(doc_json: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Dekoder et Firestore REST-dokument til (doc_id, felter)."""
    doc_id = str(doc_json.get("name", "")).rsplit("/", 1)[-1]
    fields = doc_json.get("fields") or {}
    return doc_id, {str(k): _decode_fs_value(v) for k, v in fields.items()}


class FirestorePlanStorage(PlanStorage):
    """Cloud-lagring i Google Firebase Firestore via officiel REST API v1."""

    backend_name = "firebase"
    backend_label = "Firebase Firestore"

    def __init__(
        self,
        credentials_dict: Mapping[str, Any] | None = None,
        database_id: str = "(default)",
        project_id: str | None = None,
        session: Any | None = None,
        timeout: float = 20.0,
    ) -> None:
        if session is not None:
            self._session = session
            self._project_id = str(
                project_id
                or (credentials_dict or {}).get("project_id")
                or "test-project"
            )
        else:
            if not credentials_dict:
                raise ValueError("Mangler Firebase Service Account-oplysninger.")

            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account

            creds_info = dict(credentials_dict)
            creds = service_account.Credentials.from_service_account_info(
                creds_info,
                scopes=_FIRESTORE_SCOPES,
            )
            self._session = AuthorizedSession(creds)
            self._project_id = str(creds_info["project_id"])

        self._doc_root = f"projects/{self._project_id}/databases/{database_id}/documents"
        self._base_url = f"https://firestore.googleapis.com/v1/{self._doc_root}"
        self._commit_url = f"https://firestore.googleapis.com/v1/{self._doc_root}:commit"
        self._timeout = timeout

    def _raise_for_status(self, resp: Any) -> None:
        if resp.status_code < 400:
            return
        detail = ""
        try:
            body = resp.json()
            if isinstance(body, Mapping):
                err = body.get("error")
                if isinstance(err, Mapping) and err.get("message"):
                    detail = str(err["message"])
                else:
                    detail = str(body)
        except Exception:
            detail = getattr(resp, "text", "") or ""
        raise RuntimeError(
            f"Firestore-fejl (HTTP {resp.status_code}): {detail or 'Ukendt fejl'}"
        )

    def _get_doc(self, rel_path: str) -> tuple[str, dict[str, Any]] | None:
        resp = self._session.get(
            f"{self._base_url}/{rel_path}",
            timeout=self._timeout,
        )
        if resp.status_code == 404:
            return None
        self._raise_for_status(resp)
        return _decode_fs_doc(resp.json())

    def _patch_doc(
        self,
        rel_path: str,
        data: Mapping[str, Any],
        update_fields: Iterable[str] | None = None,
    ) -> None:
        params = (
            [("updateMask.fieldPaths", str(field)) for field in update_fields]
            if update_fields is not None
            else None
        )
        resp = self._session.patch(
            f"{self._base_url}/{rel_path}",
            params=params,
            json={"fields": _encode_fs_fields(data)},
            timeout=self._timeout,
        )
        self._raise_for_status(resp)

    def _delete_doc(self, rel_path: str) -> None:
        resp = self._session.delete(
            f"{self._base_url}/{rel_path}",
            timeout=self._timeout,
        )
        if resp.status_code == 404:
            return
        self._raise_for_status(resp)

    def _list_docs(
        self,
        rel_collection: str,
        field_paths: Iterable[str] | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        docs: list[tuple[str, dict[str, Any]]] = []
        page_token: str | None = None
        mask_params = (
            [("mask.fieldPaths", str(field)) for field in field_paths]
            if field_paths is not None
            else []
        )
        while True:
            params: list[tuple[str, str]] = [("pageSize", "100"), *mask_params]
            if page_token:
                params.append(("pageToken", page_token))
            resp = self._session.get(
                f"{self._base_url}/{rel_collection}",
                params=params,
                timeout=self._timeout,
            )
            if resp.status_code == 404:
                return docs
            self._raise_for_status(resp)
            payload = resp.json()
            for raw_doc in payload.get("documents") or []:
                docs.append(_decode_fs_doc(raw_doc))
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return docs

    def _commit_writes(self, writes: list[dict[str, Any]]) -> None:
        if not writes:
            return
        resp = self._session.post(
            self._commit_url,
            json={"writes": writes},
            timeout=self._timeout,
        )
        self._raise_for_status(resp)

    def get_active_plan_id(self) -> str | None:
        found = self._get_doc("app_meta/state")
        if found is None:
            return None
        _, data = found
        active_id = data.get("active_plan_id")
        return str(active_id) if active_id else None

    def set_active_plan_id(self, plan_id: str | None) -> None:
        cleaned = _validate_plan_id(plan_id) if plan_id else None
        self._patch_doc(
            "app_meta/state",
            {
                "active_plan_id": cleaned,
                "updated_at": _utc_now_iso(),
            },
        )

    def list_plans(self) -> list[SavedPlanMeta]:
        docs = self._list_docs("meal_plans", field_paths=_META_FIELDS)
        plans = [_meta_from_raw_dict(doc_id, data) for doc_id, data in docs]
        plans.sort(key=lambda item: item.created_at, reverse=True)
        return plans

    def load_plan(self, plan_id: str) -> LoadedPlan | None:
        pid = _validate_plan_id(plan_id)
        found = self._get_doc(f"meal_plans/{pid}")
        if found is None:
            return None
        _, data = found

        plan = WeeklyMealPlan.model_validate(data["plan_data"])
        checked_items = {str(item) for item in (data.get("checked_items") or [])}

        images: dict[int, bytes] = {}
        for img_doc_id, img_data in self._list_docs(f"meal_plans/{pid}/images"):
            raw_bytes = img_data.get("image_bytes")
            if raw_bytes is None:
                continue
            try:
                day_idx = int(img_data.get("day_idx", img_doc_id))
            except (TypeError, ValueError):
                continue
            images[day_idx] = bytes(raw_bytes)

        meta = _meta_from_raw_dict(pid, data)
        return LoadedPlan(
            meta=meta,
            plan=plan,
            images=images,
            checked_items=checked_items,
        )

    def save_new_plan(
        self,
        plan: WeeklyMealPlan,
        images: dict[int, bytes],
        location_label: str = "",
        title: str | None = None,
        set_active: bool = True,
    ) -> SavedPlanMeta:
        plan_id = generate_plan_id()
        now_iso = _utc_now_iso()
        resolved_title = (title or "").strip() or build_default_plan_title(location_label)

        valid_images = {
            int(day_idx): bytes(raw)
            for day_idx, raw in images.items()
            if raw and len(raw) <= _MAX_FIRESTORE_IMAGE_BYTES
        }
        meta = _build_meta_from_plan(
            plan_id=plan_id,
            title=resolved_title,
            created_at=now_iso,
            updated_at=now_iso,
            location_label=location_label,
            plan=plan,
            checked_count=0,
            image_count=len(valid_images),
        )

        writes: list[dict[str, Any]] = [
            {
                "update": {
                    "name": f"{self._doc_root}/meal_plans/{plan_id}",
                    "fields": _encode_fs_fields(
                        {
                            **meta.model_dump(),
                            "plan_data": plan.model_dump(),
                            "checked_items": [],
                        }
                    ),
                }
            }
        ]
        for day_idx, raw_bytes in valid_images.items():
            writes.append(
                {
                    "update": {
                        "name": f"{self._doc_root}/meal_plans/{plan_id}/images/{day_idx}",
                        "fields": _encode_fs_fields(
                            {
                                "day_idx": day_idx,
                                "image_bytes": raw_bytes,
                                "mime_type": "image/jpeg",
                            }
                        ),
                    }
                }
            )
        if set_active:
            writes.append(
                {
                    "update": {
                        "name": f"{self._doc_root}/app_meta/state",
                        "fields": _encode_fs_fields(
                            {
                                "active_plan_id": plan_id,
                                "updated_at": now_iso,
                            }
                        ),
                    }
                }
            )
        self._commit_writes(writes)
        return meta

    def update_plan_and_day_image(
        self,
        plan_id: str,
        plan: WeeklyMealPlan,
        day_idx: int,
        image_bytes: bytes | None,
    ) -> None:
        pid = _validate_plan_id(plan_id)
        img_rel_path = f"meal_plans/{pid}/images/{int(day_idx)}"

        if image_bytes is not None and 0 < len(image_bytes) <= _MAX_FIRESTORE_IMAGE_BYTES:
            self._patch_doc(
                img_rel_path,
                {
                    "day_idx": int(day_idx),
                    "image_bytes": bytes(image_bytes),
                    "mime_type": "image/jpeg",
                },
            )
        elif image_bytes is None:
            self._delete_doc(img_rel_path)

        image_count = len(self._list_docs(f"meal_plans/{pid}/images", field_paths=("day_idx",)))
        update_payload = {
            "plan_data": plan.model_dump(),
            "summary": plan.summary,
            "dish_preview": build_dish_preview(plan),
            "store_count": len(plan.shopping_list_by_store),
            "total_shop_items": count_total_shop_items(plan),
            "image_count": image_count,
            "updated_at": _utc_now_iso(),
        }
        self._patch_doc(
            f"meal_plans/{pid}",
            update_payload,
            update_fields=update_payload.keys(),
        )

    def update_checked_items(
        self,
        plan_id: str,
        checked_items: Iterable[str],
    ) -> None:
        pid = _validate_plan_id(plan_id)
        items_list = sorted({str(item) for item in checked_items})
        update_payload = {
            "checked_items": items_list,
            "checked_count": len(items_list),
            "updated_at": _utc_now_iso(),
        }
        self._patch_doc(
            f"meal_plans/{pid}",
            update_payload,
            update_fields=update_payload.keys(),
        )

    def delete_plan(self, plan_id: str) -> str | None:
        pid = _validate_plan_id(plan_id)
        image_docs = self._list_docs(f"meal_plans/{pid}/images", field_paths=("day_idx",))
        writes: list[dict[str, Any]] = [
            {"delete": f"{self._doc_root}/meal_plans/{pid}/images/{img_id}"}
            for img_id, _ in image_docs
        ]
        writes.append({"delete": f"{self._doc_root}/meal_plans/{pid}"})
        self._commit_writes(writes)

        current_active = self.get_active_plan_id()
        if current_active == pid:
            remaining = self.list_plans()
            next_active = remaining[0].id if remaining else None
            self.set_active_plan_id(next_active)
            return next_active
        return current_active


class LocalFilePlanStorage(PlanStorage):
    """Lokal fil-lagring under data/saved_plans/ når Firebase ikke er konfigureret."""

    backend_name = "local"
    backend_label = "Lokalt lager"

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir is not None else _DEFAULT_LOCAL_DIR

    def _ensure_base_dir(self) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _meta_file(self) -> Path:
        return self._base_dir / "meta.json"

    def _plan_dir(self, plan_id: str) -> Path:
        return self._base_dir / _validate_plan_id(plan_id)

    def _plan_file(self, plan_id: str) -> Path:
        return self._plan_dir(plan_id) / "plan.json"

    def _images_dir(self, plan_id: str) -> Path:
        return self._plan_dir(plan_id) / "images"

    def _read_plan_payload(self, plan_file: Path) -> dict[str, Any]:
        return json.loads(plan_file.read_text(encoding="utf-8"))

    def _write_plan_payload(self, plan_file: Path, payload: Mapping[str, Any]) -> None:
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_active_plan_id(self) -> str | None:
        meta_file = self._meta_file()
        if not meta_file.exists():
            return None
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            return None
        active_id = data.get("active_plan_id")
        return str(active_id) if active_id else None

    def set_active_plan_id(self, plan_id: str | None) -> None:
        self._ensure_base_dir()
        cleaned = _validate_plan_id(plan_id) if plan_id else None
        self._meta_file().write_text(
            json.dumps(
                {"active_plan_id": cleaned, "updated_at": _utc_now_iso()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def list_plans(self) -> list[SavedPlanMeta]:
        if not self._base_dir.exists():
            return []
        plans: list[SavedPlanMeta] = []
        for entry in self._base_dir.iterdir():
            if not entry.is_dir() or not _SAFE_PLAN_ID_RE.match(entry.name):
                continue
            plan_file = entry / "plan.json"
            if not plan_file.is_file():
                continue
            try:
                data = self._read_plan_payload(plan_file)
            except Exception:
                continue
            plans.append(_meta_from_raw_dict(entry.name, data))
        plans.sort(key=lambda item: item.created_at, reverse=True)
        return plans

    def load_plan(self, plan_id: str) -> LoadedPlan | None:
        pid = _validate_plan_id(plan_id)
        plan_file = self._plan_file(pid)
        if not plan_file.is_file():
            return None

        data = self._read_plan_payload(plan_file)
        plan = WeeklyMealPlan.model_validate(data["plan_data"])
        checked_items = {str(item) for item in (data.get("checked_items") or [])}

        images: dict[int, bytes] = {}
        images_dir = self._images_dir(pid)
        if images_dir.is_dir():
            for img_path in sorted(images_dir.glob("*.jpg")):
                try:
                    day_idx = int(img_path.stem)
                except ValueError:
                    continue
                images[day_idx] = img_path.read_bytes()

        meta = _meta_from_raw_dict(pid, data)
        return LoadedPlan(
            meta=meta,
            plan=plan,
            images=images,
            checked_items=checked_items,
        )

    def save_new_plan(
        self,
        plan: WeeklyMealPlan,
        images: dict[int, bytes],
        location_label: str = "",
        title: str | None = None,
        set_active: bool = True,
    ) -> SavedPlanMeta:
        self._ensure_base_dir()
        plan_id = generate_plan_id()
        now_iso = _utc_now_iso()
        resolved_title = (title or "").strip() or build_default_plan_title(location_label)

        images_dir = self._images_dir(plan_id)
        images_dir.mkdir(parents=True, exist_ok=True)
        saved_count = 0
        for day_idx, raw_bytes in images.items():
            if not raw_bytes:
                continue
            (images_dir / f"{int(day_idx)}.jpg").write_bytes(bytes(raw_bytes))
            saved_count += 1

        meta = _build_meta_from_plan(
            plan_id=plan_id,
            title=resolved_title,
            created_at=now_iso,
            updated_at=now_iso,
            location_label=location_label,
            plan=plan,
            checked_count=0,
            image_count=saved_count,
        )
        self._write_plan_payload(
            self._plan_file(plan_id),
            {
                **meta.model_dump(),
                "plan_data": plan.model_dump(),
                "checked_items": [],
            },
        )
        if set_active:
            self.set_active_plan_id(plan_id)
        return meta

    def update_plan_and_day_image(
        self,
        plan_id: str,
        plan: WeeklyMealPlan,
        day_idx: int,
        image_bytes: bytes | None,
    ) -> None:
        pid = _validate_plan_id(plan_id)
        plan_file = self._plan_file(pid)
        if not plan_file.is_file():
            return

        images_dir = self._images_dir(pid)
        images_dir.mkdir(parents=True, exist_ok=True)
        img_file = images_dir / f"{int(day_idx)}.jpg"
        if image_bytes is not None and len(image_bytes) > 0:
            img_file.write_bytes(bytes(image_bytes))
        elif image_bytes is None and img_file.exists():
            img_file.unlink()

        data = self._read_plan_payload(plan_file)
        data["plan_data"] = plan.model_dump()
        data["summary"] = plan.summary
        data["dish_preview"] = build_dish_preview(plan)
        data["store_count"] = len(plan.shopping_list_by_store)
        data["total_shop_items"] = count_total_shop_items(plan)
        data["image_count"] = sum(1 for _ in images_dir.glob("*.jpg"))
        data["updated_at"] = _utc_now_iso()
        self._write_plan_payload(plan_file, data)

    def update_checked_items(
        self,
        plan_id: str,
        checked_items: Iterable[str],
    ) -> None:
        pid = _validate_plan_id(plan_id)
        plan_file = self._plan_file(pid)
        if not plan_file.is_file():
            return

        items_list = sorted({str(item) for item in checked_items})
        data = self._read_plan_payload(plan_file)
        data["checked_items"] = items_list
        data["checked_count"] = len(items_list)
        data["updated_at"] = _utc_now_iso()
        self._write_plan_payload(plan_file, data)

    def delete_plan(self, plan_id: str) -> str | None:
        pid = _validate_plan_id(plan_id)
        plan_dir = self._plan_dir(pid)
        if plan_dir.exists():
            shutil.rmtree(plan_dir)

        current_active = self.get_active_plan_id()
        if current_active == pid:
            remaining = self.list_plans()
            next_active = remaining[0].id if remaining else None
            self.set_active_plan_id(next_active)
            return next_active
        return current_active


def create_plan_storage(
    secrets_mapping: Mapping[str, Any] | None = None,
    local_dir: Path | None = None,
) -> tuple[PlanStorage, str | None]:
    """
    Opretter det valgte PlanStorage (Firebase Firestore hvis konfigureret, ellers lokalt).

    Returnerer (storage_instance, valgfri_advarsel).
    """
    creds_dict, cred_error = load_firebase_credentials_dict(secrets_mapping)
    if cred_error:
        return (
            LocalFilePlanStorage(local_dir),
            f"{cred_error} Bruger lokalt lager i stedet.",
        )

    if creds_dict is not None:
        db_id = load_firebase_database_id(secrets_mapping)
        try:
            return FirestorePlanStorage(creds_dict, database_id=db_id), None
        except Exception as exc:
            return (
                LocalFilePlanStorage(local_dir),
                f"Kunne ikke forbinde til Firebase Firestore ({exc}). Bruger lokalt lager i stedet.",
            )

    return LocalFilePlanStorage(local_dir), None

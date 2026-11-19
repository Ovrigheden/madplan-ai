"""Generering af fotorealistiske madfotos til ugens retter via Gemini API."""

from __future__ import annotations

import base64
import io
from concurrent.futures import ThreadPoolExecutor, as_completed

from google import genai
from google.genai import types
from PIL import Image, ImageOps

from src.models import MealDay, WeeklyMealPlan

IMAGE_MODELS: tuple[str, ...] = (
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite-image",
    "gemini-3.1-flash-image-preview",
)


def _to_jpeg_4_3(raw_bytes: bytes, size: tuple[int, int] = (640, 480)) -> bytes:
    """Konverterer billedbytes til et skarpt 4:3 JPEG-foto."""
    with Image.open(io.BytesIO(raw_bytes)) as img:
        rgb = img.convert("RGB")
        fitted = ImageOps.fit(rgb, size, method=Image.Resampling.LANCZOS)
        out = io.BytesIO()
        fitted.save(out, format="JPEG", quality=82, optimize=True)
        return out.getvalue()


def _extract_image_bytes(response: types.GenerateContentResponse) -> bytes | None:
    """Finder første billeddel i Gemini-svaret og returnerer rå bytes."""
    candidates = response.candidates or []
    for candidate in candidates:
        content = candidate.content
        if not content or not content.parts:
            continue
        for part in content.parts:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline else None
            if data is not None:
                if isinstance(data, str):
                    return base64.b64decode(data)
                return bytes(data)
    return None


def generate_dish_image(api_key: str, day: MealDay) -> bytes:
    """Genererer ét fotorealistisk madfoto (4:3 JPEG) for en enkelt dags ret."""
    if not api_key.strip():
        raise ValueError("Mangler Gemini API-nøgle.")

    ingredients_hint = ", ".join((day.offer_ingredients + day.extra_ingredients)[:6])
    prompt = (
        f"Realistic editorial food photography of the Danish dinner dish '{day.dish_name}'. "
        f"Description: {day.description}. "
        f"Key visible ingredients: {ingredients_hint}. "
        "Shot with a professional DSLR camera, natural daylight from the side, shallow depth of field, "
        "served neatly on a ceramic dinner plate on a clean wooden dining table. "
        "Photorealistic photograph ONLY — absolutely no illustration, no drawing, no cartoon, "
        "no text, no labels, no watermarks."
    )

    client = genai.Client(api_key=api_key.strip())
    config = types.GenerateContentConfig(
        response_modalities=[types.Modality.IMAGE, types.Modality.TEXT],
        image_config=types.ImageConfig(aspect_ratio="4:3"),
    )

    last_not_found_exc: Exception | None = None
    for model_name in IMAGE_MODELS:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
        except Exception as exc:
            msg = str(exc)
            if "404" in msg or "NOT_FOUND" in msg.upper():
                last_not_found_exc = exc
                continue
            raise

        raw_bytes = _extract_image_bytes(response)
        if raw_bytes is None:
            raise RuntimeError("Modellen returnerede intet billede.")
        return _to_jpeg_4_3(raw_bytes)

    if last_not_found_exc is not None:
        raise last_not_found_exc
    raise RuntimeError("Ingen billedmodel tilgængelig.")


def generate_plan_images(
    api_key: str,
    plan: WeeklyMealPlan,
    max_workers: int = 4,
) -> tuple[dict[int, bytes], list[str]]:
    """
    Genererer madfotos parallelt for ugens unikke retter og genbruger fotos på restedage.

    Returnerer (dict med dagsindeks -> JPEG-bytes, liste af advarsler).
    """
    images_by_index: dict[int, bytes] = {}
    warnings: list[str] = []

    unique_indices = [idx for idx, day in enumerate(plan.days) if not day.is_leftover_day]
    if unique_indices:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(unique_indices))) as pool:
            futures = {
                pool.submit(generate_dish_image, api_key, plan.days[idx]): idx
                for idx in unique_indices
            }
            for future in as_completed(futures):
                idx = futures[future]
                day = plan.days[idx]
                try:
                    images_by_index[idx] = future.result()
                except Exception as exc:
                    warnings.append(f"{day.day}: Kunne ikke generere foto ({exc})")

    # Genbrug nærmeste foregående billede på restedage, eller generér selvstændigt foto
    for idx, day in enumerate(plan.days):
        if not day.is_leftover_day:
            continue
        reused = next(
            (images_by_index[prev] for prev in range(idx - 1, -1, -1) if prev in images_by_index),
            None,
        )
        if reused is not None:
            images_by_index[idx] = reused
        else:
            try:
                images_by_index[idx] = generate_dish_image(api_key, day)
            except Exception as exc:
                warnings.append(f"{day.day}: Kunne ikke generere foto ({exc})")

    return images_by_index, warnings

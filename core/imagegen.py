"""OpenAI image generation and editing through an explicitly configured API profile."""
import base64
import datetime as dt
import re
import uuid
from urllib.parse import urlparse

import httpx


def _input_image(value):
    value = str(value or "")
    mime = "image/jpeg"
    if value.startswith("data:"):
        head, sep, value = value.partition(",")
        if not sep or ";base64" not in head:
            raise ValueError("Исходная картинка должна быть в формате base64")
        mime = head[5:].split(";", 1)[0] or mime
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("Не удалось прочитать вложенное изображение") from error
    if not raw or len(raw) > 20 * 1024 * 1024:
        raise ValueError("Размер исходного изображения должен быть не больше 20 МБ")
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(mime)
    if not ext:
        raise ValueError("Поддерживаются JPEG, PNG и WebP")
    return raw, mime, ext


def _openai_profile(settings):
    llm = settings.get("llm") or {}
    import modelrouter
    candidates = modelrouter.candidates(llm, role="images")
    for profile in candidates:
        host = urlparse(profile.get("base_url") or "").hostname
        if profile.get("kind") == "openai" and host == "api.openai.com" and profile.get("api_key"):
            return profile
    raise RuntimeError("Не найден профиль OpenAI API для изображений. Добавьте профиль OpenAI с ключом API в очередь «Изображения».")


def _codex_profile(settings):
    """Pick a configured Codex subscription for native image generation, if available."""
    llm = settings.get("llm") or {}
    import modelrouter
    profiles = llm.get("profiles") or {}
    active = llm.get("active")
    ordered = sorted(profiles.items(), key=lambda item: (item[1].get("priority", 9), item[0] != active, item[0]))
    for pid, profile in ordered:
        if profile.get("kind") != "codex_subscription" or "images" not in modelrouter.roles(profile):
            continue
        if not profile.get("routing_enabled", True) or not modelrouter.in_schedule(profile) or modelrouter.blocked(profile):
            continue
        if egress.local_only():
            continue
        return dict(profile, _profile_id=pid)
    return None


def _api_image(profile, prompt, images):
    base = (profile.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    model = profile.get("image_model") or "gpt-image-2.5-flare"
    headers = {"Authorization": "Bearer " + profile["api_key"]}
    timeout = httpx.Timeout(180, connect=20)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        if images:
            files = []
            for i, item in enumerate(images):
                source, mime, ext = _input_image(item)
                files.append(("image[]", (f"reference-{i + 1}.{ext}", source, mime)))
            response = client.post(base + "/images/edits", headers=headers,
                                   data={"model": model, "prompt": prompt, "size": "1024x1024"}, files=files)
        else:
            response = client.post(base + "/images/generations", headers=headers,
                                   json={"model": model, "prompt": prompt, "size": "1024x1024"})
    if response.is_error:
        try:
            detail = (response.json().get("error") or {}).get("message") or response.text
        except (ValueError, AttributeError):
            detail = response.text
        raise RuntimeError(f"OpenAI не создал изображение ({response.status_code}): {str(detail)[:500]}")
    try:
        item = response.json()["data"][0]
        return base64.b64decode(item["b64_json"], validate=True)
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise RuntimeError("OpenAI вернул ответ без готового изображения") from error


def generate(settings, workspace, prompt, input_images=(), output_dir=None):
    """Generate or edit one image, save it to the user's Dewiar folder, and return base64 for display."""
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("Опишите, какое изображение создать или как изменить фотографию")
    if len(prompt) > 8000:
        raise ValueError("Описание изображения слишком длинное (максимум 8000 знаков)")
    images = list(input_images or [])[:4]
    subscription = _codex_profile(settings)
    provider = "OpenAI API"
    if subscription:
        import subscriptions
        try:
            encoded = subscriptions.generate_image(subscription, prompt, images)
            raw = base64.b64decode(encoded, validate=True)
            provider = "Подписка Codex"
        except Exception as subscription_error:
            # A confirmed operation may fall back to an API key; the confirmation warns about API billing.
            try:
                raw = _api_image(_openai_profile(settings), prompt, images)
                provider = "OpenAI API (резерв после недоступности Codex)"
            except Exception:
                raise subscription_error
    else:
        raw = _api_image(_openai_profile(settings), prompt, images)
    if not raw or len(raw) > 20 * 1024 * 1024:
        raise RuntimeError("Размер готового изображения слишком велик")
    folder = output_dir if output_dir is not None else workspace.safe("Изображения")
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = folder / f"Изображение_{stamp}_{uuid.uuid4().hex[:6]}.png"
    path.write_bytes(raw)
    b64 = base64.b64encode(raw).decode("ascii")
    return {"path": str(path), "image": b64, "provider": provider}

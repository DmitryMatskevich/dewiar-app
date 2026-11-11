"""Send a verified project ZIP to its registered cloud folders."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx


DESTINATIONS = {
    "local": ("Локальная папка", None),
    "yandex": ("Яндекс.Диск", "OAuth Яндекс.Диск для бэкапов"),
    "google": ("Google Диск", "OAuth Google Drive для бэкапов"),
}
CHUNK_SIZE = 8 * 1024 * 1024
GOOGLE_FILES = "https://www.googleapis.com/drive/v3/files"
GOOGLE_FIELDS = "id,name,parents,size,sha256Checksum,md5Checksum"
YANDEX_RESOURCES = "https://cloud-api.yandex.net/v1/disk/resources"


class DeliveryError(RuntimeError):
    pass


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError("Выгрузка резервной копии остановлена пользователем.")


def _require(response, allowed):
    if response.status_code not in allowed:
        raise DeliveryError(f"Сервис вернул HTTP {response.status_code}.")
    return response


def _value(vault, name):
    records = [row for row in vault.list() if row.get("name", "").casefold() == name.casefold()]
    if len(records) != 1:
        raise DeliveryError(f"В сейфе нужна одна запись «{name}».")
    value = (vault.get(records[0]["id"]) or {}).get("value", "").strip()
    if not value:
        raise DeliveryError(f"Запись сейфа «{name}» пуста.")
    return value


def _verified(meta, archive, sha256, remote_sha="sha256", remote_md5="md5"):
    if int(meta.get("size", -1)) != archive.stat().st_size:
        raise DeliveryError("Размер файла в облаке отличается от размера локального ZIP.")
    if meta.get(remote_sha):
        if meta[remote_sha].lower() != sha256.lower():
            raise DeliveryError("SHA-256 файла в облаке отличается от локальной.")
        return "размер и SHA-256"
    if meta.get(remote_md5):
        digest = hashlib.md5(usedforsecurity=False)
        with archive.open("rb") as stream:
            for data in iter(lambda: stream.read(CHUNK_SIZE), b""):
                digest.update(data)
        if digest.hexdigest() != meta[remote_md5].lower():
            raise DeliveryError("MD5 файла в облаке отличается от локальной.")
        return "размер и MD5"
    raise DeliveryError("Диск ещё не предоставил контрольную сумму файла.")


def _upload_url(url, domain):
    parsed = urlsplit(url or "")
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not (host == domain or host.endswith("." + domain))):
        raise DeliveryError("Сервис вернул неожиданный адрес загрузки.")
    return url


def _yandex(client, archive, sha256, target, credential, cancel, progress):
    path = f"disk:/{target.strip('/') + '/' if target.strip('/') else ''}{archive.name}"
    headers = {"Authorization": "OAuth " + credential}
    params = {"path": path, "fields": "type,size,sha256,md5"}
    existing = client.get(YANDEX_RESOURCES, params=params, headers=headers)
    if existing.status_code != 404:
        return _verified(_require(existing, (200,)).json(), archive, sha256)
    response = client.get(YANDEX_RESOURCES + "/upload", headers=headers,
                          params={"path": path, "overwrite": "false"})
    upload = _upload_url(_require(response, (200,)).json().get("href"), "disk.yandex.net")
    size = archive.stat().st_size

    def chunks():
        sent = 0
        with archive.open("rb") as stream:
            for data in iter(lambda: stream.read(CHUNK_SIZE), b""):
                _check_cancel(cancel)
                yield data
                sent += len(data)
                progress(sent, size)

    _require(client.put(upload, content=chunks(), headers={"Content-Length": str(size)}), (200, 201, 202))
    for attempt in range(6):
        _check_cancel(cancel)
        response = client.get(YANDEX_RESOURCES, params=params, headers=headers)
        if response.status_code != 404:
            meta = _require(response, (200,)).json()
            if meta.get("sha256") or meta.get("md5"):
                return _verified(meta, archive, sha256)
        if attempt < 5:
            if cancel is not None:
                cancel.wait(2)
            else:
                import time
                time.sleep(2)
    raise DeliveryError("Файл отправлен, но Яндекс.Диск ещё не подтвердил его контрольную сумму.")


def _google(client, archive, sha256, target, credential, cancel, progress):
    try:
        oauth = json.loads(credential)
        form = {key: oauth[key] for key in ("client_id", "client_secret", "refresh_token")}
        if not all(isinstance(value, str) and value.strip() for value in form.values()):
            raise ValueError
    except (TypeError, KeyError, ValueError):
        raise DeliveryError("Для Google Диска нужна запись OAuth с client_id, client_secret и refresh_token.") from None
    token = _require(client.post("https://oauth2.googleapis.com/token", data={
        **form, "grant_type": "refresh_token"}), (200,)).json().get("access_token")
    if not token:
        raise DeliveryError("Google не выдал токен доступа.")
    headers = {"Authorization": "Bearer " + token}
    folder = _require(client.get(f"{GOOGLE_FILES}/{target}", headers=headers,
        params={"fields": "id,name,mimeType,trashed"}), (200,)).json()
    if folder.get("trashed") or folder.get("mimeType") != "application/vnd.google-apps.folder":
        raise DeliveryError("Google Диск не подтвердил указанную папку.")
    name = archive.name.replace("\\", "\\\\").replace("'", "\\'")
    query = f"'{target}' in parents and name = '{name}' and trashed = false"
    existing = _require(client.get(GOOGLE_FILES, headers=headers, params={
        "q": query, "fields": f"files({GOOGLE_FIELDS}),nextPageToken"}), (200,)).json()
    if existing.get("files"):
        if len(existing["files"]) != 1 or existing.get("nextPageToken"):
            raise DeliveryError("В папке Google Диска несколько файлов с таким именем.")
        return _verified(existing["files"][0], archive, sha256, "sha256Checksum", "md5Checksum")
    size = archive.stat().st_size
    start = _require(client.post("https://www.googleapis.com/upload/drive/v3/files",
        params={"uploadType": "resumable", "fields": GOOGLE_FIELDS},
        headers={**headers, "X-Upload-Content-Type": "application/zip", "X-Upload-Content-Length": str(size)},
        json={"name": archive.name, "parents": [target], "mimeType": "application/zip"}), (200,))
    upload = _upload_url(start.headers.get("Location"), "googleapis.com")
    sent, stalls = 0, 0
    with archive.open("rb") as stream:
        while sent < size:
            _check_cancel(cancel)
            stream.seek(sent)
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                raise DeliveryError("Локальный ZIP изменился во время загрузки.")
            try:
                response = client.put(upload, content=chunk, headers={
                    "Content-Range": f"bytes {sent}-{sent + len(chunk) - 1}/{size}"})
            except httpx.TransportError:
                response = None
            if response is None or response.status_code >= 500:
                response = client.put(upload, content=b"", headers={"Content-Range": f"bytes */{size}"})
            _require(response, (200, 201, 308))
            if response.status_code != 308:
                meta = response.json()
                if meta.get("name") != archive.name or target not in meta.get("parents", []):
                    raise DeliveryError("Google Диск вернул файл из другой папки или с другим именем.")
                progress(size, size)
                return _verified(meta, archive, sha256, "sha256Checksum", "md5Checksum")
            acknowledged = re.fullmatch(r"bytes=0-(\d+)", response.headers.get("Range", ""))
            offset = int(acknowledged[1]) + 1 if acknowledged else 0
            stalls = 0 if offset > sent else stalls + 1
            if stalls >= 3 or offset >= size:
                raise DeliveryError("Google Диск не подтвердил завершение загрузки.")
            sent = offset
            progress(sent, size)
    raise DeliveryError("Google Диск не подтвердил завершение загрузки.")


def _local(archive, sha256, target, cancel, progress):
    directory = Path(target)
    if not directory.is_dir():
        raise DeliveryError("Папка назначения недоступна. Проверьте, подключён ли диск.")
    destination = directory / archive.name
    if destination == archive:
        return "проверенный локальный архив уже в папке"
    if destination.exists():
        raise DeliveryError("В папке уже есть файл с таким именем.")
    size = archive.stat().st_size
    with tempfile.NamedTemporaryFile(dir=directory, prefix='.' + archive.name, suffix='.partial', delete=False) as output:
        temporary = Path(output.name)
        try:
            sent = 0
            with archive.open('rb') as stream:
                for data in iter(lambda: stream.read(CHUNK_SIZE), b''):
                    _check_cancel(cancel)
                    output.write(data)
                    sent += len(data)
                    progress(sent, size)
            output.flush()
            os.fsync(output.fileno())
            output.close()
            digest = hashlib.sha256()
            with temporary.open('rb') as copy:
                for data in iter(lambda: copy.read(CHUNK_SIZE), b''):
                    _check_cancel(cancel)
                    digest.update(data)
            if sent != size or digest.hexdigest() != sha256:
                raise DeliveryError("Контрольная сумма локальной копии не совпала.")
            if destination.exists():
                raise DeliveryError("В папке уже есть файл с таким именем.")
            os.replace(temporary, destination)
        finally:
            output.close()
            temporary.unlink(missing_ok=True)
    return "размер и SHA-256"


def deliver_backup(spec, archive, sha256, vault, cancel, progress, *, local_only=False):
    """Attempt each destination independently and return safe, displayable results."""
    results = []
    for row in spec.get("destinations", []):
        _check_cancel(cancel)
        provider, target = row['provider'], row['target']
        provider_name, vault_name = DESTINATIONS[provider]
        label = f'{provider_name}: {target}'
        try:
            report = lambda sent, size: progress(label, sent, size)
            report(0, archive.stat().st_size)
            if provider == 'local':
                check = _local(archive, sha256, target, cancel, report)
            else:
                if local_only:
                    raise DeliveryError('Включён режим «Ничего не уходит в интернет».')
                credential = _value(vault, vault_name)
                uploader = {'yandex': _yandex, 'google': _google}[provider]
                with httpx.Client(timeout=httpx.Timeout(60, write=120), follow_redirects=False) as client:
                    check = uploader(client, archive, sha256, target, credential, cancel, report)
        except InterruptedError:
            raise
        except DeliveryError as exc:
            results.append((label, False, str(exc)))
        except Exception:
            results.append((label, False, "Не удалось связаться с Диском или прочитать данные подключения."))
        else:
            results.append((label, True, f"копия подтверждена: {check}"))
    return results

"""Навыки: папки с SKILL.md (формат Agent Skills — как у Claude и ZCode).

Где лежат: встроенные — DATA_DIR/skills; свои — в папке ассистента «Навыки» (set_folder), а также в
~/.config/dewiar/skills (оттуда они переносятся в папку ассистента). Свой навык с тем же именем перекрывает встроенный;
встроенный нельзя удалить, только выключить (настройка skills.disabled).

<папка>/<имя>/SKILL.md:
    ---
    name: morning-brief
    description: Утренняя сводка — дата, погода, напоминания. Когда просят «что у меня сегодня».
    ---
    Инструкции для модели… (могут ссылаться на соседние файлы: references/*.md, scripts/*)

Модель видит только имена и описания; полный текст берёт инструментом use_skill,
когда навык нужен, а соседние файлы — read_skill_file. Скрипты навыка запускаются
только через run_command — то есть с подтверждением, даже если навык скачан из чужого каталога.
"""

import io
import logging
import re
import shutil
import zipfile
from pathlib import Path

import httpx

from settings import CONFIG_DIR, DATA_DIR

log = logging.getLogger("dewiar.skills")
USER_DIR = CONFIG_DIR / "skills"
BUILTIN_DIR = DATA_DIR / "skills"
MAX_ZIP = 20 * 1024 * 1024


class SkillError(Exception):
    pass


def parse_skill(path: Path):
    """SKILL.md -> {name, title, description, body, dir}. Шапка между --- разбирается построчно «ключ: значение»
    (полноценный YAML не нужен). name — для модели: нижний регистр, без пробелов; нет в шапке — имя папки.
    description обрезается до 600 знаков: описания всех навыков каждый раз уходят модели (prompt())."""
    text =path.read_text(encoding="utf-8", errors="replace")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    meta, body = {}, text
    if m:
        body = m.group(2)
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            if k.strip() and v.strip():
                meta[k.strip().lower()] = v.strip().strip("'\"")
    name = meta.get("name") or path.parent.name
    return {"name": re.sub(r"[^\w.-]+", "-", name.lower()).strip("-"), "title": meta.get("name", name),
            "description": meta.get("description", "")[:600], "body": body.strip(), "dir": path.parent}


# Кладётся в папку навыков как «О папке.md» — для человека, который откроет папку.
FOLDER_README = """# Навыки

Навык — папка с файлом SKILL.md: когда его применять (description) и что делать (инструкции ниже).
Ассистент сам выбирает подходящий навык по описанию. Можно положить сюда навык вручную, скачать из каталога
(студия → «Плагины» → «Навыки») или попросить: «научись…» — он напишет навык сам.

    Навыки/
      письмо-клиенту/
        SKILL.md          ---  name: letters  /  description: Когда просят написать письмо клиенту…  ---
        примеры.md        (необязательно: доп. файлы, ассистент читает их по ссылке из SKILL.md)
"""


class Skills:
    def __init__(self, settings):
        self.s = settings
        self.items = {}
        self.user_dir = USER_DIR                 # куда пишутся свои навыки (папка ассистента → «Навыки»)
        self.reload()

    def set_folder(self, folder):
        """Свои навыки — в папке ассистента, где их видно. Навыки из ~/.config/dewiar/skills переносим туда
        (если там ещё нет навыка с тем же именем папки). Папка недоступна — остаёмся на прежней."""
        folder = Path(folder)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if not (folder / "О папке.md").exists():
                (folder / "О папке.md").write_text(FOLDER_README, encoding="utf-8")
            if USER_DIR.is_dir() and USER_DIR.resolve() != folder.resolve():
                for d in USER_DIR.iterdir():
                    if (d / "SKILL.md").is_file() and not (folder / d.name).exists():
                        shutil.move(str(d), str(folder / d.name))
                        log.info("Навык %s перенесён в папку ассистента", d.name)
            self.user_dir = folder
        except OSError as e:
            log.warning("Папка навыков %s недоступна: %s", folder, e)
        self.reload()

    def reload(self):
        """Перечитать все навыки с диска. Порядок папок важен: позже прочитанный навык с тем же именем побеждает."""
        items = {}
        roots = [(BUILTIN_DIR, True), (USER_DIR, False)]
        if self.user_dir != USER_DIR:
            roots.append((self.user_dir, False))
        for root, builtin in roots:
            if not root.is_dir():
                continue
            for f in sorted(root.glob("*/SKILL.md")):
                try:
                    sk = parse_skill(f)
                except OSError as e:
                    log.warning("навык %s не прочитан: %s", f, e)
                    continue
                sk["builtin"] = builtin
                items[sk["name"]] = sk                     # свои перекрывают встроенные
        plugins = getattr(self, "plugins", None)           # навыки установленных плагинов (core/plugins.py)
        for d, pid in (plugins.skill_roots() if plugins else []):
            try:
                sk = parse_skill(d / "SKILL.md")
            except OSError as e:
                log.warning("навык плагина %s не прочитан: %s", d, e)
                continue
            if sk["name"] in items:
                continue                                   # свой или встроенный навык с тем же именем важнее
            sk["builtin"], sk["plugin"] = True, pid
            items[sk["name"]] = sk
        self.items = items
        log.info("Навыки: %s", ", ".join(sorted(items)) or "—")

    def enabled(self):
        off = set(self.s.get("skills", "disabled") or [])
        return [sk for n, sk in sorted(self.items.items()) if n not in off]

    def catalog(self):
        off = set(self.s.get("skills", "disabled") or [])
        return [{"name": n, "title": sk["title"], "description": sk["description"], "builtin": sk["builtin"],
                 "enabled": n not in off, "path": str(sk["dir"]), "plugin": sk.get("plugin", "")} for n, sk in sorted(self.items.items())]

    def prompt(self):
        """Добавка к системному сообщению: имена и описания включённых навыков (тексты — только через use_skill)."""
        en = self.enabled()
        if not en:
            return ""
        lines = "\n".join(f"- {sk['name']}: {sk['description']}" for sk in en)
        return ("\n\nУ тебя есть навыки — готовые инструкции для особых задач. Если просьба подходит под навык, "
                "сначала вызови use_skill с его именем и следуй инструкциям.\n" + lines)

    # ---- короткая инструкция (llm.lean_prompt) ----
    # В обычной инструкции — имена и описания всех навыков (~5 тысяч знаков к каждому вопросу; локальная модель на
    # обычной видеокарте тратит на них время). В короткой — только имена, а описания 1–3 подходящих навыков идут в
    # справку к самому вопросу: системная подсказка не меняется от вопроса к вопросу (кэш сервера модели цел).
    _STOP = {"когда", "просят", "нужно", "чтобы", "сделать", "файлы", "файл", "работа", "помощь", "только", "через", "задача",
             "просьба", "который", "какой", "можно", "этого", "этом", "твоей", "своей", "своих", "сначала", "сделай", "сделать",
             "сделаю", "давай", "пожалуйста", "можешь", "нужен", "нужна", "привет", "спасибо", "дела"}

    @staticmethod
    def _stems(text):
        words = re.findall(r"[a-zа-яё0-9]+", str(text or "").lower().replace("ё", "е"))
        return {w[:5] for w in words if len(w) >= 4 and w not in Skills._STOP} | {w for w in words if 2 <= len(w) <= 4 and w.isascii()}

    def prompt_lean(self):
        """Короткая добавка к системному сообщению: только имена навыков."""
        en = self.enabled()
        if not en:
            return ""
        return ("\n\nУ тебя есть навыки — готовые инструкции для особых задач: " + ", ".join(sk["name"] for sk in en)
                + ". Если в справке к вопросу назван подходящий навык (или просьба явно под навык по имени), сначала вызови "
                  "use_skill с его именем и следуй инструкциям.")

    def hint_for(self, text, limit=3):
        """Навыки, похожие на вопрос (по общим словам с именем и описанием) -> строка для справки к вопросу или ""."""
        q = self._stems(text) - {w[:5] for w in self._STOP}
        if not q:
            return ""
        scored = []
        for sk in self.enabled():
            name = self._stems(sk["name"].replace("-", " "))
            words = self._stems(sk.get("description", "")) | name
            n = len(q & words) + len(q & name)          # слово из имени навыка (docx, zip, git) — весомее
            if n >= 2:                                  # одно случайное общее слово — не повод
                scored.append((n, sk["name"], sk.get("description", "")))
        scored.sort(key=lambda x: (-x[0], x[1]))
        top = scored[:limit]
        if not top:
            return ""
        return "Подходящие навыки (use_skill): " + "; ".join(f"{n} — {d[:200]}" for _, n, d in top) + "."

    # ---- инструменты для модели ----
    def use(self, name):
        """use_skill: текст навыка (до 24000 знаков) + список его файлов (до 40) + путь папки — для скриптов."""
        sk = self.items.get(name)
        if not sk or sk not in self.enabled():
            raise SkillError(f"нет навыка {name}")
        files = [p.relative_to(sk["dir"]).as_posix() for p in sorted(sk["dir"].rglob("*"))
                 if p.is_file() and p.name != "SKILL.md"][:40]
        extra = ("\n\nФайлы навыка (read_skill_file): " + ", ".join(files)) if files else ""
        plugins = getattr(self, "plugins", None)
        if sk.get("plugin") and plugins:               # настройки плагина (имя ребёнка, город дачи…) — к инструкции
            extra = plugins.note(sk["plugin"]) + extra
        return sk["body"][:24000] + extra + f"\n\nПапка навыка: {sk['dir']}"

    def read_file(self, name, rel):
        """read_skill_file: файл навыка (до 12000 знаков). Путь от модели — только внутри папки навыка (../ не пройдёт)."""
        sk = self.items.get(name)
        if not sk:
            raise SkillError(f"нет навыка {name}")
        p = (sk["dir"] / rel).resolve()
        if sk["dir"].resolve() not in p.parents or not p.is_file():
            raise SkillError("нет такого файла в навыке")
        return p.read_text(encoding="utf-8", errors="replace")[:12000]

    # ---- установка ----
    def install_zip(self, data: bytes, name_hint=""):
        """Установить навык из zip -> имя. Навык — папка с самым верхним SKILL.md; остальное в архиве игнорируется.
        Пути с .. и абсолютные отвергаются (архив не должен писать вне папки навыка). Распаковка — во временную
        папку, затем замена прежней версии целиком. Предел MAX_ZIP — на размер самого архива."""
        if len(data) > MAX_ZIP:
            raise SkillError("архив навыка больше 20 МБ")
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise SkillError("это не zip-архив") from e
        names = [n for n in z.namelist() if not n.endswith("/")]
        skill_md = sorted((n for n in names if n.split("/")[-1] == "SKILL.md"), key=lambda n: n.count("/"))
        if not skill_md:
            raise SkillError("в архиве нет SKILL.md")
        prefix = skill_md[0][:-len("SKILL.md")]
        meta = parse_skill_text(z.read(skill_md[0]).decode("utf-8", "replace"), name_hint or prefix.strip("/") or "skill")
        target = self.user_dir / meta
        tmp = self.user_dir / (meta + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        for n in names:
            if not n.startswith(prefix):
                continue
            rel = Path(n[len(prefix):])
            if rel.is_absolute() or ".." in rel.parts:
                raise SkillError("в архиве опасные пути")
            out = tmp / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(n))
        shutil.rmtree(target, ignore_errors=True)
        tmp.rename(target)
        self.reload()
        return meta

    def install_url(self, url):
        """Навык по ссылке: .md или текст с шапкой --- — это SKILL.md, иначе zip-архив."""
        try:
            r = httpx.get(url, timeout=30, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise SkillError(f"не скачался: {e}") from e
        if url.lower().endswith(".md") or r.content[:3] == b"---":
            return self.install_text(r.text)
        return self.install_zip(r.content)

    def install_text(self, text):
        name = parse_skill_text(text, "skill")
        d = self.user_dir / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(text, encoding="utf-8")
        self.reload()
        return name

    # ---- редактор ----
    def get(self, name):
        """Навык для редактора студии: описание, текст и текстовые файлы до 200 КБ."""
        sk = self.items.get(name)
        if not sk:
            raise SkillError(f"нет навыка {name}")
        files = {}
        for p in sorted(sk["dir"].rglob("*")):
            if p.is_file() and p.name != "SKILL.md" and p.stat().st_size < 200_000:
                try:
                    files[p.relative_to(sk["dir"]).as_posix()] = p.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue                                   # двоичные файлы в редакторе не показываем
        return {"name": sk["name"], "description": sk["description"], "body": sk["body"], "files": files,
                "builtin": sk["builtin"]}

    def save(self, name, description, body, files=None, old_name=""):
        """Создать или изменить навык -> имя. Пишется всегда в свою папку, поэтому правка встроенного создаёт свою копию,
        которая его перекрывает. old_name — переименование своего навыка (папка переносится).
        files — содержимое из редактора: записываются, а текстовые файлы (.md .txt .json .py .sh), которых
        в files нет, удаляются; прочие файлы папки (картинки и т. п.) не трогаем."""
        name = re.sub(r"[^\w.-]+", "-", (name or "").strip().lower()).strip("-.")[:64]
        if not name:
            raise SkillError("у навыка должно быть имя (латиница, цифры, дефис)")
        description = " ".join((description or "").split())
        if len(description) < 10:
            raise SkillError("опишите, когда применять навык, — хотя бы одной фразой")
        if not (body or "").strip():
            raise SkillError("напишите инструкции навыка")
        for rel in (files or {}):                      # сначала проверить все имена — отвергнутый навык не оставит следов
            rp = Path(rel)
            if rp.is_absolute() or ".." in rp.parts or not rel.strip() or rp.name == "SKILL.md":
                raise SkillError(f"недопустимое имя файла: {rel}")
        d = self.user_dir / name
        old = self.items.get(old_name) if old_name else None
        if old and old_name != name and not old["builtin"] and old["dir"].is_dir():
            shutil.rmtree(d, ignore_errors=True)
            shutil.move(str(old["dir"]), str(d))
        d.mkdir(parents=True, exist_ok=True)
        desc = description.replace("\n", " ")
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body.strip()}\n", encoding="utf-8")
        if files is not None:
            keep = set()
            for rel, content in files.items():
                rp = Path(rel)
                if rp.is_absolute() or ".." in rp.parts or not rel.strip() or rp.name == "SKILL.md":
                    raise SkillError(f"недопустимое имя файла: {rel}")
                (d / rp).parent.mkdir(parents=True, exist_ok=True)
                (d / rp).write_text(content, encoding="utf-8")
                keep.add((d / rp).resolve())
            for p in list(d.rglob("*")):
                if p.is_file() and p.name != "SKILL.md" and p.resolve() not in keep and p.suffix in (".md", ".txt", ".json", ".py", ".sh"):
                    p.unlink()
        self.reload()
        return name

    def export_zip(self, name):
        """Навык в zip (папка <имя>/ внутри) — тот же вид, что принимает install_zip."""
        sk = self.items.get(name)
        if not sk:
            raise SkillError(f"нет навыка {name}")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(sk["dir"].rglob("*")):
                if p.is_file():
                    z.write(p, f"{name}/{p.relative_to(sk['dir']).as_posix()}")
        return buf.getvalue()

    def catalog_fetch(self, url):
        """Каталог навыков с сайта: {"skills": [{name, title, description, url}]} или просто список
        -> [{name, title, description, url, installed}]. Относительные url — от адреса каталога."""
        try:
            r = httpx.get(url, timeout=15, follow_redirects=True)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise SkillError(f"каталог не открылся: {e}") from e
        items = data.get("skills", []) if isinstance(data, dict) else data
        base = url.rsplit("/", 1)[0] + "/"
        out = []
        for x in items:
            if not isinstance(x, dict) or not x.get("url"):
                continue
            u = x["url"] if re.match(r"^https?://", x["url"]) else base + x["url"].lstrip("/")
            out.append({"name": x.get("name", ""), "title": x.get("title") or x.get("name", ""),
                        "description": x.get("description", ""), "url": u,
                        "installed": x.get("name", "") in self.items})
        return out

    def remove(self, name):
        sk = self.items.get(name)
        if not sk or sk["builtin"]:
            raise SkillError("встроенный навык можно только выключить")
        shutil.rmtree(sk["dir"])
        self.reload()


def parse_skill_text(text, fallback):
    """Имя навыка из текста SKILL.md (поле name в шапке) или fallback -> безопасное имя папки: без / и ..,
    не длиннее 64 знаков."""
    m =re.search(r"(?m)^name:\s*(.+)$", text.split("\n---", 2)[0] if text.startswith("---") else "")
    name = m.group(1).strip().strip("'\"") if m else fallback
    name = re.sub(r"[^\w.-]+", "-", name.lower()).strip("-.") or "skill"
    return name[:64]

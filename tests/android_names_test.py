"""Телефон: «позвони …» — кому звонить (Names.java на обычной JDK); ловит звонок не тому человеку.
Падежи («маме» → «Мама», «Лёше» → «Лёша»), латиница («Саше» → «Sasha»), имя с фамилией точнее; общее начало разных слов
(«мам…» у «Мамедов») — не совпадение; «жене» — и «Жена», и «Женя»: тогда спрашиваем."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


if not shutil.which("javac"):
    print("нет JDK — пропускаю"); print("ALL OK"); sys.exit(0)
tmp = Path(tempfile.mkdtemp())
src = tmp / "src/pro/dewiar/app"; src.mkdir(parents=True)
shutil.copy(ROOT / "android/src/pro/dewiar/app/Names.java", src)
(src / "NamesMain.java").write_text("""package pro.dewiar.app;
import java.io.*;
public class NamesMain { public static void main(String[] a) throws Exception {
  BufferedReader r = new BufferedReader(new InputStreamReader(System.in, "UTF-8"));
  PrintStream o = new PrintStream(System.out, true, "UTF-8");
  for (String l; (l = r.readLine()) != null; ) { String[] p = l.split("\\t"); o.println(Names.score(p[0], p[1])); } } }
""", encoding="utf-8")
env = {k: v for k, v in os.environ.items() if k != "JAVA_TOOL_OPTIONS"}
subprocess.run(["javac", "-encoding", "UTF-8", "-d", str(tmp / "cls")] + [str(p) for p in src.glob("*.java")], check=True, env=env, capture_output=True)
CONTACTS = ["Мама", "Мамедов Рустам", "Лёша Сосед", "Sasha Petrov", "Саша Иванова", "Жена", "Женя Работа", "Папа", "Паша Такси",
            "Наталья Петровна", "Наташа", "Олег", "Телефон На Громова 46", "Исаева Людмила"]


def best(who):
    lines = "".join(f"{who}\t{c}\n" for c in CONTACTS)
    out = subprocess.run(["java", "-Dfile.encoding=UTF-8", "-cp", str(tmp / "cls"), "pro.dewiar.app.NamesMain"], input=lines.encode(), env=env,
                         capture_output=True).stdout.decode().split()
    sc = sorted(((int(s), c) for s, c in zip(out, CONTACTS)), reverse=True)
    good = [c for s, c in sc if s >= 43]
    close = [c for s, c in sc if s >= 43 and s >= sc[0][0] - 3]
    return good[0] if good else None, close


cases = {"маме": "Мама", "Лёше": "Лёша Сосед", "Леше": "Лёша Сосед", "папе": "Папа", "Олегу": "Олег",
         "Саше Петрову": "Sasha Petrov", "Наташе": "Наташа"}
got = {w: best(w)[0] for w in cases}
ok(got == cases, f"падежи и латиница: {got}")
ok(best("маме")[1] == ["Мама"], f"«маме» — только «Мама», не «Мамедов»: {best('маме')[1]}")
ok(set(best("жене")[1]) == {"Жена", "Женя Работа"}, f"«жене» — и «Жена», и «Женя» (дательный у обоих): спросим ({best('жене')[1]})")
amb = best("Саше")[1]
ok(set(amb) == {"Sasha Petrov", "Саша Иванова"}, f"«Саше» — две Саши: спросим, кому, а не позвоним наугад ({amb})")
ok(best("Василию")[0] is None, "кого нет — так и говорим")
# 24.10, владелец: «позвони на обычный мобильный телефон Исаевой Людмиле» — звонок ушёл «Телефон На Громова 46»
ok(best("на обычный мобильный телефон исаевой людмиле")[0] == "Исаева Людмила", f"служебные слова не в счёт: {best('на обычный мобильный телефон исаевой людмиле')}")
ok(best("Исаевой Людмиле")[0] == "Исаева Людмила", "фамилия и имя в дательном — тот же человек")
print("ИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)

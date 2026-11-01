"""Install a verified build at one stable path, retaining the previous bundle.

Does not edit TCC, request permissions or change user data. Quit Dewiar first.
"""
import argparse
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ID = "pro.dewiar.macos"


def signing(bundle):
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)],
                   check=True, capture_output=True, text=True)
    result = subprocess.run(["/usr/bin/codesign", "-d", "-r-", str(bundle)],
                            check=True, capture_output=True, text=True)
    return result.stdout + result.stderr


def validate_bundle(bundle):
    with (bundle / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if info.get("CFBundleIdentifier") != BUNDLE_ID:
        raise ValueError("Установка отменена: это не сборка Dewiar")
    return signing(bundle)


def requirement(signature):
    # codesign prefixes an implicitly derived ad-hoc requirement with '# '.
    for line in signature.splitlines():
        line = line.lstrip("# ")
        if line.startswith("designated =>"):
            return line
    raise ValueError("Не удалось определить идентичность подписи приложения")


def install(source, target):
    source, target = Path(source).resolve(), Path(target).absolute()
    if target.name != "Dewiar.app" or target.is_symlink():
        raise ValueError("Нужен обычный путь установки с именем Dewiar.app")
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Сборка и установленное приложение должны быть отдельными папками")
    new_signature = validate_bundle(source)
    if target.exists():
        old_signature = validate_bundle(target)
        if old_signature != new_signature:
            # Never silently replace a persistent signer with an ad-hoc build or
            # a different identity. Ad-hoc hashes change between development builds.
            old_requirement = requirement(old_signature)
            new_requirement = requirement(new_signature)
            if old_requirement != new_requirement:
                if "cdhash H" not in old_requirement:
                    raise ValueError("Изменилась постоянная подпись Dewiar: установка отменена")
                print("ВНИМАНИЕ: изменилась ad-hoc подпись. macOS может потребовать заново добавить Dewiar в разрешения записи экрана.")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Same filesystem: the final replacement is a rename, never a merge of files.
    stage_root = Path(tempfile.mkdtemp(prefix=".dewiar-install-", dir=target.parent))
    staged, backup = stage_root / "Dewiar.app", stage_root / "previous.app"
    try:
        subprocess.run(["/usr/bin/ditto", str(source), str(staged)], check=True)
        validate_bundle(staged)
        if target.exists():
            target.rename(backup)
        try:
            staged.rename(target)
        except OSError:
            if backup.exists():
                backup.rename(target)
            raise
        print(f"Установлено: {target}")
        if backup.exists():
            print(f"Предыдущая версия для отката: {backup}")
    finally:
        if not backup.exists():
            shutil.rmtree(stage_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "dist/Dewiar.app")
    parser.add_argument("--target", type=Path, default=Path("/Applications/Dewiar.app"))
    args = parser.parse_args()
    # Refuse replacement of either a running source or installed build, including
    # orphaned --core processes left after closing the GUI.
    binaries = [str(p.resolve() / "Contents/MacOS/Dewiar") for p in (args.source, args.target)]
    processes = subprocess.check_output(["/bin/ps", "-axo", "command="], text=True)
    if any(line == exe or line.startswith(exe + " ") for line in processes.splitlines() for exe in binaries):
        parser.error("Сначала завершите Dewiar и его процессы ядра; запущенную сборку заменять нельзя")
    install(args.source, args.target)


if __name__ == "__main__":
    main()

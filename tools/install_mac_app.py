"""Install a verified build at one stable path, retaining the previous bundle.

Does not edit TCC, request permissions or change user data. Quit Dewiar first.
"""
import argparse
import plistlib
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ID = "pro.dewiar.macos"
LSREGISTER = Path("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister")
sys.path.insert(0, str(ROOT / "tools"))
from mac_build_identity import validate_manifest  # noqa: E402


def signing(bundle):
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)],
                   check=True, capture_output=True, text=True)
    result = subprocess.run(["/usr/bin/codesign", "-d", "-r-", str(bundle)],
                            check=True, capture_output=True, text=True)
    return result.stdout + result.stderr


def validate_bundle(bundle, *, expected_fingerprint=None):
    with (bundle / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if info.get("CFBundleIdentifier") != BUNDLE_ID:
        raise ValueError("Установка отменена: это не сборка Dewiar")
    signature = signing(bundle)
    if expected_fingerprint is not None:
        validate_manifest(bundle, ROOT, expected_fingerprint)
    return signature


def requirement(signature):
    # codesign prefixes an implicitly derived ad-hoc requirement with '# '.
    for line in signature.splitlines():
        line = line.lstrip("# ")
        if line.startswith("designated =>"):
            return line
    raise ValueError("Не удалось определить идентичность подписи приложения")


def install(source, target, *, expected_fingerprint=None):
    source, target = Path(source).resolve(), Path(target).absolute()
    if target.name != "Dewiar.app" or target.is_symlink():
        raise ValueError("Нужен обычный путь установки с именем Dewiar.app")
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("Сборка и установленное приложение должны быть отдельными папками")
    new_signature = validate_bundle(source, expected_fingerprint=expected_fingerprint)
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
    staged, backup = stage_root / "Dewiar.app", stage_root / "previous.zip"
    old_contents = stage_root / "previous-contents"
    installed = False
    try:
        subprocess.run(["/usr/bin/ditto", str(source), str(staged)], check=True)
        validate_bundle(staged, expected_fingerprint=expected_fingerprint)
        if set(p.name for p in staged.iterdir()) != {'Contents'}:
            raise ValueError('Неожиданная структура новой .app: установка отменена')
        if target.exists():
            # Retain the application directory itself. Moving it to previous.app
            # lets LaunchServices/TCC follow the old app under its backup name.
            # Archives are not launchable copies with the same bundle identifier.
            if set(p.name for p in target.iterdir()) != {'Contents'}:
                raise ValueError('Неожиданная структура установленной .app: установка отменена')
            subprocess.run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent',
                            str(target), str(backup)], check=True)
            if not backup.is_file() or backup.stat().st_size == 0:
                raise OSError('Не создан архив предыдущей версии; установка отменена')
            (target / 'Contents').rename(old_contents)
            try:
                (staged / 'Contents').rename(target / 'Contents')
                validate_bundle(target, expected_fingerprint=expected_fingerprint)
            except Exception:
                if (target / 'Contents').exists():
                    shutil.rmtree(target / 'Contents')
                old_contents.rename(target / 'Contents')
                raise
        else:
            staged.rename(target)
        installed = True
        print(f"Установлено: {target}")
        if backup.exists():
            print(f"Предыдущая версия для отката: {backup}")
    finally:
        # Keep only the rollback archive; never leave a second live bundle.
        for folder in ((staged, old_contents) if installed else (staged,)):
            if folder.exists():
                shutil.rmtree(folder)
        if not backup.exists():
            shutil.rmtree(stage_root)


def unregister_legacy_launchers(source, target):
    """Remove stale Dewiar paths from LaunchServices without deleting files.

    macOS can otherwise resolve ``open -a Dewiar`` to an older .app left in a
    worktree or a previous experiment.  The canonical copy remains registered
    at ``/Applications/Dewiar.app``; old bundles are only unregistered.
    """
    if not LSREGISTER.is_file():
        return
    source = Path(source).resolve()
    target = Path(target).resolve()
    candidates = {source}
    for search_root in (ROOT.parent, Path("/Applications")):
        if not search_root.exists():
            continue
        try:
            candidates.update(path.resolve() for path in search_root.rglob("Dewiar.app"))
        except OSError:
            continue
    for candidate in sorted(candidates, key=str):
        if candidate == target or not candidate.is_dir():
            continue
        subprocess.run([str(LSREGISTER), "-u", str(candidate)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "dist/Dewiar.app")
    parser.add_argument("--target", type=Path, default=Path("/Applications/Dewiar.app"))
    args = parser.parse_args()
    # Refuse replacement of either a running source or installed build, including
    # orphaned --core processes left after closing the GUI.
    binaries = [str(p.resolve() / "Contents/MacOS/Dewiar") for p in (args.source, args.target)]
    import fcntl
    sys.path.insert(0, str(ROOT / 'core'))
    from settings import STATE_DIR
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Share the GUI's launch lock so a launch during archive/copy cannot start
    # reading the bundle halfway through its replacement.
    with (STATE_DIR / 'ui.lock').open('a') as held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('Dewiar запущен; завершите приложение перед установкой')
        processes = subprocess.check_output(["/bin/ps", "-axo", "command="], text=True)
        if any(line == exe or line.startswith(exe + " ") for line in processes.splitlines() for exe in binaries):
            parser.error("Сначала завершите Dewiar и его процессы ядра; запущенную сборку заменять нельзя")
    from mac_build_identity import source_fingerprint
    expected_fingerprint = source_fingerprint(ROOT)
    install(args.source, args.target, expected_fingerprint=expected_fingerprint)
    unregister_legacy_launchers(args.source, args.target)
    print(f"LaunchServices: оставлена только копия {args.target}")


if __name__ == "__main__":
    main()

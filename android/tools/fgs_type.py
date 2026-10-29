"""Тип службы переднего плана, которого нет в framework-res сборки (Android 11): remoteMessaging = 0x200 (Android 14).
aapt2 принимает в foregroundServiceType только известные ему имена, поэтому в AndroidManifest.xml стоит метка
«camera|microphone|location» (= 0xC8, ни одна наша служба её не использует), а здесь в собранном двоичном манифесте
значение 0xC8 заменяется на 0x200. Метка должна найтись ровно один раз — иначе сборка падает, а не выходит со службой
«камера». Использование: fgs_type.py base.apk [ещё.apk …]"""
import struct
import sys
import zipfile

MARK, REAL = 0xC8, 0x200
PAT = struct.pack("<HBBI", 8, 0, 0x11, MARK)         # Res_value: size 8, res0, TYPE_INT_HEX, data


def patch(apk):
    with zipfile.ZipFile(apk) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    out = []
    for info, data in items:
        if info.filename == "AndroidManifest.xml":
            n = data.count(PAT)
            if n != 1:
                sys.exit(f"{apk}: метка типа службы найдена {n} раз (нужно 1)")
            data = data.replace(PAT, struct.pack("<HBBI", 8, 0, 0x11, REAL))
        out.append((info, data))
    with zipfile.ZipFile(apk, "w") as z:
        for info, data in out:
            z.writestr(info, data, compress_type=info.compress_type)


if __name__ == "__main__":
    for a in sys.argv[1:]:
        patch(a)

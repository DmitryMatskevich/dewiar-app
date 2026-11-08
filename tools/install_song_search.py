"""Install only the free browser-search runtime; never touch model settings."""
from pathlib import Path
import subprocess
import sys


def main():
    root=Path.home()/'Library/Application Support/Dewiar/runtimes/song-search'
    python=root/'bin/python'
    if not python.is_file():subprocess.run([sys.executable,'-m','venv',str(root)],check=True)
    subprocess.run([str(python),'-m','pip','install','playwright==1.64.1'],check=True)
    subprocess.run([str(python),'-m','playwright','install','chromium'],check=True)
    print('Готов бесплатный браузерный поиск Яндекс/Google:',root)


if __name__=='__main__':main()

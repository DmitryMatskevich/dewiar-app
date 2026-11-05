"""Reject a HUD with unparseable scripts before packaging (Node is build-only)."""
import subprocess
from html.parser import HTMLParser
from pathlib import Path


def check_hud_scripts(root):
    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__()
            self.current = None
            self.scripts = []

        def handle_starttag(self, tag, attrs):
            if tag == 'script':
                self.current = (dict(attrs), [])

        def handle_data(self, data):
            if self.current is not None:
                self.current[1].append(data)

        def handle_endtag(self, tag):
            if tag == 'script' and self.current is not None:
                self.scripts.append(self.current)
                self.current = None

    page = Path(root) / 'ui/hud.html'
    parser = Scripts()
    parser.feed(page.read_text(encoding='utf-8'))
    for index, (attrs, parts) in enumerate(parser.scripts):
        source = attrs.get('src')
        label = source or f'hud.html inline script {index + 1}'
        text = (page.parent / source).read_text(encoding='utf-8') if source else ''.join(parts)
        result = subprocess.run(['node', '--check', '--input-type=' + ('module' if attrs.get('type') == 'module' else 'commonjs')], input=text, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(f'Cannot package broken HUD: {label}\n{result.stderr}')
    print(f'HUD syntax: {len(parser.scripts)} scripts OK')


if __name__ == '__main__':
    import sys
    check_hud_scripts(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])

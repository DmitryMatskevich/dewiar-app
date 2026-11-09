"""Extract named contact details and social links from fetched HTML pages."""
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from contact_extraction import extract_contacts_from_text


SOCIAL_HOSTS = (
    'instagram.com', 'facebook.com', 'vk.com', 't.me', 'telegram.me', 'wa.me',
    'whatsapp.com', 'youtube.com', 'youtu.be', 'linkedin.com', 'x.com',
    'twitter.com', 'ok.ru', 'tiktok.com',
)


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text, self.title, self.links = [], [], []
        self._skip = 0
        self._title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'style', 'noscript', 'svg'}:
            self._skip += 1
        if tag == 'title':
            self._title = True
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'].strip())

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript', 'svg'} and self._skip:
            self._skip -= 1
        if tag == 'title':
            self._title = False

    def handle_data(self, data):
        if self._title:
            self.title.append(data)
        if not self._skip and data.strip():
            self.text.append(data.strip())


def extract(html, source_url):
    parser = _Page()
    parser.feed(html)
    title = ' '.join(' '.join(parser.title).split())
    contacts = extract_contacts_from_text('\n'.join(parser.text))
    for href in parser.links:
        lower = href.lower()
        if lower.startswith('mailto:'):
            address = href[7:].split('?', 1)[0].strip()
            if address and address not in contacts['email']:
                contacts['email'].append(address)
        elif lower.startswith('tel:'):
            phone = href[4:].split('?', 1)[0].strip()
            if phone and phone not in contacts['phone']:
                contacts['phone'].append(phone)
    socials = []
    for href in parser.links:
        absolute = urljoin(source_url, href)
        host = (urlsplit(absolute).hostname or '').lower().removeprefix('www.')
        if any(host == domain or host.endswith('.' + domain) for domain in SOCIAL_HOSTS):
            if absolute not in socials:
                socials.append(absolute)
    contacts['social'] = socials
    return {'title': title, 'contacts': contacts}

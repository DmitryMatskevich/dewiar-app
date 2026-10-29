package pro.dewiar.app;

import java.util.Locale;

/** Сравнение имён без Android: контакты («позвони маме») и приложения («открой калькулятор») — для Phone.java.
 *  Без зависимостей от Android, чтобы проверяться на обычной JDK (tests/android_names_test.py). Главное правило: лучше
 *  не найти или переспросить, чем позвонить не тому. Счёт 0..100+: 100 — имя целиком, ~60 — совпало слово. */
final class Names {
    private Names() { }

    static String norm(String s) { return s == null ? "" : s.toLowerCase(Locale.ROOT).replace('ё', 'е').trim(); }

    // латиница для а…я по порядку (ё уже заменена на е в norm)
    private static final String[] TR = {"a", "b", "v", "g", "d", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
            "f", "kh", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"};

    /** «Саша» → «sasha»: контакты часто записаны латиницей. */
    static String translit(String s) {
        StringBuilder b = new StringBuilder();
        for (char ch : norm(s).toCharArray()) {
            if (ch >= 'а' && ch <= 'я') b.append(TR[ch - 'а']); else b.append(ch);
        }
        return b.toString().replace("kh", "h");      // «х» в именах латиницей чаще пишут как h: Mihail
    }

    /** Одно слово запроса и одно слово имени: точно — 60; то же слово в другом падеже («маме» ~ «Мама», «Лёше» ~ «Лёша») —
     *  40 + длина общего начала − разница длин, обычно 40–50 (общее начало от 3 букв, различаются только окончания
     *  до 3 букв); разные слова с общим началом
     *  («мам» у «Мамедов») — 0, иначе ассистент позвонит не тому. */
    static int wordScore(String a, String b) {
        if (a.length() < 2 || b.length() < 2) return 0;
        if (a.equals(b)) return 60;
        int n = 0;
        while (n < Math.min(a.length(), b.length()) && a.charAt(n) == b.charAt(n)) n++;
        int sh = Math.min(a.length(), b.length()), lg = Math.max(a.length(), b.length());
        if (n >= 3 && n >= sh - 2 && lg - n <= 3) return 40 + n - (lg - sh);
        return 0;
    }

    /** Слова просьбы, а не имени: «на обычный мобильный телефон» (24.10: «телефон» и «на» точно совпали с контактом
     *  «Телефон На Громова 46» и перевесили «Исаеву Людмилу» в другом падеже — звонок ушёл не тому). */
    static final java.util.Set<String> STOP = new java.util.HashSet<String>(java.util.Arrays.asList(
            "на", "по", "в", "во", "к", "ко", "с", "со", "у", "о", "через", "и", "для", "мой", "моей", "моему", "мою", "моя",
            "телефон", "телефона", "телефону", "номер", "номера", "номеру", "мобильный", "мобильного", "мобильному", "мобильник",
            "сотовый", "сотового", "сотовому", "обычный", "обычного", "обычному", "простой", "домашний", "рабочий", "звонок"));

    /** Совпадение имени: лучшее слово + по 8 за каждое ещё совпавшее («Саше Петрову» точнее, чем просто «Саше»).
     *  Служебные слова запроса (STOP) не считаются. */
    static int score(String want, String have) {
        String w = norm(want), h = norm(have);
        if (w.isEmpty() || h.isEmpty()) return 0;
        if (h.equals(w)) return 100;
        String[] ws = w.split("[\\s,.]+"), hs = h.split("[\\s,.()-]+");
        int best = 0, extra = 0;
        for (String a : ws) {
            if (STOP.contains(a) && ws.length > 1) continue;
            int sa = 0;
            for (String b : hs) {
                sa = Math.max(sa, wordScore(a, b));
                sa = Math.max(sa, wordScore(translit(a), b) - 1);           // «Саше» ~ «Sasha»
                sa = Math.max(sa, wordScore(a, translit(b)) - 1);
            }
            if (sa > 0) { if (best > 0) extra += 8; best = Math.max(best, sa); }
        }
        return best == 0 ? 0 : best + extra;
    }

}

import sys; sys.path.insert(0, "core")
from llm import SentenceSplitter, ThinkStripper, speakable
from engine import find_name

t = ThinkStripper()
out = "".join(t.feed(x) for x in ["<thi", "nk>hmm</th", "ink>При", "вет!"])
assert out == "Привет!", out

sp = SentenceSplitter(); got = []
for ch in "Москва — столица России, крупнейший город страны и важный центр. Население около тринадцати миллионов. Ещё":
    got += sp.feed(ch)
got += sp.flush()
print(got)
assert got[-1] == "Ещё" and len(got) >= 3

al = {"patrik": ["патрик"], "jarvis": ["джарвис", "джервис"], "eva": ["эва", "ева"], "agent": ["агент"]}
for text, exp in [("Патрик, какая погода?", ("patrik", "какая погода?")),
                  ("Эй, Джарвис включи музыку", ("jarvis", "включи музыку")),
                  ("Ева расскажи анекдот", ("eva", "расскажи анекдот")),
                  ("мой друг работает агентом в банке", (None, "")),
                  ("Агент, доклад", ("agent", "доклад"))]:
    pid, end = find_name(text, al)
    rest = text[end:].lstrip(" ,.!?") if pid else ""
    print(text, "->", pid, "|", rest)
    assert (pid, rest) == exp, (text, pid, rest)
print(speakable("**Жирный** текст [ссылка](http://x) и `код` 😀 - пункт"))
print("OK")

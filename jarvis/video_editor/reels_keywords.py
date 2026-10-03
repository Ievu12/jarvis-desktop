"""Suggests the key words of a Reels transcript to highlight - offline,
no API: Lithuanian and English filler words are skipped, and long
words, numbers, names (capitalized mid-sentence), repeated words, words
said with "!" and your own word list score higher."""

from __future__ import annotations

from jarvis.video_editor.reels import ReelsWord, normalize_word

_STOPWORDS = frozenset("""
ir o bet kad kaip tai yra buvo bus būti būna esu esi esame aš tu jis ji mes jūs jie jos mano tavo savo jo jos
jų mūsų jūsų man tau jam jai mums jums jiems mane tave jį ją mus jus juos jas save sau į iš su be per prie už
ant po apie nuo iki ar ne nei net jau dar tik labai čia ten šis ši šie šios tas ta tie tos tą tuos kur kai kas
ką kuo kuris kuri kurie kurios kodėl nes todėl taip gal gali galima galite reikia vienas viena du dvi trys
bei arba tačiau nors jeigu jei tada dabar šiandien visi visos viskas visą kiekvienas tiek kiek dėl tam
taip pat pati pats patys mūsų čia štai na nu va gerai labas ačiū prašau dar vis vėl
the a an and or but if so to of in on at for with from by is are was were be been it this that these those
i you he she we they me my your our their his her its not no yes just very really also then than there here
""".split())


def suggest_keywords(
    words: tuple[ReelsWord, ...] | list[ReelsWord], *, max_count: int = 5, own_words: list[str] | tuple = (),
) -> list[str]:
    """Up to `max_count` key words (as spoken, punctuation stripped),
    in the order they are first said."""
    own = {normalize_word(w) for w in own_words if normalize_word(w)}
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    shown: dict[str, str] = {}
    previous_text = ""
    for position, word in enumerate(words):
        plain = normalize_word(word.text)
        raw = word.text.strip()
        if not plain or (plain in _STOPWORDS and plain not in own):
            previous_text = raw
            continue
        score = 0.0
        if plain in own:
            score += 5.0
        if any(ch.isdigit() for ch in plain):
            score += 2.5
        if len(plain) >= 8:
            score += 0.7 + (len(plain) - 8) * 0.15
        sentence_start = position == 0 or previous_text.endswith((".", "!", "?", "…"))
        stripped = raw.strip("\"'„“()«»")
        if stripped[:1].isupper() and not sentence_start:
            score += 1.5
        if stripped.isupper() and len(plain) > 1:
            score += 1.0
        if raw.endswith("!"):
            score += 1.0
        if plain in scores:
            scores[plain] += 0.8 + score * 0.2
        else:
            scores[plain] = score
            first_seen[plain] = position
            shown[plain] = stripped.strip(".,!?…:;")
        previous_text = raw

    ranked = sorted((k for k, v in scores.items() if v >= 1.0), key=lambda k: (-scores[k], first_seen[k]))
    chosen = ranked[:max_count]
    return [shown[k] for k in sorted(chosen, key=lambda k: first_seen[k])]

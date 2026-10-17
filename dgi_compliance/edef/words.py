"""French amount in words, in Congolese francs (pure Python, no Frappe import).

Frappe's money_in_words produces "CDF Cent Vingt Huit Mille seulement." (capitalised words,
no hyphens, currency code). The normalized invoice needs a proper French sentence, always in
CDF (the e-MCF works in CDF only), e.g. 2 950 000 -> "Deux millions neuf cent cinquante mille
francs congolais". Traditional French spelling rules are applied (vingt/cent agreement, "et un",
"mille" invariable, "de" after an exact million/milliard).
"""
from decimal import Decimal, ROUND_HALF_UP

_UNITS = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf",
          "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize",
          "dix-sept", "dix-huit", "dix-neuf"]
_TENS = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante", 6: "soixante"}


def _below_100(n: int, plural_ok: bool) -> str:
    if n < 20:
        return _UNITS[n]
    tens, unit = divmod(n, 10)
    if tens in _TENS:
        word = _TENS[tens]
        if unit == 0:
            return word
        if unit == 1:
            return f"{word} et un"
        return f"{word}-{_UNITS[unit]}"
    if tens == 7:
        if unit == 1:
            return "soixante et onze"
        return "soixante-" + _UNITS[10 + unit]
    # 80..99
    if n == 80:
        return "quatre-vingts" if plural_ok else "quatre-vingt"
    rest = n - 80
    return "quatre-vingt-" + _UNITS[rest]


def _below_1000(n: int, plural_ok: bool) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        if hundreds == 1:
            parts.append("cent")
        else:
            word = f"{_UNITS[hundreds]} cent"
            if rest == 0 and plural_ok:
                word += "s"
            parts.append(word)
    if rest:
        parts.append(_below_100(rest, plural_ok))
    return " ".join(parts)


def number_to_words(n: int) -> str:
    """Integer >= 0 in French words (traditional spelling)."""
    n = int(n)
    if n == 0:
        return "zéro"
    if n < 0:
        return "moins " + number_to_words(-n)
    parts = []
    for value, singular, plural in ((10 ** 9, "milliard", "milliards"),
                                    (10 ** 6, "million", "millions")):
        count, n = divmod(n, value)
        if count:
            parts.append(f"{number_to_words(count)} {plural if count > 1 else singular}")
    thousands, n = divmod(n, 1000)
    if thousands:
        parts.append("mille" if thousands == 1 else f"{_below_1000(thousands, False)} mille")
    if n:
        parts.append(_below_1000(n, True))
    return " ".join(parts)


def amount_in_words_cdf(amount) -> str:
    """'Deux millions neuf cent cinquante mille francs congolais' (+ 'et X centimes')."""
    value = Decimal(str(abs(float(amount or 0)))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    francs = int(value)
    centimes = int((value - francs) * 100)
    words = number_to_words(francs)
    ends_with_big_unit = francs and francs % 1_000_000 == 0
    if francs <= 1:
        currency = "franc congolais"
    else:
        currency = ("de " if ends_with_big_unit else "") + "francs congolais"
    text = f"{words} {currency}"
    if centimes:
        text += f" et {number_to_words(centimes)} centime{'s' if centimes > 1 else ''}"
    return text[0].upper() + text[1:]

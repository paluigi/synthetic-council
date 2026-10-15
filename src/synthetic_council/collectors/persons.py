"""Canonical person names + person ids for GC members (name variants across eras)."""

# canonical name -> known variants (as printed on ECB GC pages over time)
CANONICAL = {
    "Vítor Constâncio": [
        "Vítor Manuel Ribeiro Constâncio",
        "Victor Constancio",
        "Vitor Constancio",
    ],
    "Mario Draghi": ["Mario Draghi"],
    "Jean-Claude Trichet": ["Jean-Claude Trichet"],
    "Lucas Papademos": ["Lucas D. Papademos"],
    "Willem F. Duisenberg": ["Wim Duisenberg", "Willem Duisenberg"],
    "Yves Mersch": ["Yves Mersch"],
    "Gertrude Tumpel-Gugerell": ["Gertrude Tumpel-Gugerell"],
    "Joaquín Almunia": ["Joaquín Almunia"],
    "Nout Wellink": ["Nout Wellink"],
    "Axel A. Weber": ["Axel Weber", "Axel A. Weber"],
    "Erkki Liikanen": ["Erkki Liikanen"],
    "Miguel Fernández Ordóñez": [
        "Miguel Fernández Ordóñez",
        "Miguel Fernández-Ordóñez",
    ],
    "Athanasios Orphanides": ["Athanasios Orphanides"],
    "Michael C. Bonello": ["Michael Bonello"],
    "Guy Quaden": ["Guy Quaden"],
    "Christian Noyer": ["Christian Noyer"],
    "John Hurley": ["John Hurley"],
    "Marko Kranjec": ["Marko Kranjec"],
    "Jozef Makúch": ["Jozef Makuch", "Jozef Makúch"],
    "Ivan Ramk": ["Ivan Ramk"],
    "Klaus Liebscher": ["Klaus Liebscher"],
    "Ewald Nowotny": ["Ewald Nowotny"],
    "Patrick Honohan": ["Patrick Honohan"],
    "Carlos da Silva Costa": ["Carlos Costa", "Carlos da Silva Costa"],
    "Andrea Enria": ["Andrea Enria"],
    "Ignazio Visco": ["Ignazio Visco"],
    "Jens Weidmann": ["Jens Weidmann"],
    "Klaas Knot": ["Klaas Knot"],
    "Patrick Herz": ["Patrick Herz"],
    "Ardo Hansson": ["Ardo Hansson"],
    "Bogusław Grabowski": ["Boguslaw Grabowski", "Bogusław Grabowski"],
    "Sławomir Skrzypek": ["Slawomir Skrzypek", "Sławomir Skrzypek"],
    "Marek Belka": ["Marek Belka"],
    # Wayback snapshot typo: accent on the wrong syllable created a second
    # person entry with a 6-week tenure
    "Pablo Hernández de Cos": ["Pablo Hernandéz de Cos"],
    # wayback handover artifact: the 6-week "Philip Lane" EB row is the
    # Praet→Lane chief-economist transition seam; participants use the
    # full spelling
    "Philip R. Lane": ["Philip Lane"],
    # "George A." spelling never made it into the original variant list —
    # the 2008–14 Bank of Greece governorship split into two identities
    "Georgios Provopoulos": [
        "George Provopoulos",
        "Georgios A. Provopoulos",
        "George A. Provopoulos",
    ],
    # surname-only spellings printed in some monetary policy accounts
    "Ilmar Rimsevics": ["Ilmārs Rimšēvičs", "Ilmars Rimsevics", "Rimšēvičs"],
    "Alexander Demarco": ["Demarco"],
    "Māris Kālis": ["Kālis"],
    "Sandra Švaljek": ["Švaljek"],
    "Tina Žumer": ["Žumer Šujica"],
    "Boris Vujčić": ["Vujčić"],
    "Dimitar Radev": ["Radev"],
    "Margarita Delgado": ["Delgado"],
    "Primož Dolenc": ["Dolenc"],
    "Tuomas Välimäki": ["Välimäki"],
    "Zeti Aziz": [],
    "Rein Minka": [],
    "Durmus Yilmaz": [],
    "Justin Yifu Lin": [],
}

_VARIANT_TO_CANON = {v: k for k, vs in CANONICAL.items() for v in vs}


def canonicalise(name: str) -> str:
    n = " ".join(name.split())
    if n in _VARIANT_TO_CANON:
        return _VARIANT_TO_CANON[n]
    if n in CANONICAL:
        return n
    return n


def person_id(name: str) -> str:
    """Stable slug for a canonical person name."""
    import re
    import unicodedata

    n = unicodedata.normalize("NFKD", canonicalise(name))
    n = "".join(c for c in n if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-")

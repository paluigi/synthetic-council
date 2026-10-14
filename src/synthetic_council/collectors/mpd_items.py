"""Full MPD EA item catalogue: official CL_PD_ITEM labels, units, categories.

Units verified against the ECB MPD data-information page layout (annual
percentage changes / balances in % of GDP / levels): percentages everywhere
except interest rates (%, level) and NEER (level, 3y % change is published but
the database stores the level path).
"""

# code -> (short label, unit, category)
ITEMS: dict[str, tuple[str, str, str]] = {
    # headline
    "HIC": ("HICP inflation", "%", "Headline"),
    "YER": ("Real GDP growth", "%", "Headline"),
    "URX": ("Unemployment rate", "% of labour force", "Headline"),
    # economic activity (expenditure)
    "PCR": ("Private consumption", "% growth", "Economic activity"),
    "GCR": ("Government consumption", "% growth", "Economic activity"),
    "GIR": ("Government investment", "% growth", "Economic activity"),
    "ITR": ("Gross fixed capital formation", "% growth", "Economic activity"),
    "PYR": ("Real disposable household income", "% growth", "Economic activity"),
    "SAX": ("Household saving ratio", "%", "Economic activity"),
    "DDR": ("Domestic demand (excl. stocks)", "% growth", "Economic activity"),
    "SCR": ("Changes in inventories", "pp contribution to GDP", "Economic activity"),
    "XTR": ("Exports (goods & services)", "% growth", "Economic activity"),
    "MTR": ("Imports (goods & services)", "% growth", "Economic activity"),
    "NER": ("Net exports", "pp contribution to GDP", "Economic activity"),
    "CAN": ("Current account balance", "% of GDP", "Economic activity"),
    "YEG": ("Output gap", "% of GDP", "Economic activity"),
    # labour market
    "LNN": ("Total employment", "% growth", "Labour market"),
    "LNNM": ("Employment, market sector", "% growth", "Labour market"),
    "LFN": ("Labour force", "% growth", "Labour market"),
    "LAX": ("Participation rate", "%", "Labour market"),
    "LAN": ("Working-age population", "% growth", "Labour market"),
    "LEN": ("Employees", "% growth", "Labour market"),
    "LSN": ("Self-employed", "% growth", "Labour market"),
    # prices and costs
    "YED": ("GDP deflator", "% growth", "Prices and costs"),
    "CEX": ("Compensation per employee", "% growth", "Prices and costs"),
    "PRO": ("Productivity (whole economy)", "% growth", "Prices and costs"),
    "UTAX": ("Unit taxes (whole economy)", "% growth", "Prices and costs"),
    "ULA": ("Unit labour costs (whole economy)", "% growth", "Prices and costs"),
    "UPFA": ("Unit profits (whole economy)", "% growth", "Prices and costs"),
    "HEX": ("HICP ex energy", "% growth", "Prices and costs"),
    "HEF": ("HICP ex food & energy", "% growth", "Prices and costs"),
    "HEFT": ("HICP ex energy, food & ind. taxes", "% growth", "Prices and costs"),
    "HIF": ("HICP food", "% growth", "Prices and costs"),
    "HEG": ("HICP energy", "% growth", "Prices and costs"),
    "HSE": ("HICP services", "% growth", "Prices and costs"),
    "HNE": ("HICP non-energy ind. goods", "% growth", "Prices and costs"),
    "GID": ("Government investment deflator", "% growth", "Prices and costs"),
    "GCD": ("Government consumption deflator", "% growth", "Prices and costs"),
    # public finance
    "SED": ("Government budget balance", "% of GDP", "Public finance"),
    "MAL": ("Government debt (Maastricht)", "% of GDP", "Public finance"),
    "STB": ("Structural budget balance", "% of GDP", "Public finance"),
    # external / technical assumptions
    "FOD": ("Foreign demand for EA exports", "% growth", "External & assumptions"),
    "MTD": ("Import deflator", "% growth", "External & assumptions"),
    "CPX": ("Competitor export prices (n.c.)", "% growth", "External & assumptions"),
    "EER": ("Nominal effective exchange rate (EER19)", "level", "External & assumptions"),
    "EERB": ("Nominal effective exchange rate (EER38)", "level", "External & assumptions"),
    "EXR": ("USD/EUR exchange rate", "level", "External & assumptions"),
    "OIL": ("Oil price (Brent)", "USD/bbl", "External & assumptions"),
    "GAS": ("Gas price (TTF)", "EUR/MWh", "External & assumptions"),
    "ELEC": ("Electricity price (wholesale)", "EUR/MWh", "External & assumptions"),
    "ETS": ("EU carbon allowance (ETS)", "EUR/t", "External & assumptions"),
    "LTN": ("Long-term interest rate (10y)", "%", "External & assumptions"),
    "STN": ("Short-term interest rate (3m)", "%", "External & assumptions"),
    # codes present in the bulk extract since the 2026 vintage (official
    # CL_PD_ITEM labels, added 2026-09 after the catalogue-vs-data test failed)
    "FSTN": ("Fiscal stance (adjusted for NGEU and CICE)", "pp", "Public finance"),
    "PCD": ("Household consumption deflator", "% growth", "Prices and costs"),
    "CER": ("Real compensation per employee", "% growth", "Prices and costs"),
    "FOOD": ("Food price (DG-AGRI total food)", "% growth", "External & assumptions"),
    "YER_M": (
        "Real GDP with modified domestic demand for Ireland",
        "% growth",
        "Economic activity",
    ),
}

# NOTE: EXR, GCD, GID, GIR, LAN, LAX, LEN, LFN, LNNM, LSN, OIL, YEG exist in
# CL_PD_ITEM but have no EA annual observations in the bulk extract; they are
# kept for reference and skipped automatically when absent from a round.

# items that are percentage-point contributions, not growth rates
CONTRIB_ITEMS = {"SCR", "NER"}
# items expressed as levels (do not append % when rendering)
LEVEL_ITEMS = {"EER", "EERB", "EXR", "OIL", "GAS", "ELEC", "ETS", "LTN", "STN"}

HEADLINE = ["HIC", "YER", "URX"]

CATEGORY_ORDER = [
    "Headline",
    "Economic activity",
    "Labour market",
    "Prices and costs",
    "Public finance",
    "External & assumptions",
]


def category_order() -> list[str]:
    """PD_ITEM codes in memo display order (by category, then catalogue order)."""
    out: list[str] = []
    for cat in CATEGORY_ORDER:
        out.extend(code for code, (_, _, c) in ITEMS.items() if c == cat)
    return out


def label(item: str) -> str:
    """Official short label for a PD_ITEM code."""
    short, unit, _cat = ITEMS.get(item, (item, "", ""))
    return short


def unit(item: str) -> str:
    """Unit string for a PD_ITEM code (empty if unknown)."""
    return ITEMS.get(item, ("", "", ""))[1]


def category(item: str) -> str:
    """Category for a PD_ITEM code ('Other' if unknown)."""
    return ITEMS.get(item, ("", "", "Other"))[2]

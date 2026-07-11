RTL_LANGS = {"Farsi", "Persian", "Arabic", "Urdu", "Hebrew"}
CJK_LANGS = {"Chinese", "Japanese", "Korean"}

COMMON_LANGUAGES = [
    "Farsi",
    "Arabic",
    "Spanish",
    "French",
    "German",
    "Portuguese",
    "Italian",
    "Russian",
    "Turkish",
    "Chinese",
    "Japanese",
    "Korean",
    "Hindi",
    "Urdu",
    "Hebrew",
]

EXPANSION_RATIOS = {
    ("English", "German"): 1.30,
    ("English", "French"): 1.15,
    ("English", "Spanish"): 1.20,
    ("English", "Portuguese"): 1.15,
}


def mode_for(source_language: str, target_language: str) -> str:
    if target_language in RTL_LANGS or target_language in CJK_LANGS:
        return "B"
    if EXPANSION_RATIOS.get((source_language, target_language), 1.20) > 1.15:
        return "A_with_B_fallback"
    return "A"


def is_rtl(language: str) -> bool:
    return language in RTL_LANGS


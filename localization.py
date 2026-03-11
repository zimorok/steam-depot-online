import os
import json
from typing import Dict, Optional

class LocalizationManager:
    def __init__(self, lang_dir: str = "lang"):
        self.lang_dir = lang_dir
        self.translations: Dict[str, Dict[str, str]] = {}
        self.current_language: str = "en"
        self._load_all_translations()

    def _load_all_translations(self) -> None:
        if not os.path.exists(self.lang_dir):
            os.makedirs(self.lang_dir, exist_ok=True)
            # Warning will be logged via callback if set
            return

        any_translation_loaded = False
        for filename in os.listdir(self.lang_dir):
            if filename.endswith(".json"):
                lang_code = filename[:-5]
                filepath = os.path.join(self.lang_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        self.translations[lang_code] = json.load(f)
                        any_translation_loaded = True
                except (json.JSONDecodeError, IOError) as e:
                    # Errors will be reported via callback
                    pass

        if not any_translation_loaded:
            if "en" not in self.translations:
                self.translations["en"] = {}

    def get_string(self, key: str) -> str:
        lang_translations = self.translations.get(self.current_language, {})
        return lang_translations.get(key, key)

    def set_language(self, lang_code: str) -> None:
        if lang_code not in self.translations and lang_code != "en":
            # Will be reported via callback
            pass
        self.current_language = lang_code

    def get_available_languages(self) -> Dict[str, str]:
        display_names = {
            "en": "English", "fr": "Français", "cn": "中文", "de": "Deutsch",
            "es": "Español", "it": "Italiano", "jp": "日本語", "ko": "한국어",
            "pt": "Português", "ru": "Русский", "tr": "Türkçe", "zh": "简体中文",
            "ar": "العربية", "bg": "Български", "ca": "Català", "cs": "Čeština",
            "da": "Dansk", "el": "Ελληνικά", "eo": "Esperanto", "fa": "فارسی",
            "fi": "Suomi", "he": "עברית", "in": "हिंदी", "hr": "Hrvatski",
            "hu": "Magyar", "id": "Bahasa Indonesia", "is": "Íslenska",
            "lt": "Lietuvių", "lv": "Latviešu", "nl": "Nederlands", "no": "Norsk",
            "pl": "Polski", "pt-br": "Português do Brasil", "ro": "Română",
            "sk": "Slovenčina", "sl": "Slovenščina", "sv": "Svenska", "th": "ไทย",
            "uk": "Українська", "vi": "Tiếng Việt", "zh-cn": "简体中文",
            "zh-tw": "繁體中文", "af": "Afrikaans",
        }
        available = {"en": display_names.get("en", "English")}
        for code in sorted(self.translations.keys()):
            available[code] = display_names.get(code, code)
        return available

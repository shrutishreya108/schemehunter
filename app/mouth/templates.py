"""Templated (no-LLM) messages: nudges, reminders, "forward the SMS". Bilingual {hi, en}."""

TEMPLATES: dict[str, dict[str, str]] = {
    "forward_sms": {
        "en": "Please forward the latest SMS from the {scheme} portal about application {ref}.",
        "hi": "कृपया आवेदन {ref} के बारे में {scheme} पोर्टल का आखिरी SMS फ़ॉरवर्ड करें।",
    },
    "doc_nudge": {
        "en": "Still needed for {scheme}: {docs}. Send a clear photo when you can.",
        "hi": "{scheme} के लिए अभी चाहिए: {docs}। जब हो सके साफ़ फ़ोटो भेजें।",
    },
    "deadline_reminder": {
        "en": "{scheme}: the window closes in {days} days.",
        "hi": "{scheme}: आवेदन की आखिरी तारीख में {days} दिन बचे हैं।",
    },
}


def render(name: str, lang: str, **values: str) -> str:
    template = TEMPLATES[name]
    return (template.get(lang) or template["en"]).format(**values)

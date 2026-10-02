"""Languages NEXUS writes in besides English: Arabic, Turkish and Hebrew.

Everything language-specific that code (not the AI) is responsible for lives
here: which language a country gets, right-to-left layout, the fixed wording
of landing pages and system e-mails, opt-out phrases to recognise in replies,
the claim phrases that are banned in each language, and local search and ad
keywords.

Free text (e-mails, page copy, ads) is written by the AI in the target
language and then checked three ways: figures against your catalogue, the
banned phrases below, and an independent translation back into English that
must pass the same checks as an English message.
"""

from __future__ import annotations

import re
from typing import Any

ENGLISH = "en"

LANGUAGES: dict[str, dict[str, Any]] = {
    "en": {"name": "English", "native": "English", "rtl": False, "google_language": 1000},
    "ar": {"name": "Arabic (Modern Standard Arabic)", "native": "العربية", "rtl": True, "google_language": 1019},
    "tr": {"name": "Turkish", "native": "Türkçe", "rtl": False, "google_language": 1037},
    "he": {"name": "Hebrew", "native": "עברית", "rtl": True, "google_language": 1027},
}

# The business language NEXUS uses by default in each market. Countries not
# listed get English. Override per country in commercial settings ("languages"),
# e.g. {"United Arab Emirates": "en"} if your buyers there prefer English.
COUNTRY_LANGUAGES = {
    "Saudi Arabia": "ar", "United Arab Emirates": "ar", "Qatar": "ar", "Kuwait": "ar", "Bahrain": "ar",
    "Oman": "ar", "Jordan": "ar", "Lebanon": "ar", "Iraq": "ar", "Yemen": "ar", "Egypt": "ar",
    "Palestine": "ar", "Turkey": "tr", "Israel": "he",
}

COUNTRY_NAMES = {
    "ar": {"Saudi Arabia": "السعودية", "United Arab Emirates": "الإمارات", "Qatar": "قطر", "Kuwait": "الكويت",
           "Bahrain": "البحرين", "Oman": "عُمان", "Jordan": "الأردن", "Lebanon": "لبنان", "Iraq": "العراق",
           "Yemen": "اليمن", "Egypt": "مصر", "Palestine": "فلسطين"},
    "tr": {"Turkey": "Türkiye"},
    "he": {"Israel": "ישראל"},
}

CATEGORY_NAMES = {
    "ar": {"medical_equipment": "المعدات الطبية", "pharmaceutical": "المنتجات الصيدلانية",
           "refurbished_laptop": "أجهزة الكمبيوتر المحمولة المجددة", "used_iphone": "هواتف آيفون المستعملة",
           "server_it": "الخوادم ومعدات تقنية المعلومات"},
    "tr": {"medical_equipment": "tıbbi ekipman", "pharmaceutical": "ilaç ürünleri",
           "refurbished_laptop": "yenilenmiş dizüstü bilgisayarlar", "used_iphone": "ikinci el iPhone'lar",
           "server_it": "sunucular ve BT ekipmanları"},
    "he": {"medical_equipment": "ציוד רפואי", "pharmaceutical": "מוצרי תרופות",
           "refurbished_laptop": "מחשבים ניידים מחודשים", "used_iphone": "מכשירי אייפון משומשים",
           "server_it": "שרתים וציוד IT"},
}

DEFAULT_PROMISE = "within one business day"

# ------------------------------------------------------------------ fixed wording
STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "wa_header": "WhatsApp us", "wa_chat": "Chat on WhatsApp", "wa_continue": "Continue on WhatsApp",
        "privacy": "Privacy notice", "serving": "Serving {country}", "reply": "Reply {promise}",
        "promise": DEFAULT_PROMISE, "what_we_supply": "What we supply", "why_us": "Why buyers work with us",
        "how_it_works": "How it works", "step1_t": "Tell us what you need",
        "step1_d": "Models, quantity and delivery location.", "step2_t": "Get a written quotation",
        "step2_d": "Price, condition, warranty, lead time and terms, {promise}.",
        "step3_t": "Confirm and receive", "step3_d": "You only commit when you accept the quotation.",
        "request_quote": "Request a quotation", "name": "Your name", "organisation": "Organisation",
        "email": "Work e-mail", "phone": "Phone / WhatsApp", "product": "Product", "any_product": "Any / not sure",
        "quantity": "Quantity", "need": "What do you need?", "need_ph": "Specification, delivery location, timing",
        "consent": "I agree to be contacted about this enquiry. See the", "questions": "Questions",
        "about": "About us",
        "price": "Indicative prices from <b>USD {price}</b> per unit &mdash; your quotation depends on quantity, specification and delivery.",
        "warranty": "warranty", "moq": "MOQ", "thanks_title": "Thank you &mdash; we have your enquiry",
        "thanks_body": "We will reply by e-mail {promise} with a written quotation or any questions.",
        "wa_text": "Hello, I would like a quotation for {topic}.", "card_cta": "Get a quotation",
        "ack_subject": "We have your enquiry",
        "ack": "Dear {name},\n\nThank you for your enquiry about {topic}{qty}. We have received it and will reply {promise} with a written quotation or any questions we have.\n\nIf it helps, reply to this e-mail with the exact specification, delivery location and timing.\n\nRegards,\n{sender}",
        "units": "units",
        "optout": "To stop receiving these emails, reply with 'unsubscribe'", "optout_link": " or use {link}",
    },
    "ar": {
        "wa_header": "راسلنا عبر واتساب", "wa_chat": "تحدث معنا عبر واتساب", "wa_continue": "المتابعة عبر واتساب",
        "privacy": "إشعار الخصوصية", "serving": "نخدم {country}", "reply": "نرد {promise}",
        "promise": "خلال يوم عمل واحد", "what_we_supply": "ما نورّده", "why_us": "لماذا يتعامل المشترون معنا",
        "how_it_works": "كيف تتم العملية", "step1_t": "أخبرنا بما تحتاجه",
        "step1_d": "الطرازات والكمية وموقع التسليم.", "step2_t": "احصل على عرض سعر مكتوب",
        "step2_d": "السعر والحالة والضمان ومدة التوريد والشروط، {promise}.",
        "step3_t": "أكّد واستلم", "step3_d": "لا تلتزم بشيء إلا عند قبولك عرض السعر.",
        "request_quote": "اطلب عرض سعر", "name": "الاسم", "organisation": "الجهة / الشركة",
        "email": "البريد الإلكتروني للعمل", "phone": "الهاتف / واتساب", "product": "المنتج",
        "any_product": "أي منتج / غير متأكد", "quantity": "الكمية", "need": "ما الذي تحتاجه؟",
        "need_ph": "المواصفات، موقع التسليم، التوقيت",
        "consent": "أوافق على التواصل معي بخصوص هذا الطلب. راجع", "questions": "أسئلة شائعة", "about": "من نحن",
        "price": "أسعار استرشادية تبدأ من <b>{price} دولار أمريكي</b> للوحدة &mdash; يعتمد عرض السعر على الكمية والمواصفات والتسليم.",
        "warranty": "الضمان", "moq": "الحد الأدنى للطلب", "thanks_title": "شكراً لك &mdash; استلمنا طلبك",
        "thanks_body": "سنرد عليك بالبريد الإلكتروني {promise} بعرض سعر مكتوب أو بأي استفسارات.",
        "wa_text": "مرحباً، أرغب في الحصول على عرض سعر لـ {topic}.", "card_cta": "اطلب عرض سعر",
        "ack_subject": "استلمنا طلبك",
        "ack": "السيد/السيدة {name}،\n\nشكراً لاستفساركم عن {topic}{qty}. لقد استلمنا طلبكم وسنرد عليكم {promise} بعرض سعر مكتوب أو بأي استفسارات لدينا.\n\nيمكنكم الرد على هذه الرسالة بالمواصفات الدقيقة وموقع التسليم والتوقيت المطلوب.\n\nمع التحية،\n{sender}",
        "units": "وحدة",
        "optout": "لإيقاف استلام هذه الرسائل، يكفي الرد بعبارة «إلغاء الاشتراك»", "optout_link": " أو استخدام الرابط {link}",
        "err_many": "طلبات كثيرة؛ يرجى المحاولة لاحقاً", "err_name": "يرجى إدخال اسمك واسم الجهة",
        "err_email": "يرجى إدخال بريد إلكتروني صحيح", "err_consent": "يرجى الموافقة على التواصل معك بخصوص هذا الطلب",
        "err_qty": "الكمية يجب أن تكون رقماً", "err_qty_range": "الكمية يجب أن تكون بين 1 و 1,000,000",
    },
    "tr": {
        "wa_header": "WhatsApp'tan yazın", "wa_chat": "WhatsApp'tan sohbet edin", "wa_continue": "WhatsApp'tan devam edin",
        "privacy": "Gizlilik bildirimi", "serving": "{country} için tedarik", "reply": "Yanıt: {promise}",
        "promise": "bir iş günü içinde", "what_we_supply": "Tedarik ettiklerimiz",
        "why_us": "Alıcılar neden bizimle çalışıyor", "how_it_works": "Nasıl çalışır",
        "step1_t": "İhtiyacınızı bildirin", "step1_d": "Modeller, miktar ve teslimat yeri.",
        "step2_t": "Yazılı teklif alın", "step2_d": "Fiyat, durum, garanti, teslim süresi ve koşullar, {promise}.",
        "step3_t": "Onaylayın ve teslim alın", "step3_d": "Yalnızca teklifi kabul ettiğinizde taahhütte bulunursunuz.",
        "request_quote": "Teklif isteyin", "name": "Adınız", "organisation": "Kurum / Şirket", "email": "İş e-postası",
        "phone": "Telefon / WhatsApp", "product": "Ürün", "any_product": "Herhangi biri / emin değilim",
        "quantity": "Miktar", "need": "Neye ihtiyacınız var?", "need_ph": "Özellikler, teslimat yeri, zamanlama",
        "consent": "Bu talep hakkında benimle iletişime geçilmesini kabul ediyorum. Bkz.", "questions": "Sorular",
        "about": "Hakkımızda",
        "price": "Gösterge fiyatlar birim başına <b>{price} USD</b>'den başlar &mdash; teklifiniz miktara, özelliklere ve teslimata bağlıdır.",
        "warranty": "garanti", "moq": "minimum sipariş", "thanks_title": "Teşekkürler &mdash; talebinizi aldık",
        "thanks_body": "{promise} yazılı bir teklif veya sorularımızla e-posta ile yanıt vereceğiz.",
        "wa_text": "Merhaba, {topic} için teklif almak istiyorum.", "card_cta": "Teklif isteyin",
        "ack_subject": "Talebinizi aldık",
        "ack": "Sayın {name},\n\n{topic}{qty} hakkındaki talebiniz için teşekkür ederiz. Talebinizi aldık; {promise} yazılı bir teklif veya sorularımızla yanıt vereceğiz.\n\nBu e-postayı tam özellikler, teslimat yeri ve zamanlama ile yanıtlayabilirsiniz.\n\nSaygılarımızla,\n{sender}",
        "units": "adet",
        "optout": "Bu e-postaları almak istemiyorsanız 'abonelikten çık' yazarak yanıtlayın",
        "optout_link": " veya şu bağlantıyı kullanın: {link}",
        "err_many": "Çok fazla talep; lütfen daha sonra tekrar deneyin", "err_name": "Lütfen adınızı ve kurumunuzu yazın",
        "err_email": "Lütfen geçerli bir e-posta adresi girin",
        "err_consent": "Lütfen bu talep hakkında sizinle iletişime geçilmesini kabul edin",
        "err_qty": "Miktar bir sayı olmalıdır", "err_qty_range": "Miktar 1 ile 1.000.000 arasında olmalıdır",
    },
    "he": {
        "wa_header": "כתבו לנו בוואטסאפ", "wa_chat": "שוחחו איתנו בוואטסאפ", "wa_continue": "המשך בוואטסאפ",
        "privacy": "הודעת פרטיות", "serving": "משרתים את {country}", "reply": "מענה {promise}",
        "promise": "בתוך יום עסקים אחד", "what_we_supply": "מה אנחנו מספקים", "why_us": "למה קונים עובדים איתנו",
        "how_it_works": "איך זה עובד", "step1_t": "ספרו לנו מה אתם צריכים", "step1_d": "דגמים, כמות ומקום אספקה.",
        "step2_t": "קבלו הצעת מחיר בכתב", "step2_d": "מחיר, מצב, אחריות, זמן אספקה ותנאים, {promise}.",
        "step3_t": "אשרו וקבלו", "step3_d": "אתם מתחייבים רק כשאתם מאשרים את הצעת המחיר.",
        "request_quote": "בקשת הצעת מחיר", "name": "שם", "organisation": "ארגון / חברה", "email": 'דוא"ל בעבודה',
        "phone": "טלפון / וואטסאפ", "product": "מוצר", "any_product": "כל מוצר / לא בטוח", "quantity": "כמות",
        "need": "מה אתם צריכים?", "need_ph": "מפרט, מקום אספקה, לוח זמנים",
        "consent": "אני מסכים/ה שייצרו איתי קשר בנוגע לפנייה זו. ראו", "questions": "שאלות", "about": "אודותינו",
        "price": "מחירים משוערים החל מ-<b>{price} דולר</b> ליחידה &mdash; הצעת המחיר תלויה בכמות, במפרט ובאספקה.",
        "warranty": "אחריות", "moq": "הזמנה מינימלית", "thanks_title": "תודה &mdash; קיבלנו את פנייתכם",
        "thanks_body": 'נחזור אליכם בדוא"ל {promise} עם הצעת מחיר בכתב או שאלות.',
        "wa_text": "שלום, אשמח לקבל הצעת מחיר עבור {topic}.", "card_cta": "בקשת הצעת מחיר",
        "ack_subject": "קיבלנו את פנייתכם",
        "ack": "שלום {name},\n\nתודה על פנייתכם בנושא {topic}{qty}. קיבלנו אותה ונחזור אליכם {promise} עם הצעת מחיר בכתב או שאלות.\n\nאפשר להשיב להודעה זו עם המפרט המדויק, מקום האספקה ולוח הזמנים.\n\nבברכה,\n{sender}",
        "units": "יחידות",
        "optout": "כדי להפסיק לקבל הודעות אלה, השיבו 'הסר'", "optout_link": " או השתמשו בקישור {link}",
        "err_many": "יותר מדי פניות; נסו שוב מאוחר יותר", "err_name": "נא למלא שם וארגון",
        "err_email": 'נא למלא כתובת דוא"ל תקינה', "err_consent": "נא לאשר שניצור איתכם קשר בנוגע לפנייה זו",
        "err_qty": "הכמות חייבת להיות מספר", "err_qty_range": "הכמות חייבת להיות בין 1 ל-1,000,000",
    },
}

# Form errors are raised in English by the intake code; this maps them to the keys above.
ERROR_KEYS = {
    "too many enquiries; please try again later": "err_many",
    "please give your name and organisation": "err_name",
    "please give a valid e-mail address": "err_email",
    "please agree to be contacted about this enquiry": "err_consent",
    "quantity must be a number": "err_qty",
    "quantity must be between 1 and 1,000,000": "err_qty_range",
}

# ------------------------------------------------------------------ opt-out phrases in replies
OPT_OUT_PHRASES = {
    "ar": ["إلغاء الاشتراك", "الغاء الاشتراك", "أزلني", "ازلني", "احذفني", "لا ترسل", "توقف عن الإرسال", "توقفوا عن"],
    "tr": ["abonelikten çık", "aboneliği iptal", "listeden çıkar", "beni çıkar", "göndermeyin", "e-posta göndermeyi bırak"],
    "he": ["הסר", "הסירו", "להסיר אותי", "הפסיקו לשלוח", "בטל מנוי"],
}

# ------------------------------------------------------------------ banned claims
# Same meaning as the English lists in app/policies/fact_check.py and
# app/ads/compliance.py. "regulatory", "relationship" and "license" phrases are
# allowed only when the corresponding fact is verified; "medical" and "ad" never.
CLAIMS = {
    "ar": {
        "medical": ["يشفي", "يعالج جميع", "بدون آثار جانبية", "آمن لجميع المرضى", "مثبت سريريا"],
        "regulatory": ["معتمد من هيئة الغذاء والدواء", "معتمد من fda", "موافقة fda", "علامة ce", "شهادة ce",
                       "معتمد من منظمة الصحة", "شهادة iso", "شهادة الأيزو", "معتمد gmp", "شهادة gmp"],
        "relationship": ["موزع معتمد", "الموزع المعتمد", "وكيل معتمد", "الوكيل المعتمد", "الوكيل الحصري",
                         "وكيل حصري", "شريك حصري", "موزع رسمي", "الموزع الرسمي"],
        "license": ["مستورد مرخص", "موزع مرخص", "مرخص من", "مرخصة من", "رخصة استيراد", "ترخيص استيراد", "مستورد مسجل"],
        "ad": ["مضمون", "أفضل سعر", "أقل سعر", "أرخص", "رقم 1", "رقم واحد", "شحن مجاني", "بدون مخاطر", "لا يقبل المنافسة"],
        "new": ["جديد تماما", "جديدة تماما", "بختم المصنع"],
    },
    "tr": {
        "medical": ["tedavi eder", "iyileştirir", "yan etkisi yok", "klinik olarak kanıtlanmış", "tüm hastalar için güvenli"],
        "regulatory": ["fda onaylı", "fda onayı", "ce belgeli", "ce işaretli", "dsö ön yeterlilik", "iso belgeli",
                       "gmp belgeli", "titck onaylı"],
        "relationship": ["yetkili distribütör", "yetkili bayi", "yetkili satıcı", "münhasır", "resmi distribütör", "resmi satıcı"],
        "license": ["lisanslı ithalatçı", "lisanslı distribütör", "ruhsatlı", "ithalat lisansı", "ithalat ruhsatı", "kayıtlı ithalatçı"],
        # "garantili" is not listed: in Turkish it also means "with a warranty".
        "ad": ["en iyi fiyat", "en düşük fiyat", "en ucuz", "1 numara", "bir numara", "ücretsiz kargo", "risksiz",
               "para iade garantisi", "rakipsiz"],
        "new": ["sıfır ürün", "kapalı kutu", "yepyeni"],
    },
    "he": {
        "medical": ["מרפא מחלות", "מרפא את", "מטפל בכל", "ללא תופעות לוואי", "מוכח קלינית", "בטוח לכל המטופלים"],
        "regulatory": ["מאושר fda", "באישור fda", "אישור fda", "אישור ce", "תו ce", "מאושר משרד הבריאות", "תקן iso",
                       "מאושר gmp"],
        "relationship": ["מפיץ מורשה", "יבואן רשמי", "נציג בלעדי", "שותף בלעדי", "משווק מורשה", "נציג רשמי"],
        "license": ["יבואן מורשה", "בעל רישיון", "בעלת רישיון", "רישיון יבוא", "יבואן רשום"],
        "ad": ["מובטח", "המחיר הטוב ביותר", "המחיר הנמוך ביותר", "הכי זול", "מספר 1", "מספר אחת", "משלוח חינם", "ללא סיכון"],
        "new": ["חדש לגמרי", "חדש באריזה"],
    },
}

# ------------------------------------------------------------------ search and ads
# Local-language buyer research (medical and pharmaceutical lines; the Middle East markets).
PROSPECT_QUERIES = {
    "ar": {"medical_equipment": ["مناقصة توريد أجهزة طبية {country}", "مستشفى مشتريات معدات طبية {country}"],
           "pharmaceutical": ["مناقصة توريد أدوية {country}", "مستودع أدوية موزع {country}"]},
    "tr": {"medical_equipment": ["{country} hastane tıbbi cihaz alım ihalesi", "{country} özel hastane satın alma tıbbi cihaz"],
           "pharmaceutical": ["{country} ilaç alım ihalesi", "{country} ecza deposu"]},
    "he": {"medical_equipment": ["מכרז ציוד רפואי בית חולים {country}", "רכש ציוד רפואי מרפאות {country}"],
           "pharmaceutical": ["מכרז אספקת תרופות {country}", "סיטונאי תרופות {country}"]},
}
PLACE_QUERIES = {
    "ar": {"medical_equipment": ["مستشفى في {country}", "مركز تشخيص طبي في {country}"],
           "pharmaceutical": ["مستودع أدوية في {country}", "صيدلية في {country}"]},
    "tr": {"medical_equipment": ["{country} hastane", "{country} görüntüleme merkezi"],
           "pharmaceutical": ["{country} ecza deposu", "{country} eczane"]},
    "he": {"medical_equipment": ["בית חולים ב{country}", "מכון הדמיה ב{country}"],
           "pharmaceutical": ["סיטונאי תרופות ב{country}", "בית מרקחת ב{country}"]},
}

# Google search-ad keywords (phrase match). Only medical equipment: it is the one
# advertised line that targets these markets (pharmaceuticals are never advertised).
AD_KEYWORDS = {
    "ar": {"medical_equipment": [
        "مورد معدات طبية", "موردين اجهزة طبية", "شركة اجهزة طبية", "معدات مستشفيات", "اجهزة طبية بالجملة",
        "تجهيز مستشفيات", "مورد اجهزة مراقبة المريض", "مورد جهاز سونار", "مورد جهاز تخطيط القلب",
        "اجهزة مختبرات طبية", "معدات طبية {country}", "شركات اجهزة طبية في {country}", "موردين معدات طبية {country}",
    ]},
    "tr": {"medical_equipment": [
        "tıbbi cihaz tedarikçisi", "medikal cihaz toptan", "hastane ekipmanları tedarikçisi", "hasta başı monitörü tedarikçisi",
        "ultrason cihazı tedarikçisi", "ekg cihazı toptan", "laboratuvar cihazları tedarikçisi", "tıbbi cihaz ithalatçısı",
        "hastane donanımı firmaları", "medikal cihaz firmaları",
    ]},
    "he": {"medical_equipment": [
        "ספק ציוד רפואי", "ציוד רפואי לבתי חולים", "ציוד רפואי סיטונאות", "ספק מוניטור רפואי", "ספק מכשיר אולטרסאונד",
        "ספק מכשיר אקג", "ציוד מעבדה רפואי", "יבואן ציוד רפואי", "ציוד למרפאות",
    ]},
}
AD_NEGATIVES = {
    "ar": ["وظائف", "وظيفة", "توظيف", "راتب", "مجانا", "مجاني", "صيانة", "تصليح", "دورة", "تحميل", "ايجار", "اعراض", "علاج", "مستعمل للبيع"],
    "tr": ["iş ilanları", "iş ilanı", "maaş", "ücretsiz", "tamir", "teknik servis", "kurs", "indir", "kiralık", "belirtileri", "tedavisi", "ikinci el"],
    "he": ["דרושים", "משרות", "שכר", "חינם", "תיקון", "קורס", "הורדה", "השכרה", "תסמינים", "טיפול", "יד שנייה"],
}

_AR_MARKS = re.compile(r"[ً-ْـ]")
_AR_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"})


def supported(code: str | None) -> bool:
    return bool(code) and code in LANGUAGES


def is_rtl(code: str | None) -> bool:
    return bool(LANGUAGES.get(code or ENGLISH, {}).get("rtl"))


def language_name(code: str | None) -> str:
    return LANGUAGES.get(code or ENGLISH, LANGUAGES[ENGLISH])["name"]


def normalise(text: str) -> str:
    """Lower-case and fold Arabic spelling variants so phrase matching is not fooled by vowel marks."""
    return _AR_MARKS.sub("", text or "").translate(_AR_ALEF).casefold()


def t(code: str | None, key: str, **values: Any) -> str:
    table = STRINGS.get(code or ENGLISH, STRINGS[ENGLISH])
    text = table.get(key) or STRINGS[ENGLISH].get(key, key)
    return text.format(**values) if values else text


def country_name(code: str | None, country: str) -> str:
    return COUNTRY_NAMES.get(code or ENGLISH, {}).get(country, country)


def category_name(code: str | None, category: str) -> str:
    return CATEGORY_NAMES.get(code or ENGLISH, {}).get(category, category.replace("_", " "))


def promise(code: str | None, configured: str | None) -> str:
    """Your reply-time promise; the default wording is translated, custom wording is shown as you wrote it."""
    if not configured or configured.strip().lower() == DEFAULT_PROMISE:
        return t(code, "promise")
    return configured


def default_language(country: str | None) -> str:
    return COUNTRY_LANGUAGES.get(country or "", ENGLISH)


def language_for(session: Any, country: str | None) -> str:
    """The language NEXUS writes in for a country: your override, else the default, else English."""
    from app.commercial import settings as commercial
    from app.policies.licenses import canonical_country

    if not country:
        return ENGLISH
    name = canonical_country(country)
    code = (commercial.get(session).get("languages") or {}).get(name) or default_language(name)
    return code if supported(code) else ENGLISH


def claim_phrases(code: str | None, kinds: tuple[str, ...]) -> list[tuple[str, str]]:
    """(kind, normalised phrase) pairs for a language."""
    table = CLAIMS.get(code or ENGLISH, {})
    return [(kind, normalise(p)) for kind in kinds for p in table.get(kind, [])]


def opt_out_in(text: str) -> bool:
    """True when a reply contains an opt-out phrase in Arabic, Turkish or Hebrew."""
    folded = normalise(text)
    for code, phrases in OPT_OUT_PHRASES.items():
        for phrase in phrases:
            p = normalise(phrase)
            if re.search(rf"(?<!\w){re.escape(p)}(?!\w)", folded):
                return True
    return False

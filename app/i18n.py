"""Fixed bot messages in Turkish and English (everything else is written by Claude)."""
from __future__ import annotations

from . import config, textutil

TEXTS: dict[str, dict[str, str]] = {
    "tr": {
        "greeting": (
            "Merhaba, ben {bot}. Size uygun çalışabilmem için {total} kısa soru soracağım. Hepsi isteğe bağlı: "
            "bir soruyu atlamak için 'geç', hepsini atlamak için 'kurulumu atla' yazın. "
            "Cevapları yalnızca size hizmet vermek için saklarım; 'neler biliyorsun' diye sorabilir, "
            "'hepsini unut' yazarak sildirebilirsiniz. Yazışmanız yalnızca size yardım etmek için saklanır. "
            "Güven notu: sizin adınıza asla rezervasyon yapmam, bir şey satın almam, ödeme yapmam; hatırlatmalarınız şifreli saklanır; "
            "yazışmalarınızı hizmeti sunan kişi göremez. "
            "Otomatik mesajları (sesli brifing, haber uyarıları) kapatmak için istediğiniz an 'dur' yazın. "
            "For English, reply 'English'."
        ),
        "done": (
            "Teşekkürler, tanışmış olduk. Artık bana yazabilir ya da sesli not gönderebilirsiniz: "
            "hatırlatmalar, hisse takibi ve limit uyarıları, mesaj taslakları, alışveriş linkleri ve daha fazlası."
        ),
        "sensitive_skip": "Güvenliğiniz için kart, IBAN veya şifre gibi bilgileri kaydetmiyorum; bu cevabı atladım.",
        "erase_ask": "Hafızamdaki tüm bilgileri silmek istediğinizden emin misiniz? Onaylamak için EVET yazın.",
        "erase_done": "Tüm kayıtlı bilgileriniz silindi.",
        "working": "Üzerinde çalışıyorum, birkaç saniye daha sürecek.",
        "erase_cancel": "Silme işlemi iptal edildi, hiçbir şey silinmedi.",
        "stop_done": ("Tamam, otomatik mesajları kapattım: sesli brifing ve haber uyarıları artık gelmeyecek. "
                      "Kendi kurduğunuz hatırlatmalar yine zamanında gelir. İstediğiniz an bana yazabilirsiniz."),
        "audio_unavailable": "Ses notlarını şu an yazıya çeviremiyorum, lütfen yazarak iletir misiniz?",
        "audio_failed": "Ses notunu anlayamadım. Tekrar kaydedebilir ya da yazabilir misiniz?",
        "media_unsupported": "Şimdilik yazı, sesli not, fotoğraf ve PDF okuyabiliyorum.",
        "doc_unsupported": "Bu dosya türünü okuyamıyorum. PDF, fotoğraf veya düz metin dosyası gönderebilir misiniz?",
        "file_too_big": "Dosya çok büyük. Daha küçük bir dosya veya birkaç sayfalık bir bölüm gönderebilir misiniz?",
        "file_failed": "Dosyayı açamadım. Bozuk olabilir ya da sayfa sayısı çok fazla (en fazla 100 sayfa). Tekrar gönderir misiniz?",
        "heard": "Duyduğum: “{text}”",
        "tap_below": "Aşağıdaki düğmeye dokunun.",
        "quiet_unparsed": ("Saatleri anlayamadım. Örneğin '23:00-08:00' yazarsanız bu saatlerde otomatik "
                           "mesaj göndermem."),
        "reminder": "Hatırlatma: {text}",
        "reminder_before": "{minutes} dk sonra: {text}",
        "reminder_followup": "Takip: “{text}” halloldu mu? Hallolduysa 'tamam' yazın, ertelemek için ne zaman hatırlatayım?",
        "alert_above": ("{symbol} üst limitinize ulaştı: {price} {cur} (limitiniz {limit}). "
                        "Veriler yaklaşık 15 dk gecikmeli."),
        "alert_below": ("{symbol} alt limitinizin altına indi: {price} {cur} (limitiniz {limit}). "
                        "Veriler yaklaşık 15 dk gecikmeli."),
        "alert_move": ("{symbol} günlük %{pct} eşiğinizi geçti: önceki kapanışa göre {chg} ({price} {cur}). "
                       "Veriler yaklaşık 15 dk gecikmeli."),
        "error_fallback": "Şu anda yanıt veremiyorum, lütfen birazdan tekrar deneyin.",
        "trial_one_day_pay": ("Plus denemenizin bitmesine 1 gün kaldı. Plus'ta sabah sesli brifing, hisse senaryoları ve "
                              "e-posta asistanı var; Basic'te sohbet, hatırlatma ve uçuş arama devam eder. Devam etmek için:\n{howto}"),
        "trial_ended_pay": ("Denemeniz sona erdi. Kesintisiz devam etmek için {days} gün içinde ödeme yapmanız gerekiyor. "
                            "Plus'ta sabah brifingi, hisse senaryoları ve e-posta asistanı da sizinle kalır.\n{howto}"),
        "pay_howto": ("Ödeme banka havalesiyle yapılır.\n{amounts}\nHesap sahibi: {holder}\nBanka: {bank}\nIBAN: {iban}\n"
                      "Açıklamaya şu kodu yazın: {code}\nHavaleden sonra dekontu (ekran görüntüsü ya da PDF) buraya gönderin; "
                      "kontrol edip hemen açarım."),
        "pay_upgrade_hint": "Plus'a geçmek istiyorsanız Plus tutarını gönderin.",
        "pay_before": "Paketiniz {date} tarihinde bitiyor. Devam etmek için:\n{howto}",
        "pay_due": "Paketiniz bugün ({date}) bitiyor. Devam etmek için:\n{howto}",
        "pay_late": ("Ödemeniz gecikti (bitiş tarihi {date}). Birkaç gün içinde ödeme gelmezse hesabınız durdurulacak. "
                     "Ödemek için:\n{howto}"),
        "pay_first": "Devam edebilmem için önce ödemeyi yapmanız gerekiyor.\n{howto}",
        "pay_received": "Ödemeniz alındı, teşekkürler! {plan} paketiniz {date} tarihine kadar açık.",
        "receipt_review": ("Dekontunuzu aldım ama bir kısmını kontrol etmem gerekiyor. {why} "
                           "Bu sırada hesabınız {date} tarihine kadar açık kalacak; kısa süre içinde size dönüş yapılacak."),
        "receipt_wrong_account": "Bu dekont bizim hesabımıza yapılmış görünmüyor. Lütfen şu IBAN'a gönderin:\n{howto}",
        "receipt_used": "Bu dekont daha önce kullanılmış görünüyor. Yeni bir ödeme yaptıysanız onun dekontunu gönderin.",
        "receipt_unreadable": "Dekontu okuyamadım. Daha net bir ekran görüntüsü ya da PDF gönderebilir misiniz?",
        "receipt_not_receipt": "Bu bir havale dekontu gibi görünmüyor. Ödeme yaptıysanız dekontu gönderin.",
        "why_amount": "Tutar beklenenden farklı görünüyor.",
        "why_code": "Açıklamada kodunuzu ({code}) göremedim.",
        "why_date": "Tarih eski ya da okunamıyor.",
        "why_iban": "IBAN'ı net okuyamadım.",
        "why_unreadable": "Dekontu net okuyamadım.",
        "why_ref": "Dekonttaki işlem/referans numarasını göremedim.",
        "test_ping": "Test mesajı: {bot} çalışıyor.",
        "trial_started": ("🎁 Hoş geldiniz! Size {days} gün ücretsiz Plus deneme hakkı tanımladık ({date} tarihine kadar). "
                          "Bu sürede sabah sesli brifingi, grafik taraması, senaryo ve risk hesabı, işlem defteri gibi "
                          "her şey açık. Bol bol deneyin."),
        "trial_one_day": ("Plus denemenizin bitmesine 1 gün kaldı. Sabah brifingi ve piyasa araçları yarın kapanacak. "
                          "Alışkanlık olduysa Plus'ta kalmak için {contact}."),
        "trial_ended": ("Plus denemeniz sona erdi. Basic paketinizde sohbet, hatırlatma, fiyat alarmı, fotoğraf ve PDF "
                        "okuma, fiyatlı uçuş arama hâlâ sizinle. Sabah sesli brifingi ve piyasa araçları kapandı; "
                        "geri açmak için {contact}."),
        "cap_reached": ("Bu ayki {what} hakkınız doldu; gelecek ay yenilenir. Acil bir ihtiyacınız varsa bizimle "
                        "iletişime geçin, paketinizi yükseltebiliriz."),
        "cap_files": "dosya ve fotoğraf okuma",
        "lite_no_media": "Sesli mesaj, fotoğraf ve dosya okuma Basic ve Plus paketlerinde. Yazarak her konuda yardımcı olurum; geçmek isterseniz 'upgrade' yazın.",
        "cap_voice": "sesli not",
        "briefing_unparsed": "Saati anlayamadım. Örneğin '08:30' yazın, istemiyorsanız 'yok' yazın.",
        "briefing_set": "Tamam, sabah brifingi her gün {time}'de sesli not olarak gelecek. İstediğiniz zaman 'brifing' yazarak hemen de isteyebilirsiniz.",
        "briefing_off": "Tamam, sabah brifingi kapalı. Fikrinizi değiştirirseniz bana yazmanız yeterli.",
        "briefing_working": "Brifingi hazırlıyorum, birkaç saniye sürer.",
        "briefing_plus_only": "Sesli sabah brifingi Plus paketine dahil.",
        "briefing_text_footer": "Sesli dinlemek için 'brifing' yazın.",
        "briefing_morning": "Günaydın {name}! İşte günün özeti.",
        "briefing_noun": "sesli brifing",
    },
    "en": {
        "greeting": (
            "Hello, I'm {bot}. To work the way you like I'll ask {total} short questions. All optional: "
            "write 'skip' to skip one, or 'skip setup' to skip them all. I only keep your answers to serve you; "
            "ask 'what do you know about me' any time, and write 'forget everything' to erase it. "
            "Our chat is stored only to help you. A note on trust: I never book, buy or pay anything for you; your reminders are "
            "stored encrypted; the person who runs this service cannot read your chats. Write 'stop' any time to turn off automatic messages "
            "(voice briefing, news alerts). Türkçe için 'Türkçe' yazın."
        ),
        "done": (
            "Thank you, we're all set. You can message me or send voice notes: reminders, stock tracking "
            "with limit alerts, message drafts, shopping links and more."
        ),
        "sensitive_skip": "For your safety I don't store cards, IBANs or passwords, so I skipped that answer.",
        "erase_ask": "Are you sure you want me to erase everything I know about you? Reply YES to confirm.",
        "erase_done": "Everything I had saved about you has been erased.",
        "working": "On it, this will take a few more seconds.",
        "erase_cancel": "Cancelled, nothing was erased.",
        "stop_done": ("Done, I turned off automatic messages: the voice briefing and news alerts will no longer arrive. "
                      "Reminders you set yourself still arrive on time. You can write to me any time."),
        "audio_unavailable": "I can't transcribe voice notes right now, could you type it instead?",
        "audio_failed": "I couldn't understand that voice note. Could you record it again or type it?",
        "media_unsupported": "For now I can read text, voice notes, photos and PDFs.",
        "doc_unsupported": "I can't read that file type. Could you send a PDF, a photo or a plain text file?",
        "file_too_big": "That file is too big. Could you send a smaller file or a few pages of it?",
        "file_failed": "I couldn't open that file. It may be damaged or have too many pages (100 at most). Could you send it again?",
        "heard": "I heard: “{text}”",
        "tap_below": "Tap the button below.",
        "quiet_unparsed": ("I couldn't read those hours. If you write for example '23:00-08:00' I won't send "
                           "automatic messages during that time."),
        "reminder": "Reminder: {text}",
        "reminder_before": "In {minutes} min: {text}",
        "reminder_followup": "Follow-up: did you get “{text}” done? Reply 'done', or tell me when to remind you again.",
        "alert_above": ("{symbol} reached your upper limit: {price} {cur} (your limit {limit}). "
                        "Data is delayed about 15 min."),
        "alert_below": ("{symbol} fell to your lower limit: {price} {cur} (your limit {limit}). "
                        "Data is delayed about 15 min."),
        "alert_move": ("{symbol} passed your daily {pct}% threshold: {chg} vs previous close "
                       "({price} {cur}). Data is delayed about 15 min."),
        "error_fallback": "I can't answer right now, please try again in a moment.",
        "trial_one_day_pay": ("Your Plus trial ends in 1 day. Plus keeps the morning voice briefing, stock scenarios and the "
                              "email assistant; Basic keeps chat, reminders and flight search. To continue:\n{howto}"),
        "trial_ended_pay": ("Your trial has ended. To continue without a break, please pay within {days} days. Plus also "
                            "keeps the morning briefing, stock scenarios and the email assistant.\n{howto}"),
        "pay_howto": ("Payment is by bank transfer.\n{amounts}\nAccount holder: {holder}\nBank: {bank}\nIBAN: {iban}\n"
                      "Write this code in the description: {code}\nAfter you transfer, send the receipt (screenshot or PDF) "
                      "here and I will check it and switch you on right away."),
        "pay_upgrade_hint": "To move to Plus, send the Plus amount.",
        "pay_before": "Your plan ends on {date}. To continue:\n{howto}",
        "pay_due": "Your plan ends today ({date}). To continue:\n{howto}",
        "pay_late": ("Your payment is late (it was due {date}). If it does not arrive in a few days your account will be "
                     "paused. To pay:\n{howto}"),
        "pay_first": "Please pay first so I can continue.\n{howto}",
        "pay_received": "Payment received, thank you! Your {plan} plan is active until {date}.",
        "receipt_review": ("I received your receipt but I need to check part of it. {why} Meanwhile your account stays on "
                           "until {date}, and you will hear back soon."),
        "receipt_wrong_account": "This receipt does not look like it went to our account. Please send it to this IBAN:\n{howto}",
        "receipt_used": "This receipt looks like it was already used. If you made a new payment, send that receipt.",
        "receipt_unreadable": "I could not read the receipt. Could you send a clearer screenshot or the PDF?",
        "receipt_not_receipt": "That does not look like a bank transfer receipt. If you paid, please send the receipt.",
        "why_amount": "The amount looks different from what was expected.",
        "why_code": "I could not see your code ({code}) in the description.",
        "why_date": "The date is old or unreadable.",
        "why_iban": "I could not read the IBAN clearly.",
        "why_unreadable": "I could not read the receipt clearly.",
        "why_ref": "I could not see the bank reference number on the receipt.",
        "test_ping": "Test message: {bot} is working.",
        "trial_started": ("🎁 Welcome! You have a free {days}-day Plus trial (until {date}). Everything is on: the "
                          "morning voice briefing, chart scans, scenarios and risk maths, the trade journal. Try it all."),
        "trial_one_day": ("Your Plus trial ends in 1 day. The morning briefing and the market tools switch off "
                          "tomorrow. If you've got used to them, to stay on Plus {contact}."),
        "trial_ended": ("Your Plus trial has ended. Your Basic plan keeps chat, reminders, price alerts, photo and PDF "
                        "reading and flight search with prices. The morning voice briefing and the market tools are "
                        "off now; to switch them back on {contact}."),
        "cap_reached": ("You've used this month's allowance for {what}; it renews next month. If it's urgent, get in "
                        "touch and we can upgrade your plan."),
        "cap_files": "reading files and photos",
        "lite_no_media": "Voice notes, photos and files are part of Basic and Plus. I am happy to help in writing; write 'upgrade' to move up.",
        "cap_voice": "voice notes",
        "briefing_unparsed": "I couldn't read that time. Write for example '08:30', or 'no' if you don't want one.",
        "briefing_set": "Done: your morning briefing arrives every day at {time} as a voice note. You can also write 'briefing' any time to get it right away.",
        "briefing_off": "OK, the morning briefing is off. Just tell me if you change your mind.",
        "briefing_working": "Preparing your briefing, it takes a few seconds.",
        "briefing_plus_only": "The audio morning briefing is part of the Plus plan.",
        "briefing_text_footer": "Reply 'briefing' to hear it as audio.",
        "briefing_morning": "Good morning {name}! Here is your day.",
        "briefing_noun": "audio briefings",
    },
}

QUESTIONS: list[dict[str, str]] = [
    {"key": "name_lang", "label": "Name and preferred language", "section": "about",
     "tr": "Size nasıl hitap edeyim (ilk adınız yeterli) ve hangi dilde yazalım (Türkçe / English)?",
     "en": "What should I call you (first name is enough), and which language should we use (Türkçe / English)?"},
    {"key": "style", "label": "Reply style", "section": "preferences",
     "tr": "Yanıtlarım nasıl olsun: kısa mı detaylı mı, resmi mi samimi mi?",
     "en": "How should I reply: short or detailed, formal or casual?"},
    {"key": "goals", "label": "Wants help with", "section": "goals",
     "tr": "En çok neyde yardım istersiniz (hatırlatmalar, piyasa takibi, alışveriş, belgeler, seyahat...)?",
     "en": "What do you most want help with (reminders, markets, shopping, documents, travel...)?"},
    {"key": "quiet", "label": "Do not disturb times", "section": "preferences",
     "tr": "Hangi saatlerde size mesaj atmamalıyım (örneğin 23:00-08:00)?",
     "en": "During which hours should I not message you (for example 23:00-08:00)?"},
    {"key": "stocks", "label": "Stocks followed (client's words)", "section": "stocks",
     "tr": ("Takip ettiğiniz hisseler var mı? Her biri için üst ve alt fiyat limiti verirseniz limit aşılınca "
            "haber veririm (örnek: THYAO üst 320, alt 290). Yoksa 'geç' yazın."),
     "en": ("Do you follow any stocks? Give an upper and lower price limit for each and I'll alert you when "
            "one is crossed (example: NVDA upper 150, lower 120). Or write 'skip'.")},
]
TOTAL_QUESTIONS = len(QUESTIONS)  # Basic plan; Plus clients get one more (BRIEFING_QUESTION)

BRIEFING_QUESTION: dict[str, str] = {
    "key": "briefing", "label": "Morning audio briefing time", "section": "preferences",
    "tr": ("Plus paketinizde her sabah sesli bir özet gönderebilirim: piyasalar, takip ettiğiniz hisseler ve günün "
           "hatırlatmaları. Saat kaçta isterseniz (örnek: 08:30)? İstemezseniz 'yok' yazın."),
    "en": ("With your Plus plan I can send a spoken summary every morning: markets, your stocks and today's "
           "reminders. At what time would you like it (example: 08:30)? Write 'no' if you don't want it."),
}


WORK_QUESTION: dict[str, str] = {
    "key": "work", "label": "Area of work", "section": "work",
    "tr": ("Sabah brifingine alanınızla ilgili haberleri de ekleyeyim: hangi alanda çalışıyorsunuz "
           "(örnek: inşaat, e-ticaret, bankacılık, yazılım, sağlık)? İstemezseniz 'geç' yazın, yalnızca genel gündemi gönderirim."),
    "en": ("I'll also put news from your field in the morning briefing: what area do you work in "
           "(example: construction, e-commerce, banking, software, healthcare)? Write 'skip' for general news only."),
}


def questions_for(plan: str) -> list[dict[str, str]]:
    if plan == "lite":  # no stocks, no briefing; the quiet-hours question still matters for nothing proactive, so it goes too
        return [q for q in QUESTIONS if q["key"] in ("name_lang", "style", "goals")]
    return QUESTIONS + [BRIEFING_QUESTION, WORK_QUESTION] if plan == "plus" else QUESTIONS


def total_for(plan: str) -> int:
    return len(questions_for(plan))

YES = {"evet", "yes", "onay", "onayliyorum", "confirm"}
SKIP = {"gec", "atla", "skip", "pass"}
SKIP_ALL = {"kurulumu atla", "skip setup", "skip all", "hepsini gec", "sorulari atla"}
BRIEFING_NOW = {"brifing", "briefing", "sesli brifing", "audio briefing", "brifing ver", "brifing gonder",
                "sabah brifingi", "morning briefing", "my briefing", "brifingim"}
STOP = {"dur", "durdur", "stop", "bildirimleri kapat", "mesajlari durdur", "otomatik mesajlari kapat",
        "stop messages", "stop all", "unsubscribe", "abonelikten cik"}
ERASE = {"hepsini unut", "tum hafizami sil", "tum hafizayi sil", "verilerimi sil", "beni unut",
         "forget everything", "erase my data", "delete my data", "forget me"}


def t(lang: str, key: str, **kw) -> str:
    table = TEXTS.get(lang) or TEXTS["tr"]
    text = table.get(key) or TEXTS["tr"][key]
    kw.setdefault("total", TOTAL_QUESTIONS)
    return text.format(bot=config.BOT_NAME, **kw)


def question(lang: str, number: int, plan: str = "basic") -> str:
    qs = questions_for(plan)
    q = qs[number - 1]
    return f"{number}/{len(qs)} — {q.get(lang) or q['tr']}"


def clean(text: str) -> str:
    """Folded, trimmed form used to recognise commands like 'geç' or 'Hepsini unut!'."""
    return textutil.fold(text).strip(" \t\n.!?,;:'\"")


def lang_from_answer(text: str, current: str) -> str:
    f = textutil.fold(text)
    if "english" in f or "ingilizce" in f:
        return "en"
    if "turkce" in f or "turkish" in f:
        return "tr"
    return current

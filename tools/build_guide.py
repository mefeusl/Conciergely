# Builds the Turkish customer guide PDF. Needs: pip install reportlab, and the DejaVu fonts (fonts-dejavu-core). Run: python tools/build_guide.py
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle,
                                PageBreak, KeepTogether)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

pdfmetrics.registerFont(TTFont("DV", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DVB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
pdfmetrics.registerFont(TTFont("DVI", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"))
from reportlab.pdfbase.pdfmetrics import registerFontFamily
registerFontFamily("DV", normal="DV", bold="DVB", italic="DVI", boldItalic="DVB")

INK = colors.HexColor("#1c2530"); ACC = colors.HexColor("#0b6b57"); SOFT = colors.HexColor("#eef5f2")
GREY = colors.HexColor("#5b6670"); LINE = colors.HexColor("#cfd8d4"); WARN = colors.HexColor("#fff4e0"); WARNL = colors.HexColor("#e6b25c")

body = ParagraphStyle("body", fontName="DV", fontSize=9.6, leading=14, textColor=INK, spaceAfter=4)
small = ParagraphStyle("small", parent=body, fontSize=8.6, leading=12, textColor=GREY)
cell = ParagraphStyle("cell", parent=body, fontSize=8.8, leading=12, spaceAfter=0)
cellb = ParagraphStyle("cellb", parent=cell, fontName="DVB")
head = ParagraphStyle("head", parent=cellb, textColor=colors.white)
h1 = ParagraphStyle("h1", fontName="DVB", fontSize=15, leading=19, textColor=ACC, spaceBefore=12, spaceAfter=6, keepWithNext=1)
h2 = ParagraphStyle("h2", fontName="DVB", fontSize=11.5, leading=15, textColor=INK, spaceBefore=8, spaceAfter=4)
title = ParagraphStyle("title", fontName="DVB", fontSize=28, leading=33, textColor=ACC, spaceAfter=4)
sub = ParagraphStyle("sub", fontName="DV", fontSize=12.5, leading=18, textColor=GREY, spaceAfter=10)
bullet = ParagraphStyle("bullet", parent=body, leftIndent=12, bulletIndent=2, spaceAfter=2)

def P(t, s=cell): return Paragraph(t, s)
def bullets(items): return [Paragraph(t, bullet, bulletText="•") for t in items]

def table(rows, widths, header):
    data = [[P(h, head) for h in header]] + [[P(c, cellb if i == 0 else cell) for i, c in enumerate(r)] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    st = [("BACKGROUND", (0, 0), (-1, 0), ACC), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
          ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
    for i in range(2, len(data), 2): st.append(("BACKGROUND", (0, i), (-1, i), SOFT))
    t.setStyle(TableStyle(st))
    return t

def box(text_items, bg=WARN, edge=WARNL):
    t = Table([[text_items]], colWidths=[174 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("BOX", (0, 0), (-1, -1), 0.8, edge),
                           ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    return t

def deco(canvas, doc):
    canvas.saveState()
    canvas.setFont("DV", 8); canvas.setFillColor(GREY)
    canvas.drawString(18 * mm, 10 * mm, "Conciergely Kullanım Kılavuzu")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Sayfa {doc.page}")
    canvas.setStrokeColor(LINE); canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
    canvas.restoreState()

doc = BaseDocTemplate("Conciergely-Kullanim-Kilavuzu.pdf", pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                      topMargin=16 * mm, bottomMargin=20 * mm, title="Conciergely Kullanım Kılavuzu",
                      author="Conciergely", subject="WhatsApp kişisel asistanı: özellikler ve kullanım")
doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(18 * mm, 20 * mm, A4[0] - 36 * mm, A4[1] - 36 * mm, id="f", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=deco)])

W = [48 * mm, 70 * mm, 56 * mm]
S = []
S += [Spacer(1, 6 * mm), P("Conciergely", title), P("WhatsApp'taki kişisel asistanınız: neler yapar, nasıl kullanılır, neler yapmaz", sub)]
S += [P("Conciergely, WhatsApp'ta yanınızda duran kişisel asistanınızdır. Ona bir arkadaşınıza yazar gibi yazabilir, sesli not bırakabilir, fotoğraf ya da belge gönderebilirsiniz. "
        "Hatırlatmalarınızı tutar, belgelerinizi özetler, uçuşları fiyatlarıyla bulur, hava durumunu söyler ve yatırım düşüncelerinizde size sayılarla eşlik eder. "
        "Türkçe ve İngilizce anlar; siz hangi dilde yazarsanız o dilde yanıtlar.", body), Spacer(1, 3)]
S += [box([P("<b>Önemli:</b> Conciergely bir yatırım danışmanı, avukat ya da vergi uzmanı değildir. Sizin yerinize hiçbir şey satın almaz, satmaz, rezerve etmez ya da ödemez. "
              "Piyasa bölümündeki her şey bilgi ve hesaplama aracıdır; al, sat ya da tut tavsiyesi değildir.", cell)])]

S += [P("1. Başlarken", h1)]
S += bullets(["Conciergely'yi telefonunuzdaki WhatsApp'tan, sıradan bir kişiye yazar gibi kullanırsınız. Uygulama kurmanız gerekmez.",
              "İlk yazışmada sizi tanımak için birkaç kısa soru sorar. Hepsi isteğe bağlıdır; <b>geç</b> yazarak atlayabilirsiniz.",
              "Yazı, sesli not, fotoğraf, PDF, metin ve CSV dosyası gönderebilirsiniz.",
              "Yanıt hazırlanırken WhatsApp'ta <b>yazıyor…</b> işareti görünür."])
S += [box([P("<b>İlk 7 gün Plus hediye.</b> İlk mesajınızla birlikte 7 günlük ücretsiz Plus denemeniz başlar. Kredi kartı ya da başka bir şey gerekmez. "
              "Sabah sesli brifingi, grafik taraması, senaryo ve risk hesabı gibi her şeyi denersiniz. Bitmesine 1 gün kala asistan size haber verir; "
              "denemeden sonra Basic paketiniz sorunsuz devam eder.", cell)], bg=SOFT, edge=ACC)]

S += [P("2. Basic neler sunar, Plus neyi değiştirir?", h1)]
S += [P("<b>Basic</b> günlük hayatınızı kolaylaştırır: sohbet, hatırlatma, hafıza, fiyat sorgusu ve alarmı, fotoğraf ve PDF okuma, sesli not, hava durumu, yol tarifi, "
        "uçuş durumu ve <b>fiyatlı uçuş arama</b> Basic pakete dahildir.", body)]
S += [P("<b>Plus</b> ise asistanı günlük yardımcıdan, gününüzü sizin yerinize hazırlayan bir ekip arkadaşına dönüştürür:", body)]
S += bullets(["<b>Güne hazır uyanın.</b> Her sabah seçtiğiniz saatte asistan size piyasaları, takip ettiğiniz hisseleri, günün hatırlatmalarını ve haberleri toplayıp "
              "önce yazı, sonra <b>sesli not</b> olarak gönderir. Kahvenizi içerken dinlersiniz. Haberlerin bir kısmı sizin çalışma alanınıza göre seçilir.",
              "<b>Yatırım kararlarınızda net olun.</b> Her akşam yüzlerce hissede grafik taraması, bir desenin geçmişte nasıl çalıştığı, "
              "giriş, zarar kes ve hedefinizin ödül/risk hesabı, binlerce olası geleceğin denendiği senaryo analizi.",
              "<b>Hatalarınızdan öğrenin.</b> İşlem defteri ve karar günlüğü, hangi kararlarınızda ne kadar isabetli olduğunuzu zamanla gösterir.",
              "<b>Siz bakmadan haberiniz olsun.</b> İzleme listenizdeki hisseler için önemli haberleri kendiliğinden bildirir.",
              "<b>Her şey tek yerde, WhatsApp'ta.</b> Yeni bir uygulama, yeni bir hesap ya da yeni bir alışkanlık yok."])
S += [Spacer(1, 3), box([P("<b>Karar vermeden önce deneyin.</b> Plus'ı 7 gün boyunca ücretsiz kullanırsınız. Brifing alışkanlığa dönüşürse deneme bitmeden "
                           "“PLUS” yazmanız yeterli; isteğiniz bize ulaşır.", cell)], bg=SOFT, edge=ACC)]

S += [P("3. Günlük işleriniz", h1)]
rows = [
 ["Hatırlatma", "Tam saatinde size mesaj atar. İsterseniz öncesinde haber verir, sonrasında nasıl geçtiğini sorar.",
  "“Yarın 15:00 toplantı var, yarım saat önce hatırlat, bir saat sonra nasıl geçti diye sor”"],
 ["Sesli not", "Sesli mesajınızı anlar, ne duyduğunu yazar ve hatırlatmaya çevirir.", "Sesli not: “Cuma günü annemi aramayı hatırlat”"],
 ["Sessiz saatler", "Belirlediğiniz saatlerde otomatik mesaj (alarm, haber, takip) göndermez. Tam saat belirttiğiniz hatırlatmalar yine gelir.",
  "“Gece 11 ile sabah 8 arası beni rahatsız etme”"],
 ["Hafıza", "Önemli bilgileri ve tercihlerinizi hatırlar. Ne bildiğini sorabilir, istediğinizi sildirebilirsiniz.",
  "“Eşimin doğum günü 12 Mart, hatırla” / “Benim hakkımda ne biliyorsun?” / “Hepsini unut”"],
 ["Fotoğraf okuma", "Fişi, kartviziti, el yazısı listeyi ya da bir grafiği okur. Fişten satıcıyı, tarihi ve tutarı çıkarır; listeyi göreve çevirir.",
  "Fotoğraf gönderin, isterseniz altına bir soru yazın"],
 ["Belge özeti", "İlettiğiniz PDF, metin ya da CSV dosyasını tam üç maddede özetler: belgenin ne olduğu, önemli sayılar ve tarihler, ana risk. Sözleşme, bilanço, dekont ve ekstre için uygundur.",
  "PDF'i WhatsApp'tan iletin"],
 ["Konum paylaşımı", "WhatsApp'ta ataç simgesi, sonra Konum ile bulunduğunuz yeri gönderin. Conciergely konumu yalnızca o yanıt için kullanır, kaydetmez: hava durumu, buradan yol tarifi ya da yakındaki mekanlar için.",
  "Konum gönderin + “Burada hava nasıl?” / “Buradan Taksim'e metroyla” / “Yakında kahve”"],
 ["Hava durumu", "Anlık hava ve 3 günlük görünüm (sıcaklık, hissedilen, yağış ihtimali, rüzgar).", "“Kadıköy'de yarın hava nasıl?”"],
 ["Canlı bilgi", "Kur, güncel haberler, çalışma saatleri ve benzeri bilgileri internetten arar ve kaynağını söyler.", "“Bugün dolar kaç?”"],
 ["Sabah sesli brifing (Plus)", "Her gün seçtiğiniz saatte piyasaları, takip ettiğiniz hisseleri, günün hatırlatmalarını, genel gündemden üç haberi ve çalışma alanınızla ilgili üç haberi önce yazı, sonra sesli not olarak gönderir. İstediğiniz an <b>brifing</b> yazarak da isteyebilirsiniz. Alım-satım tavsiyesi içermez.",
  "“brifing” / “Sabah brifingini 07:30'a al” / “Brifingi kapat”"],
 ["Fiyatlı uçuş arama", "İki şehir arasındaki gerçek uçuş seçeneklerini fiyat, havayolu, saat ve aktarma bilgisiyle listeler; en uygun olanı öne çıkarır. Rezervasyon bağlantısı en sonda gelir, rezervasyonu siz yaparsınız.",
  "“20 Kasım İstanbul İzmir uçuşlarını göster”"],
 ["Uçuş durumu", "Bir uçuş numarasının saat, gecikme ve terminal bilgisini söyler.", "“TK1971 bugün ne durumda?”"],
 ["Tek dokunuş düğmeleri", "Yol tarifi, uçuş, otel, harita ve alışveriş için tek bir düğme gönderir. Düğmeye dokunur, işlemi kendi uygulamanızda tamamlarsınız.",
  "“Kadıköy'den Taksim'e metroyla yol tarifi” / “Trendyol'da siyah spor ayakkabı bul”"],
]
S += [table(rows, W, ["Özellik", "Ne yapar", "Nasıl söylersiniz"])]

S += [P("4. Plus ile gelen piyasa araçları (bilgi ve hesaplama, tavsiye değil)", h1)]
S += [P("Aşağıdaki araçlar fiyatları, geçmiş davranışı ve riski sayılarla gösterir. Conciergely hiçbir zaman “al”, “sat” ya da “tut” demez ve bir fiyatın ne olacağını söylemez. "
        "Fiyat verileri yaklaşık 15 dakika gecikmelidir.", body)]
rows = [
 ["Fiyat", "Borsa İstanbul ve ABD hisselerinin güncel fiyatı.", "“THYAO ne kadar?” / “NVDA price”"],
 ["Fiyat alarmı", "Fiyat üst ya da alt sınırınızı geçince, siz bakmasanız da haber verir.", "“THYAO 320 üstüne çıkarsa ya da 290 altına inerse haber ver”"],
 ["Grafik taraması", "Her akşam birkaç yüz hisseyi bilinen grafik desenleri için tarar (kırılım, aşırı satımdan dönüş, altın kesişim, sıkışmadan çıkış, yükselişte geri çekilme). Her desenin geçmişte nasıl çalıştığını ve kaç örneğe dayandığını da gösterir.",
  "“Bugünkü grafik taraması ne gösteriyor?”"],
 ["Geçmiş testi", "Bir desenin tek bir hissede son 2 yılda nasıl sonuç verdiğini, komisyon payı düşülmüş olarak hesaplar. 30'dan az örnek varsa güvenilmez olduğunu belirtir.",
  "“THYAO'da geri çekilme deseni geçmişte nasıl çalışmış?”"],
 ["Risk hesabı", "Giriş, zarar kes ve hedef fiyatınızdan ödül/risk oranını, başa baş için gereken isabet oranını ve işlem büyüklüğünü hesaplar.",
  "“120'den giriş, 114 stop, 138 hedef: riski hesapla” / “Hesabım 100.000 TL, işlem başı %1 risk, giriş 50, stop 48”"],
 ["Hisse senaryoları", "Hissenin kendi geçmişinden binlerce olası gelecek üretir; hedefe mi yoksa zarar kese mi önce ulaşılacağının sıklığını ve olası düşüşleri gösterir. Bu bir stres testidir, tahmin değildir.",
  "“NVDA için 30 günlük senaryo, stop 114, hedef 138”"],
 ["Seviyeler", "Trend, RSI, dalgalanma, son değişimler, 52 haftalık konum ve yakın destek/direnç seviyelerini verir.", "“THYAO seviyeleri neler?”"],
 ["Haberler", "Bir hisse ya da konu hakkındaki son haberleri özetler.", "“THYAO haberleri”"],
 ["Haber uyarısı", "İsterseniz izleme listenizdeki ve portföyünüzdeki hisseler için önemli haberleri (bilanço, temettü, dava, satın alma vb.) kendiliğinden bildirir. Günde en fazla 3, sessiz saatlerde asla.",
  "“Haber uyarılarını aç” / “kapat”"],
 ["Piyasa özeti", "Dolar/TL, Euro/TL, BIST 100, S&amp;P 500, Nasdaq, VIX, altın, Brent petrol ve ABD 10 yıllık faiz tek bakışta.", "“Piyasa özeti”"],
 ["Ön-ölüm analizi", "Bir işleme girmeden önce nasıl ters gidebileceğine dair üç yol ve erken uyarı işaretlerini sıralar.", "“THYAO'yu 300'den alacağım, ne ters gidebilir?”"],
 ["İşlem defteri", "Yaptığınız alım-satımları kaydeder, ortalama maliyeti ve kâr/zararı gösterir. Hiçbir aracı kuruma bağlanmaz.", "“THYAO'dan 310 TL'den 100 adet aldım” / “Portföyüm”"],
 ["Karar günlüğü", "İşlemden önce gerekçenizi ve ne kadar emin olduğunuzu yazarsınız; sonra sonuçla karşılaştırıp güveninizin ne kadar isabetli olduğunu gösterir.",
  "“NVDA için günlük: yapay zeka talebi, yüzde 70 eminim, giriş 120, 2 hafta sonra kontrol” / “Günlük istatistiğim”"],
]
S += [table(rows, W, ["Araç", "Ne yapar", "Nasıl söylersiniz"])]

S += [P("5. Conciergely neleri yapmaz", h1)]
S += bullets(["Al, sat ya da tut demez; bir fiyatın yükseleceğini, düşeceğini ya da bir işlemin “garanti” ya da “güvenli” olduğunu söylemez.",
              "Sizin adınıza alım-satım, rezervasyon, sipariş ya da ödeme yapmaz. Yalnızca bağlantıyı hazırlar; işlemi siz uygulamada tamamlarsınız.",
              "Takviminizi, e-postanızı ya da banka hesaplarınızı okuyamaz.",
              "Şifre, kart numarası, IBAN, kimlik numarası ve sağlık bilgisi kaydetmez.",
              "Hukuki, vergisel ya da yatırım danışmanlığı vermez.",
              "Konumunuzu kaydetmez; paylaştığınız konum yalnızca o cevabı hazırlamak için kullanılır. Sürekli konum takibi yapmaz.",
              "Telefon araması yapmaz; yazı ve sesli not üzerinden çalışır.",
              "Fotoğraflardaki kişileri yüzlerinden tanımaz; tıbbi teşhis koymaz.",
              "Belgelerin ya da haberlerin içindeki “şunu yap” gibi cümlelere uymaz; onları sadece veri olarak okur.",
              "Word, Excel, zip, video ve çıkartma dosyalarını okuyamaz; PDF, metin, CSV ve fotoğraf destekler."])

S += [P("6. Bilmeniz gereken sınırlar", h1)]
S += bullets(["<b>Gecikmeli veri:</b> Fiyatlar Yahoo Finance'ten gelir ve yaklaşık 15 dakika gecikmelidir. Lisanslı gerçek zamanlı veri değildir; ara sıra eksik ya da hatalı olabilir.",
              "<b>Tarama kapsamı:</b> Grafik taraması birkaç yüz hisseyi kapsar (yaklaşık 90 Borsa İstanbul ve 150 ABD), binlerce değil.",
              "<b>Geçmiş gelecek değildir:</b> Geçmiş test ve senaryolar haber, boşluk ve rejim değişikliğini bilemez. Az örneğe dayanan sonuçlar “güvenilmez” diye işaretlenir.",
              "<b>Uçuş fiyatları</b> çok hızlı değişir; gördüğünüz fiyat rezervasyon anında farklı olabilir. Rezervasyonu havayolunda ya da seyahat sitesinde yaparsınız.",
              "<b>Kullanım:</b> Normal kullanımın bir sınırı yoktur. Kısa sürede art arda çok uzun mesaj göndermek gibi kötüye kullanım belirtileri görülürse hesap güvenlik için otomatik olarak durdurulur; devam etmek için hizmet sağlayıcınızla telefonda görüşmeniz gerekir.",
              "<b>Brifing:</b> 24 saatten uzun süredir yazmadıysanız brifing yalnızca yazı olarak gelir. Sesli not gönderilemezse yazı yine de size ulaşır.",
              "Yoğun anlarda yanıt birkaç saniye gecikebilir."])

S += [P("7. Verileriniz", h1)]
S += bullets(["Notlarınız yalnızca size yardımcı olmak için tutulur.",
              "Ne bildiğini istediğiniz an sorabilirsiniz: <b>“Benim hakkımda ne biliyorsun?”</b>",
              "Belirli bir bilgiyi sildirmek için: <b>“Ayşe'yi unut”</b>. Her şeyi silmek için: <b>“Hepsini unut”</b> yazın ve EVET ile onaylayın.",
              "Gönderdiğiniz fotoğraf ve belgeler diske kaydedilmez; yalnızca yanıt hazırlanırken işlenir, sohbet geçmişinde kısa bir not kalır."])

S += [P("8. Hemen deneyin", h1)]
rows = [["THYAO ne kadar?", "Fiyat ve gecikme notu"], ["5 dakika sonra su içmemi hatırlat", "Tam saatli hatırlatma"],
        ["Bir fiş fotoğrafı gönderin", "Satıcı, tarih, tutar"], ["Bir PDF iletin", "Üç maddelik özet"],
        ["Konum gönderin, “burada hava nasıl?” yazın", "Hava durumu"], ["brifing (Plus)", "Yazılı özet + sesli not"],
        ["Kadıköy'den Taksim'e metroyla yol tarifi", "Yol tarifi düğmesi"], ["20 Kasım İstanbul İzmir uçuşlarını göster", "Fiyatlı uçuş listesi (Basic'te de var)"],
        ["120'den giriş, 114 stop, 138 hedef: riski hesapla", "Ödül/risk ve başa baş oranı"], ["THYAO almalı mıyım?", "Karar vermez, sayıları gösterir"], ["PLUS", "Plus isteğinizi bize iletir"]]
S += [table(rows, [100 * mm, 74 * mm], ["Şunu yazın", "Sonuç"])]
S += [Spacer(1, 8), box([P("<b>Yasal uyarı:</b> Bu kılavuz ve Conciergely'nin piyasa araçları yalnızca bilgilendirme ve hesaplama amaçlıdır. Yatırım danışmanlığı kapsamında değildir. "
                         "Yatırım kararlarınızın sorumluluğu size aittir; kararlarınızı vermeden önce yetkili bir uzmana danışın.", cell)])]
doc.build(S)
print("ok")

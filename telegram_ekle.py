#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Dinleyici
------------------
Telegram grubuna atilan linkleri okur ve urunler.csv dosyasina ekler.

Desteklenen kullanim (gruba yazilir):
    https://site.com/urun                 -> linki ekler, adi linkten uretir
    nevresim https://site.com/urun        -> "nevresim" adiyla ekler
    buzdolabi 25000 https://site.com/x    -> hedef fiyat 25000 olarak ekler
    (tek mesajda alt alta en fazla 5 link gonderilebilir)
    /liste                                -> takip edilen urunleri listeler
    /sil 3                                -> 3 numarali urunu siler

Akakce arama baglantisi, linkin kendi icindeki urun adindan uretilir.
Sayfa indirilmez; bu yuzden bot korumasi olan sitelerde de calisir.
"""

import csv
import html as html_mod
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

KOK = Path(__file__).resolve().parent
URUNLER_DOSYASI = KOK / "urunler.csv"
DURUM_DOSYASI = KOK / "telegram_durum.json"

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
SOHBET = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

LINK_DESENI = re.compile(r"https?://\S+")

# URL yolunda urun adi tasimayan parcalar
YOL_GURULTUSU = {
    "urun", "urunler", "product", "products", "p", "dp", "gp", "detay",
    "detail", "item", "items", "tr", "www", "shop", "magaza", "pd", "c",
    "yorumlar", "yorum", "sepet", "sepete", "satici", "saticilar",
    "kampanya", "kampanyalar", "fiyat", "fiyatlari", "ürün", "sayfa",
}
# Urun adinda ise yaramayan kelimeler
KELIME_GURULTUSU = {
    "p", "dp", "urun", "product", "html", "htm", "aspx", "php",
    "ve", "ile", "icin", "için",
}

# 160x200 gibi olculer korunur, kmc12345 gibi kayit numaralari atilir
OLCU = re.compile(r"^\d+[xX]\d+([xX]\d+)?$")

# HBCV00004ABCDE / B08XYZ1234 gibi stok kodlari
URUN_KODU = re.compile(r"^(?=.*\d)[A-Za-z0-9]{8,}$")

UZUN_ESIK = 7          # bu kadar kelimeden sonrasi bas+son olarak kisaltilir
MAKS_LINK = 5          # tek mesajda islenecek en fazla link sayisi


# ----------------------------------------------------------------------------
# Telegram
# ----------------------------------------------------------------------------
def api(metot, veri=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{metot}"
    gonderi = urllib.parse.urlencode(veri).encode() if veri else None
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=gonderi), timeout=25) as y:
            return json.loads(y.read().decode())
    except Exception as hata:
        print(f"!! Telegram API hatasi ({metot}): {hata}")
        return {"ok": False}


def kacir(metin):
    """Telegram HTML modunda guvenli olmasi icin & < > karakterlerini kacirir."""
    return html_mod.escape(str(metin), quote=False)


def cevap_yaz(metin):
    yanit = api("sendMessage", {
        "chat_id": SOHBET,
        "text": metin[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    })
    if not yanit.get("ok"):
        print(f"!! Mesaj gonderilemedi: {yanit}")
    return yanit.get("ok", False)


def parcali_gonder(satirlar, baslik):
    """Telegram 4096 karakter sinirini asmadan parcalar halinde gonderir."""
    if not satirlar:
        return
    tampon = baslik
    for satir in satirlar:
        satir = satir[:1200]
        if len(tampon) + len(satir) > 3500:
            cevap_yaz(tampon)
            tampon = ""
        tampon += satir
    if tampon.strip():
        cevap_yaz(tampon)


# ----------------------------------------------------------------------------
# Linkten urun adi cikarma
# ----------------------------------------------------------------------------
def _yol_parcalari(link):
    try:
        yol = urllib.parse.urlparse(link).path
    except ValueError:
        return []
    yol = urllib.parse.unquote(yol)          # %C3%A7 -> ç
    return [p for p in yol.split("/") if p]


def _kelimeleri_ayikla(parca):
    """
    Bir URL parcasini temiz kelime listesine cevirir.
    Model numaralari korunur (Arcelik 270561), sondaki kayit numaralari atilir
    (... -p-12345678).
    """
    parca = re.sub(r"\.(html?|aspx|php)$", "", parca, flags=re.IGNORECASE)
    ham = [k.strip() for k in re.split(r"[-_+.]+", parca) if k.strip()]

    kelimeler = []
    for sira, kelime in enumerate(ham):
        son_mu = (sira == len(ham) - 1)

        if kelime.lower() in KELIME_GURULTUSU:
            continue
        if len(kelime) == 1 and not kelime.isdigit():
            continue                          # "-p-" gibi ayiraclar
        if OLCU.match(kelime):
            kelimeler.append(kelime)          # 160x200 gibi olculer
            continue
        if kelime.isdigit():
            # Sondaki uzun sayi = urun kayit numarasi, at.
            # Ortadaki sayi = model numarasi, tut (Arcelik 270561).
            if (son_mu and len(kelime) >= 4) or len(kelime) >= 9:
                continue
        elif re.search(r"\d", kelime) and kelime.isalnum():
            # Harf+rakam karisimi: uzunsa her yerde, kisaysa yalnizca sonda at
            turkce = re.search(r"[çğıöşüÇĞİÖŞÜ]", kelime)
            if not turkce and (len(kelime) >= 8 or (son_mu and len(kelime) >= 5)):
                continue                      # HBCV0001, B08XYZ1234, 1a2b3c
        kelimeler.append(kelime)
    return kelimeler


def _kisalt(kelimeler):
    """
    Cok uzun slug'larda bas ve son onemlidir: bas = marka/model,
    son = urun tipi. Ortadaki nitelik kelimeleri aramayi daraltir.
    """
    if len(kelimeler) <= UZUN_ESIK:
        return kelimeler
    secilen, gorulen = [], set()
    for kelime in kelimeler[:3] + kelimeler[-3:]:
        if kelime.lower() not in gorulen:
            gorulen.add(kelime.lower())
            secilen.append(kelime)
    return secilen


def linkten_urun_adi(link):
    """
    Linkin icindeki urun adini cikarir.

    Urun adi neredeyse her zaman yolun SONUNDAKI parcadadir; kategori
    parcalari onde durur. Bu yuzden sondan basa dogru taranir.
      .../ev-tekstili-yatak-ortusu/tac-abril-p-999888  ->  "tac abril"
    Anlamli bir ad cikmazsa None doner.
    """
    parcalar = _yol_parcalari(link)
    secilen, secilen_sira = [], -1

    for sira in range(len(parcalar) - 1, -1, -1):
        if parcalar[sira].lower() in YOL_GURULTUSU:
            continue
        kelimeler = _kelimeleri_ayikla(parcalar[sira])
        if len(kelimeler) >= 2:
            secilen, secilen_sira = kelimeler, sira
            break

    if not secilen:
        return None

    # Marka cogu sitede bir onceki parcada durur (trendyol.com/tac/...).
    # Yalnizca kisa ve kategori olmayan bir onceki parcayi basa ekle.
    if secilen_sira > 0:
        onceki = parcalar[secilen_sira - 1]
        if onceki.lower() not in YOL_GURULTUSU:
            onceki_kelimeler = _kelimeleri_ayikla(onceki)
            if 1 <= len(onceki_kelimeler) <= 2:
                mevcut = {k.lower() for k in secilen}
                secilen = [k for k in onceki_kelimeler
                           if k.lower() not in mevcut] + secilen

    ad = re.sub(r"\s+", " ", " ".join(_kisalt(secilen))).strip()
    return ad if len(ad) >= 8 else None


ALAN_ADI = re.compile(r"^[\w.-]+\.[a-z]{2,}$", re.IGNORECASE)


def etiket_kullanilabilir(ad):
    """Kullanicinin verdigi ad aramada ise yarar mi? (alan adi ise yaramaz)"""
    ad = (ad or "").strip()
    if len(ad) < 3:
        return False
    return not ALAN_ADI.match(ad)


def arama_terimi(ad, link):
    """
    Akakce'de aranacak metin.
    Once linkin icindeki urun adi, olmazsa kullanicinin verdigi ad.
    Ikisi de yoksa None -> arama baglantisi gosterilmez.
    """
    linkten = linkten_urun_adi(link)
    if linkten:
        return linkten
    return ad.strip() if etiket_kullanilabilir(ad) else None


def akakce_arama(terim):
    sorgu = re.sub(r"[^\w\sçğıöşüÇĞİÖŞÜ]", " ", terim)
    sorgu = re.sub(r"\s+", " ", sorgu).strip()
    return "https://www.akakce.com/arama/?q=" + urllib.parse.quote_plus(sorgu)


# ----------------------------------------------------------------------------
# Mesaj cozumleme
# ----------------------------------------------------------------------------
def mesaji_coz(metin):
    """
    Mesajdaki her link icin (ad, link, hedef) uretir.
    Yazilan etiket yalnizca ilk linke uygulanir; digerlerinin adi kendi
    linkinden cikarilir. Link yoksa bos liste doner.
    """
    eslesmeler = list(LINK_DESENI.finditer(metin))[:MAKS_LINK]
    if not eslesmeler:
        return []

    # Etiket ve hedef fiyat, linklerin disinda kalan metinden okunur
    kalan = metin[:eslesmeler[0].start()] + " " + metin[eslesmeler[-1].end():]
    kalan = re.sub(r"^\s*/\w+(@\w+)?\s*", "", kalan.strip()).strip()

    hedef = ""
    sayilar = re.findall(r"\b\d{2,7}\b", kalan)
    if sayilar:
        hedef = sayilar[-1]
        kalan = re.sub(r"\b" + re.escape(hedef) + r"\b", "", kalan).strip()

    etiket = re.sub(r"\s+", " ", kalan).strip(" -–—:\"'«»„“”`")[:80]

    sonuclar = []
    for sira, eslesme in enumerate(eslesmeler):
        link = eslesme.group(0).rstrip(".,;)]")
        if sira == 0 and etiket:
            ad = etiket
        else:
            ad = (linkten_urun_adi(link)
                  or urllib.parse.urlparse(link).netloc or "ürün")[:80]
        sonuclar.append((ad, link, hedef if sira == 0 else ""))
    return sonuclar


# ----------------------------------------------------------------------------
# Dosya islemleri
# ----------------------------------------------------------------------------
def urunleri_oku():
    """Her satiri [ad, link, hedef] olarak dondurur."""
    if not URUNLER_DOSYASI.exists():
        return []
    with open(URUNLER_DOSYASI, encoding="utf-8-sig", newline="") as dosya:
        satirlar = [s for s in csv.reader(dosya) if s and len(s) >= 2]
    if satirlar and satirlar[0][0].strip().lower() == "ad":
        satirlar = satirlar[1:]
    temiz = []
    for s in satirlar:
        s = (s + ["", "", ""])[:3]
        if s[1].strip().startswith("http"):
            temiz.append([s[0].strip(), s[1].strip(), s[2].strip()])
    return temiz


def urunleri_yaz(satirlar):
    with open(URUNLER_DOSYASI, "w", encoding="utf-8-sig", newline="") as dosya:
        yazici = csv.writer(dosya)
        yazici.writerow(["ad", "link", "hedef_fiyat"])
        for satir in satirlar:
            yazici.writerow((satir + ["", "", ""])[:3])


def durum_oku():
    if DURUM_DOSYASI.exists():
        try:
            veri = json.loads(DURUM_DOSYASI.read_text(encoding="utf-8"))
            if isinstance(veri, dict):
                return veri
        except json.JSONDecodeError:
            pass
    return {"offset": 0}


def durum_yaz(durum):
    DURUM_DOSYASI.write_text(json.dumps({"offset": durum.get("offset", 0)}),
                             encoding="utf-8")


# ----------------------------------------------------------------------------
# Ana akis
# ----------------------------------------------------------------------------
def urun_satiri(sira, satir):
    """Liste mesajindaki tek bir urun blogunu olusturur."""
    ad, link, hedef = satir[0], satir[1], satir[2]
    terim = arama_terimi(ad, link)
    hedef_not = f" — hedef {kacir(hedef)} TL" if hedef else ""
    if terim:
        alt = (f"<i>{kacir(terim)}</i>\n"
               f"<a href=\"{kacir(link)}\">Ürün</a> · "
               f"<a href=\"{kacir(akakce_arama(terim))}\">Akakçe'de ara</a>\n")
    else:
        alt = (f"<a href=\"{kacir(link)}\">Ürün</a>\n"
               f"<i>Arama için ad yok — /sil ile çıkarıp marka ve modelle "
               f"tekrar ekleyin.</i>\n")
    return f"\n<b>{sira}.</b> {kacir(ad)}{hedef_not}\n" + alt


def main():
    if not TOKEN or not SOHBET:
        print("!! Token veya chat ID tanimli degil.")
        return

    durum = durum_oku()
    yanit = api("getUpdates", {"offset": durum.get("offset", 0), "timeout": 0})
    if not yanit.get("ok"):
        return

    guncellemeler = yanit.get("result", [])
    if not guncellemeler:
        print("Yeni mesaj yok.")
        return

    urunler = urunleri_oku()
    mevcut_linkler = {s[1] for s in urunler}
    eklenenler, silinenler, atlananlar = [], [], 0
    son_id = durum.get("offset", 0)

    for guncelleme in guncellemeler:
        son_id = max(son_id, guncelleme.get("update_id", 0) + 1)
        mesaj = guncelleme.get("message") or guncelleme.get("channel_post")
        if not mesaj:
            continue
        if str(mesaj.get("chat", {}).get("id")) != SOHBET:
            continue

        metin = (mesaj.get("text") or mesaj.get("caption") or "").strip()
        if not metin:
            continue

        komut = metin.split()[0].split("@")[0].lower()

        if komut == "/liste":
            if not urunler:
                cevap_yaz("Takip listesi boş. Bir ürün linki göndererek başlayın.")
            else:
                parcali_gonder(
                    [urun_satiri(i, s) for i, s in enumerate(urunler, 1)],
                    f"📋 <b>Takip edilen {len(urunler)} ürün</b>\n",
                )
            continue

        if komut == "/sil":
            parcalar = metin.split()
            if len(parcalar) < 2 or not parcalar[1].isdigit():
                cevap_yaz("Kullanım: <code>/sil 3</code> — numarayı /liste ile öğrenin.")
                continue
            sira = int(parcalar[1])
            if 1 <= sira <= len(urunler):
                cikarilan = urunler.pop(sira - 1)
                silinenler.append(cikarilan[0])
                mevcut_linkler.discard(cikarilan[1])
            else:
                cevap_yaz(f"{sira} numaralı ürün yok. /liste ile bakabilirsiniz.")
            continue

        for ad, link, hedef in mesaji_coz(metin):
            if link in mevcut_linkler:
                atlananlar += 1
                continue
            urunler.append([ad, link, hedef])
            mevcut_linkler.add(link)
            eklenenler.append([ad, link, hedef])
            print(f"Eklendi: {ad} -> {arama_terimi(ad, link)}")

    durum["offset"] = son_id
    durum_yaz(durum)

    if eklenenler or silinenler:
        urunleri_yaz(urunler)

    if eklenenler:
        satirlar = []
        for satir in eklenenler:
            terim = arama_terimi(satir[0], satir[1])
            hedef_not = f" (hedef {kacir(satir[2])} TL)" if satir[2] else ""
            if terim:
                alt = (f"<i>{kacir(terim)}</i>\n"
                       f"<a href=\"{kacir(akakce_arama(terim))}\">"
                       f"Akakçe'de ara</a>\n")
            else:
                alt = ("<i>⚠️ Bu linkte ürün adı yok. Akakçe araması için "
                       "ürünü marka ve modelle birlikte yazın.</i>\n")
            satirlar.append(
                f"\n• <b>{kacir(satir[0])}</b>{hedef_not}\n" + alt
            )
        satirlar.append(f"\nToplam {len(urunler)} ürün takipte.")
        parcali_gonder(satirlar, f"✅ <b>{len(eklenenler)} ürün eklendi</b>\n")

    if silinenler:
        cevap_yaz("🗑 Silindi: " + kacir(", ".join(silinenler)))
    if atlananlar:
        cevap_yaz(f"ℹ️ {atlananlar} link zaten listede olduğu için atlandı.")

    print(f"Eklenen: {len(eklenenler)} | Silinen: {len(silinenler)} | "
          f"Atlanan: {atlananlar} | Toplam: {len(urunler)}")


if __name__ == "__main__":
    main()

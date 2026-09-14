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
    /liste                                -> takip edilen urunleri listeler
    /sil 3                                -> 3 numarali urunu siler
"""

import csv
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

KOK = Path(__file__).resolve().parent
URUNLER_DOSYASI = KOK / "urunler.csv"
DURUM_DOSYASI = KOK / "telegram_durum.json"

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
SOHBET = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

LINK_DESENI = re.compile(r"https?://\S+")
# Ad uretirken atilacak ekler
GURULTU = {
    "urun", "product", "p", "dp", "detay", "detail", "html", "htm",
    "aspx", "php", "tr", "www",
}


def api(metot, veri=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{metot}"
    gonderi = urllib.parse.urlencode(veri).encode() if veri else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=gonderi), timeout=25) as y:
            return json.loads(y.read().decode())
    except Exception as hata:
        print(f"!! Telegram API hatasi ({metot}): {hata}")
        return {"ok": False}


def cevap_yaz(metin):
    api("sendMessage", {
        "chat_id": SOHBET,
        "text": metin,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    })


SITE_EKLERI = re.compile(
    r"\s*[|\-–—]\s*(karaca|hepsiburada|trendyol|n11|amazon|vatan|media ?markt|"
    r"ikea|koçtaş|koctas|boyner|lc ?waikiki|english home|madame coco|"
    r"çiçeksepeti|ciceksepeti|teknosa|migros|gratis)[\w\s.]*$",
    re.IGNORECASE,
)
TANITIM_EKLERI = re.compile(
    r"\s*[|\-–—,]?\s*(fiyat[ıi]?|fiyatlar[ıi]?|yorumlar[ıi]?|özellikleri|"
    r"modelleri|en ucuz|satın al|online|indirimli|ücretsiz kargo|"
    r"kampanyal[ıi] fiyat)[\w\s.,|–—-]*$",
    re.IGNORECASE,
)


def sayfa_basligi_al(link):
    """Urun sayfasindan gercek urun adini cekmeye calisir. Basarisizsa None."""
    istek = urllib.request.Request(link, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0.0.0 Safari/537.36"),
        "Accept-Language": "tr-TR,tr;q=0.9",
    })
    try:
        with urllib.request.urlopen(istek, timeout=20) as yanit:
            ham = yanit.read(300_000)
        html = ham.decode("utf-8", errors="ignore")
    except Exception as hata:
        print(f"   basligi alinamadi: {hata}")
        return None

    baslik = None

    # 1) og:title (en temiz kaynak)
    eslesme = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', html, re.I)
    if not eslesme:
        eslesme = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title', html, re.I)
    if eslesme:
        baslik = eslesme.group(1)

    # 2) JSON-LD urun adi
    if not baslik:
        eslesme = re.search(r'"@type"\s*:\s*"Product".{0,400}?"name"\s*:\s*"([^"]{5,150})"',
                            html, re.I | re.S)
        if eslesme:
            baslik = eslesme.group(1)

    # 3) <title>
    if not baslik:
        eslesme = re.search(r"<title[^>]*>([^<]{5,200})</title>", html, re.I)
        if eslesme:
            baslik = eslesme.group(1)

    if not baslik:
        return None

    for eski, yeni in (("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"),
                       ("&apos;", "'"), ("&nbsp;", " "), ("&gt;", ">"), ("&lt;", "<")):
        baslik = baslik.replace(eski, yeni)

    # Amazon gibi siteler basliga kendi adini onek olarak koyuyor
    baslik = re.sub(r"^(amazon\.com\.tr|amazon|hepsiburada|trendyol|n11)\s*[:|\-]\s*",
                    "", baslik, flags=re.IGNORECASE)
    baslik = SITE_EKLERI.sub("", baslik)
    baslik = TANITIM_EKLERI.sub("", baslik)
    baslik = re.sub(r"\s+", " ", baslik).strip(" -–—|,·")

    # Arama icin ilk 10 kelime yeter, fazlasi sonucu daraltir
    kelimeler = baslik.split()
    if len(kelimeler) > 10:
        baslik = " ".join(kelimeler[:10])

    return baslik if len(baslik) >= 5 else None


def akakce_arama(ad):
    """Urun adindan Akakce arama baglantisi uretir."""
    sorgu = re.sub(r"[^\w\sçğıöşüÇĞİÖŞÜ]", " ", ad)
    sorgu = re.sub(r"\s+", " ", sorgu).strip()
    return "https://www.akakce.com/arama/?q=" + urllib.parse.quote_plus(sorgu)


def parcali_gonder(satirlar, baslik):
    """Telegram 4096 karakter sinirini asmadan parcalar halinde gonderir."""
    if not satirlar:
        return
    tampon = baslik
    for satir in satirlar:
        if len(tampon) + len(satir) > 3600:
            cevap_yaz(tampon)
            tampon = ""
        tampon += satir
    if tampon.strip():
        cevap_yaz(tampon)


def linkten_ad_uret(link):
    """URL'nin son parcasindan okunabilir bir ad cikarir."""
    try:
        yol = urllib.parse.urlparse(link).path.strip("/")
    except ValueError:
        return "urun"

    parcalar = [p for p in yol.split("/") if p]
    # En uzun ve anlamli parcayi sec
    aday = ""
    for parca in parcalar:
        temiz = re.sub(r"\.(html?|aspx|php)$", "", parca)
        if temiz.lower() in GURULTU or temiz.isdigit():
            continue
        if len(temiz) > len(aday):
            aday = temiz

    if not aday:
        alan = urllib.parse.urlparse(link).netloc.replace("www.", "")
        return alan or "urun"

    ad = re.sub(r"[-_]+", " ", aday)
    ad = re.sub(r"\s+", " ", ad).strip()
    ad = re.sub(r"\b[pP]?\d{5,}\b", "", ad).strip()  # uzun urun kodlarini at

    if len(ad) > 45:
        kesik = ad[:45].rsplit(" ", 1)[0]
        ad = kesik if len(kesik) > 15 else ad[:45]

    return ad or "urun"


def mesaji_coz(metin):
    """Mesajdan (ad, link, hedef) uclusunu cikarir. Link yoksa None doner."""
    eslesme = LINK_DESENI.search(metin)
    if not eslesme:
        return None

    link = eslesme.group(0).rstrip(".,;)")
    kalan = (metin[:eslesme.start()] + " " + metin[eslesme.end():]).strip()

    # Komut on ekini temizle
    kalan = re.sub(r"^/\w+(@\w+)?\s*", "", kalan).strip()

    # Kalan metindeki tek basina duran sayi -> hedef fiyat
    hedef = ""
    sayilar = re.findall(r"\b\d{2,7}\b", kalan)
    if sayilar:
        hedef = sayilar[-1]
        kalan = re.sub(r"\b" + re.escape(hedef) + r"\b", "", kalan).strip()

    ad = re.sub(r"\s+", " ", kalan).strip(" -–—:")
    if not ad:
        ad = linkten_ad_uret(link)

    return ad, link, hedef


def urunleri_oku():
    """Her satiri [ad, link, hedef, tam_ad] olarak dondurur."""
    if not URUNLER_DOSYASI.exists():
        return []
    with open(URUNLER_DOSYASI, encoding="utf-8-sig", newline="") as dosya:
        ham = [s for s in csv.reader(dosya) if s and len(s) >= 2][1:]
    return [(s + ["", "", "", ""])[:4] for s in ham]


def urunleri_yaz(satirlar):
    with open(URUNLER_DOSYASI, "w", encoding="utf-8", newline="") as dosya:
        yazici = csv.writer(dosya)
        yazici.writerow(["ad", "link", "hedef_fiyat", "tam_ad"])
        for satir in satirlar:
            yazici.writerow((satir + ["", "", "", ""])[:4])


def arama_adi(satir):
    """Akakce aramasi icin: varsa sayfadan alinan tam ad, yoksa kullanicinin adi."""
    return (satir[3].strip() if len(satir) > 3 and satir[3].strip() else satir[0])


def durum_oku():
    if DURUM_DOSYASI.exists():
        try:
            return json.loads(DURUM_DOSYASI.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"offset": 0}


def durum_yaz(durum):
    DURUM_DOSYASI.write_text(json.dumps(durum), encoding="utf-8")


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
    mevcut_linkler = {s[1].strip() for s in urunler if len(s) > 1}
    eklenenler, silinenler, atlananlar = [], [], 0
    son_id = durum.get("offset", 0)

    # Tam adi eksik olan eski kayitlari once tamamla ki /liste dogru gostersin
    tamamlanan = 0
    for satir in urunler:
        if tamamlanan >= 10:
            break
        if len(satir) > 3 and satir[3].strip():
            continue
        tam = sayfa_basligi_al(satir[1])
        tamamlanan += 1
        if tam:
            satir[3] = tam
            print(f"Tam ad eklendi: {satir[0]} -> {tam}")
        else:
            satir[3] = satir[0]
            print(f"Tam ad alinamadi, kisa ad kullanilacak: {satir[0]}")
        time.sleep(1.5)

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

        # /liste
        if komut == "/liste":
            if not urunler:
                cevap_yaz("Takip listesi boş.")
            else:
                satirlar = []
                for i, s in enumerate(urunler, 1):
                    hedef_not = f" — hedef {s[2]} TL" if len(s) > 2 and s[2] else ""
                    satirlar.append(
                        f"\n<b>{i}.</b> {s[0]}{hedef_not}\n"
                        f"<a href=\"{s[1]}\">Ürün</a> · "
                        f"<a href=\"{akakce_arama(arama_adi(s))}\">Akakçe'de ara</a>\n"
                    )
                parcali_gonder(
                    satirlar, f"📋 <b>Takip edilen {len(urunler)} ürün</b>\n"
                )
            continue

        # /sil <numara>
        if komut == "/sil":
            parcalar = metin.split()
            if len(parcalar) < 2 or not parcalar[1].isdigit():
                cevap_yaz("Kullanım: <code>/sil 3</code> — numarayı /liste ile öğrenin.")
                continue
            sira = int(parcalar[1])
            if 1 <= sira <= len(urunler):
                cikarilan = urunler.pop(sira - 1)
                silinenler.append(cikarilan[0])
                mevcut_linkler.discard(cikarilan[1].strip())
            else:
                cevap_yaz(f"{sira} numaralı ürün yok. /liste ile bakabilirsiniz.")
            continue

        # Link iceren mesaj
        cozum = mesaji_coz(metin)
        if not cozum:
            continue

        ad, link, hedef = cozum
        if link in mevcut_linkler:
            atlananlar += 1
            continue

        print(f"Ekleniyor: {ad}")
        tam_ad = sayfa_basligi_al(link) or ""
        if tam_ad:
            print(f"   sayfa adı: {tam_ad}")
        urunler.append([ad, link, hedef, tam_ad])
        mevcut_linkler.add(link)
        eklenenler.append((ad, hedef, tam_ad or ad))

    durum["offset"] = son_id
    durum_yaz(durum)

    if eklenenler or silinenler or tamamlanan:
        urunleri_yaz(urunler)

    # Onay mesaji
    if eklenenler:
        satirlar = [
            f"\n• <b>{ad}</b>" + (f" (hedef {hedef} TL)" if hedef else "")
            + (f"\n  <i>{tam}</i>" if tam != ad else "")
            + f"\n  <a href=\"{akakce_arama(tam)}\">Akakçe'de ara</a>\n"
            for ad, hedef, tam in eklenenler
        ]
        satirlar.append(f"\nToplam {len(urunler)} ürün takipte.")
        parcali_gonder(satirlar, f"✅ <b>{len(eklenenler)} ürün eklendi</b>\n")
    if silinenler:
        cevap_yaz("🗑 Silindi: " + ", ".join(silinenler))
    if atlananlar:
        cevap_yaz(f"ℹ️ {atlananlar} link zaten listede olduğu için atlandı.")

    print(f"Eklenen: {len(eklenenler)} | Silinen: {len(silinenler)} | "
          f"Atlanan: {atlananlar} | Toplam: {len(urunler)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fiyat Takip Botu
----------------
urunler.csv icindeki linkleri gezer, fiyatlari cikarir, fiyat_gecmisi.json'a
kaydeder ve fiyat dustugunde Telegram'dan bildirim gonderir.

Kullanim:
    python fiyat_takip.py            # normal calisma (bildirim gonderir)
    python fiyat_takip.py --test     # bildirim gondermez, sadece fiyatlari yazar
    python fiyat_takip.py --test 5   # sadece ilk 5 urunu dener
"""

import csv
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bs4 import BeautifulSoup

# curl_cffi varsa onu kullan (tarayici parmak izi taklidi -> bot korumasini asma sansi cok daha yuksek)
try:
    from curl_cffi import requests as _http
    _IMPERSONATE = True
except ImportError:  # pragma: no cover
    import requests as _http
    _IMPERSONATE = False


KOK = Path(__file__).resolve().parent
URUNLER_DOSYASI = KOK / "urunler.csv"
GECMIS_DOSYASI = KOK / "fiyat_gecmisi.json"

TR_SAAT = timezone(timedelta(hours=3))          # Europe/Istanbul
GECMIS_LIMIT = 300                              # urun basina saklanacak kayit sayisi
BEKLEME = (2.0, 4.5)                            # istekler arasi rastgele bekleme (saniye)
ZAMAN_ASIMI = 30

# Yuzde bazinda: bundan kucuk dususler "gurultu" sayilip bildirilmez.
MIN_DUSUS_YUZDE = float(os.environ.get("MIN_DUSUS_YUZDE", "1.0"))

BASLIKLAR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}

# Site bazli CSS secicileri. Once genel yontemler denenir, tutmazsa buraya bakilir.
SITE_SECICILER = {
    "trendyol.com": [".prc-dsc", ".prc-slg", ".product-price-container .prc-dsc"],
    "hepsiburada.com": [
        '[data-test-id="price-current-price"]',
        "#offering-price",
        '[data-test-id="default-price"]',
    ],
    "n11.com": [".newPrice ins", ".priceContainer .newPrice", "#unf-p-id ins"],
    "amazon.com.tr": [
        "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
        "#corePrice_feature_div .a-price .a-offscreen",
        ".a-price .a-offscreen",
    ],
    "ikea.com.tr": [".pip-temp-price__integer", ".pip-price__integer"],
    "koctas.com.tr": [".price-new", ".product-price"],
    "vatanbilgisayar.com": [".product-list__price", ".price"],
    "mediamarkt.com.tr": ['[data-test="branded-price-whole-value"]'],
    "ciceksepeti.com": [".product-price__new-price", "#priceNew"],
    "boyner.com.tr": [".product-price .discount-price", ".price-item"],
    "lcw.com": [".price-area .price", ".product-price"],
    "englishhome.com": [".product-price .price", ".prc-last"],
    "madamecoco.com": [".product-price .price", ".prc-last"],
}


# ----------------------------------------------------------------------------
# Fiyat metnini sayiya cevirme
# ----------------------------------------------------------------------------
def metni_fiyata_cevir(metin):
    """'1.299,90 TL' -> 1299.9   |   '2,499.00' -> 2499.0   |   '899' -> 899.0"""
    if metin is None:
        return None
    metin = str(metin).strip()
    if not metin:
        return None

    # Sadece rakam, nokta ve virgulleri birak
    metin = metin.replace("\xa0", " ")
    eslesme = re.search(r"\d[\d.,\s]*\d|\d", metin)
    if not eslesme:
        return None
    ham = eslesme.group(0).replace(" ", "")

    nokta = ham.rfind(".")
    virgul = ham.rfind(",")

    if nokta != -1 and virgul != -1:
        # Sonda gelen ondalik ayiracidir
        if virgul > nokta:
            ham = ham.replace(".", "").replace(",", ".")
        else:
            ham = ham.replace(",", "")
    elif virgul != -1:
        sag = len(ham) - virgul - 1
        # 1.234,56 -> ondalik   |   1,234 -> binlik
        ham = ham.replace(",", "." if sag <= 2 else "")
    elif nokta != -1:
        sag = len(ham) - nokta - 1
        if sag == 3 and len(ham.replace(".", "")) > 3:
            # 1.299 gibi -> binlik ayiraci
            ham = ham.replace(".", "")
        # aksi halde zaten ondalik (1299.90)

    try:
        deger = float(ham)
    except ValueError:
        return None

    if deger <= 0 or deger > 10_000_000:
        return None
    return round(deger, 2)


def _jsonld_fiyat_bul(veri):
    """JSON-LD icinde ic ice gecmis offers/price alanlarini arar."""
    if isinstance(veri, list):
        for parca in veri:
            sonuc = _jsonld_fiyat_bul(parca)
            if sonuc:
                return sonuc
        return None

    if not isinstance(veri, dict):
        return None

    for anahtar in ("price", "lowPrice", "highPrice"):
        if anahtar in veri:
            fiyat = metni_fiyata_cevir(veri[anahtar])
            if fiyat:
                return fiyat

    for anahtar in ("offers", "@graph", "hasVariant", "itemOffered", "mainEntity"):
        if anahtar in veri:
            sonuc = _jsonld_fiyat_bul(veri[anahtar])
            if sonuc:
                return sonuc
    return None


def fiyat_ayikla(html, url):
    """Coklu yontemle fiyati cikarmayi dener. Basarisizsa None doner."""
    corba = BeautifulSoup(html, "html.parser")

    # 1) JSON-LD structured data -- en guvenilir yontem, cogu sitede var
    for etiket in corba.find_all("script", type="application/ld+json"):
        try:
            veri = json.loads(etiket.string or etiket.get_text() or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        fiyat = _jsonld_fiyat_bul(veri)
        if fiyat:
            return fiyat, "json-ld"

    # 2) Meta etiketleri
    meta_adlari = [
        ("property", "product:price:amount"),
        ("property", "og:price:amount"),
        ("itemprop", "price"),
        ("name", "twitter:data1"),
    ]
    for nitelik, deger in meta_adlari:
        etiket = corba.find("meta", attrs={nitelik: deger})
        if etiket and etiket.get("content"):
            fiyat = metni_fiyata_cevir(etiket["content"])
            if fiyat:
                return fiyat, f"meta:{deger}"

    # 3) itemprop="price" iceren herhangi bir eleman
    etiket = corba.find(attrs={"itemprop": "price"})
    if etiket:
        fiyat = metni_fiyata_cevir(etiket.get("content") or etiket.get_text())
        if fiyat:
            return fiyat, "itemprop"

    # 4) Siteye ozel CSS secicileri
    for alan, seciciler in SITE_SECICILER.items():
        if alan in url:
            for secici in seciciler:
                bulunan = corba.select_one(secici)
                if bulunan:
                    fiyat = metni_fiyata_cevir(bulunan.get_text())
                    if fiyat:
                        return fiyat, f"secici:{secici}"

    # 5) Son care: "price" gecen class'lardaki TL ifadeleri
    adaylar = []
    for eleman in corba.select('[class*="price"], [class*="Price"], [class*="prc"], [id*="price"]'):
        metin = eleman.get_text(" ", strip=True)
        if len(metin) > 40:
            continue
        if re.search(r"(TL|₺)", metin):
            fiyat = metni_fiyata_cevir(metin)
            if fiyat:
                adaylar.append(fiyat)
    if adaylar:
        return min(adaylar), "tahmin"

    return None, None


# ----------------------------------------------------------------------------
# Ag islemleri
# ----------------------------------------------------------------------------
def sayfayi_getir(url):
    kwargs = {"headers": BASLIKLAR, "timeout": ZAMAN_ASIMI}
    if _IMPERSONATE:
        kwargs["impersonate"] = "chrome"
    else:
        kwargs["allow_redirects"] = True

    yanit = _http.get(url, **kwargs)
    if yanit.status_code != 200:
        raise RuntimeError(f"HTTP {yanit.status_code}")
    return yanit.text


def telegram_gonder(mesaj):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    sohbet = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not sohbet:
        print("!! TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tanimli degil, bildirim atlandi.")
        return False

    import urllib.parse
    import urllib.request

    veri = urllib.parse.urlencode({
        "chat_id": sohbet,
        "text": mesaj,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()

    istek = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=veri
    )
    try:
        with urllib.request.urlopen(istek, timeout=20) as yanit:
            return yanit.status == 200
    except Exception as hata:
        print(f"!! Telegram hatasi: {hata}")
        return False


def mesajlari_bolerek_gonder(satirlar, baslik):
    """Telegram 4096 karakter siniri icin mesaji parcalara boler."""
    if not satirlar:
        return
    tampon = baslik
    for satir in satirlar:
        if len(tampon) + len(satir) > 3800:
            telegram_gonder(tampon)
            time.sleep(1)
            tampon = ""
        tampon += satir
    if tampon.strip():
        telegram_gonder(tampon)


# ----------------------------------------------------------------------------
# Veri okuma / yazma
# ----------------------------------------------------------------------------
def urunleri_oku():
    if not URUNLER_DOSYASI.exists():
        print(f"!! {URUNLER_DOSYASI.name} bulunamadi.")
        return []

    urunler = []
    with open(URUNLER_DOSYASI, encoding="utf-8-sig", newline="") as dosya:
        for satir in csv.DictReader(dosya):
            link = (satir.get("link") or "").strip()
            if not link or not link.startswith("http"):
                continue
            urunler.append({
                "ad": (satir.get("ad") or "").strip() or link[:60],
                "link": link,
                "hedef": metni_fiyata_cevir(satir.get("hedef_fiyat")),
            })
    return urunler


def gecmisi_oku():
    if GECMIS_DOSYASI.exists():
        try:
            return json.loads(GECMIS_DOSYASI.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("!! fiyat_gecmisi.json bozuk, sifirdan baslaniyor.")
    return {}


def gecmisi_yaz(gecmis):
    GECMIS_DOSYASI.write_text(
        json.dumps(gecmis, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def para(deger):
    return f"{deger:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".") + " TL"


# ----------------------------------------------------------------------------
# Ana akis
# ----------------------------------------------------------------------------
def main():
    test_modu = "--test" in sys.argv
    sinir = None
    for arg in sys.argv[1:]:
        if arg.isdigit():
            sinir = int(arg)

    urunler = urunleri_oku()
    if not urunler:
        print("Takip edilecek urun yok. urunler.csv dosyasina link ekleyin.")
        return

    if sinir:
        urunler = urunler[:sinir]

    gecmis = gecmisi_oku()
    simdi = datetime.now(TR_SAAT)
    damga = simdi.strftime("%Y-%m-%d %H:%M")

    dususler, hedefler, hatalar = [], [], []
    print(f"{len(urunler)} urun kontrol ediliyor "
          f"({'curl_cffi' if _IMPERSONATE else 'requests'} ile)...\n")

    for sira, urun in enumerate(urunler, 1):
        link = urun["link"]
        ad = urun["ad"]
        kayit = gecmis.setdefault(link, {"ad": ad, "gecmis": []})
        kayit["ad"] = ad

        try:
            html = sayfayi_getir(link)
            fiyat, yontem = fiyat_ayikla(html, link)
        except Exception as hata:
            fiyat, yontem = None, None
            kayit["son_hata"] = f"{damga} - {hata}"
            hatalar.append(f"{ad}: {hata}")
            print(f"[{sira}/{len(urunler)}] HATA  {ad} -> {hata}")
            time.sleep(random.uniform(*BEKLEME))
            continue

        if fiyat is None:
            kayit["son_hata"] = f"{damga} - fiyat okunamadi"
            hatalar.append(f"{ad}: fiyat okunamadi")
            print(f"[{sira}/{len(urunler)}] ??    {ad} -> fiyat bulunamadi")
            time.sleep(random.uniform(*BEKLEME))
            continue

        kayit.pop("son_hata", None)
        onceki = kayit.get("son_fiyat")
        kayit["son_fiyat"] = fiyat
        kayit["son_kontrol"] = damga
        kayit["gecmis"].append([damga, fiyat])
        kayit["gecmis"] = kayit["gecmis"][-GECMIS_LIMIT:]

        en_dusuk = kayit.get("en_dusuk")
        if en_dusuk is None or fiyat < en_dusuk:
            kayit["en_dusuk"] = fiyat

        durum = "yeni"
        if onceki is not None:
            fark = onceki - fiyat
            yuzde = (fark / onceki * 100) if onceki else 0
            if fark > 0 and yuzde >= MIN_DUSUS_YUZDE:
                durum = f"DUSTU  -%{yuzde:.1f}"
                rekor = " 🏆 <b>en düşük fiyat!</b>" if fiyat <= kayit["en_dusuk"] else ""
                dususler.append(
                    f"\n🔻 <b>{ad}</b>\n"
                    f"{para(onceki)} → <b>{para(fiyat)}</b>  (−%{yuzde:.1f}){rekor}\n"
                    f"<a href=\"{link}\">Ürüne git</a>\n"
                )
            elif fark < 0:
                durum = f"zamlandi +%{abs(yuzde):.1f}"
            else:
                durum = "degismedi"

        if urun["hedef"] and fiyat <= urun["hedef"] and not kayit.get("hedef_bildirildi"):
            kayit["hedef_bildirildi"] = True
            hedefler.append(
                f"\n🎯 <b>{ad}</b>\n"
                f"Hedefiniz {para(urun['hedef'])} — şu an <b>{para(fiyat)}</b>\n"
                f"<a href=\"{link}\">Ürüne git</a>\n"
            )
        elif urun["hedef"] and fiyat > urun["hedef"]:
            kayit["hedef_bildirildi"] = False

        print(f"[{sira}/{len(urunler)}] {para(fiyat):>14}  {ad[:45]:<45} "
              f"({yontem}) {durum}")

        time.sleep(random.uniform(*BEKLEME))

    if not test_modu:
        gecmisi_yaz(gecmis)
        mesajlari_bolerek_gonder(
            hedefler, f"🎯 <b>Hedef fiyata ulaşan ürünler</b>\n<i>{damga}</i>\n"
        )
        mesajlari_bolerek_gonder(
            dususler, f"📉 <b>Fiyatı düşen ürünler</b>\n<i>{damga}</i>\n"
        )
        if hatalar and len(hatalar) >= max(5, len(urunler) // 2):
            telegram_gonder(
                f"⚠️ <b>Uyarı:</b> {len(hatalar)}/{len(urunler)} üründe fiyat "
                f"okunamadı. Site yapısı değişmiş veya bot koruması devreye "
                f"girmiş olabilir."
            )

    print(f"\n{'-' * 60}")
    print(f"Bitti. Düşen: {len(dususler)} | Hedefe ulaşan: {len(hedefler)} | "
          f"Okunamayan: {len(hatalar)}")
    if test_modu:
        print("(Test modu: hicbir sey kaydedilmedi, bildirim gonderilmedi.)")


if __name__ == "__main__":
    main()

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
    if not URUNLER_DOSYASI.exists():
        return []
    with open(URUNLER_DOSYASI, encoding="utf-8-sig", newline="") as dosya:
        return [s for s in csv.reader(dosya) if s and len(s) >= 2][1:]


def urunleri_yaz(satirlar):
    with open(URUNLER_DOSYASI, "w", encoding="utf-8", newline="") as dosya:
        yazici = csv.writer(dosya)
        yazici.writerow(["ad", "link", "hedef_fiyat"])
        for satir in satirlar:
            yazici.writerow((satir + ["", "", ""])[:3])


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
                satirlar = [
                    f"{i}. {s[0]}" + (f" — hedef {s[2]} TL" if len(s) > 2 and s[2] else "")
                    for i, s in enumerate(urunler, 1)
                ]
                cevap_yaz(f"📋 <b>Takip edilen {len(urunler)} ürün</b>\n\n"
                          + "\n".join(satirlar[:80]))
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

        urunler.append([ad, link, hedef])
        mevcut_linkler.add(link)
        eklenenler.append((ad, hedef))

    durum["offset"] = son_id
    durum_yaz(durum)

    if eklenenler or silinenler:
        urunleri_yaz(urunler)

    # Onay mesaji
    if eklenenler:
        satirlar = [
            f"• {ad}" + (f" (hedef {hedef} TL)" if hedef else "")
            for ad, hedef in eklenenler
        ]
        cevap_yaz(f"✅ <b>{len(eklenenler)} ürün eklendi</b>\n\n" + "\n".join(satirlar)
                  + f"\n\nToplam {len(urunler)} ürün takipte.")
    if silinenler:
        cevap_yaz("🗑 Silindi: " + ", ".join(silinenler))
    if atlananlar:
        cevap_yaz(f"ℹ️ {atlananlar} link zaten listede olduğu için atlandı.")

    print(f"Eklenen: {len(eklenenler)} | Silinen: {len(silinenler)} | "
          f"Atlanan: {atlananlar} | Toplam: {len(urunler)}")


if __name__ == "__main__":
    main()

# Fiyat Takip Botu — Kurulum (iki kişilik)

Ürün linklerini düzenli aralıklarla kontrol eder, fiyat düştüğünde bir Telegram grubuna bildirim gönderir. GitHub Actions üzerinde ücretsiz çalışır, bilgisayarınızın açık olmasına gerek yoktur.

Toplam kurulum süresi: **20-30 dakika.** Bilgisayarda yapmanız çok daha kolay olur.

---

## Bölüm 1 — Telegram botu ve grup

1. Telegram'da **@BotFather**'a yazın → `/newbot`
2. Bota bir isim verin (örn. `Fiyat Takip`)
3. Kullanıcı adı verin — `bot` ile bitmeli (örn. `ahmet_fiyat_bot`)
4. Size verdiği **token**'ı kopyalayın: `7123456789:AAF...` şeklindedir

Şimdi bildirimlerin gideceği grubu kurun:

5. Telegram'da **yeni grup** oluşturun, paylaşacağınız kişiyi ekleyin
6. Aynı gruba **botunuzu da ekleyin** (üye ekle → bot kullanıcı adını aratın)
7. Gruba **`/start`** yazın — başında eğik çizgi olmalı. Botlar gizlilik modu nedeniyle gruplarda yalnızca komutları görür
8. Tarayıcıda şu adresi açın (TOKEN yerine kendi token'ınızı yazın):

   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```

9. Çıkan metinde `"chat":{"id":-1001234567890,"title":"..."}` gibi bir bölüm bulun.
   Bu numarayı **eksi işaretiyle birlikte** kopyalayın — grup ID'nizdir.

> Sayfa `{"ok":true,"result":[]}` gösteriyorsa gruba tekrar `/start` yazıp yenileyin.

**Tek kişilik kullanmak isterseniz:** Gruba gerek yok. Botunuza özelden `/start` yazın, sonra **@userinfobot**'tan kendi ID'nizi alın ve onu kullanın.

---

## Bölüm 2 — GitHub deposu

10. github.com'a girin, hesabınız yoksa ücretsiz açın
11. Sağ üstteki **+** → **New repository**
12. İsim verin (örn. `fiyat-takip`), **Private** seçin → **Create repository**

---

## Bölüm 3 — Dosyaları yükleme

13. Depoda **Add file** → **Upload files**
14. Şu 5 dosyayı sürükleyin, sonra **Commit changes**:
    `fiyat_takip.py`, `urunler.csv`, `requirements.txt`, `KURULUM.md`, `fiyat_gecmisi.json`

Workflow dosyası özel bir klasöre gitmeli, onu elle oluşturun:

15. **Add file** → **Create new file**
16. İsim kutusuna aynen şunu yazın: `.github/workflows/fiyat-takip.yml`
    (eğik çizgileri yazdıkça GitHub klasörleri kendisi oluşturur)
17. `fiyat-takip.yml` dosyasının içeriğini yapıştırın → **Commit changes**

---

## Bölüm 4 — Şifreler ve izinler

18. **Settings** sekmesi → **Secrets and variables** → **Actions** → **New repository secret**

| Name | Secret |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather'ın verdiği token |
| `TELEGRAM_CHAT_ID` | Grup ID'si — **eksi işareti dahil** |

19. **Settings** → **Actions** → **General** → sayfanın altındaki **Workflow permissions** bölümünde **Read and write permissions** seçin → **Save**
    (bot fiyat geçmişini depoya kaydedebilsin diye gerekli)

20. Diğer kişiyi eklemek için: **Settings** → **Collaborators** → **Add people** → GitHub kullanıcı adını yazın.
    Ücretsiz planda private depoya sınırsız kişi eklenebilir. O kişi de ürün ekleyebilir ve fiyat geçmişini görebilir.

---

## Bölüm 5 — Ürünlerinizi ekleyin

21. `urunler.csv` dosyasını Excel ile açın. Örnek satırları silip kendi ürünlerinizi yazın:

```
ad,link,hedef_fiyat
Çift Kişilik Nevresim,https://www.site.com/urun-1,1200
Buzdolabı A+++,https://www.site.com/urun-2,
Salon Halısı,https://www.site.com/urun-3,4500
```

- **ad** — bildirimlerde göreceğiniz isim, istediğiniz gibi yazın
- **link** — ürünün doğrudan sayfası (kısaltılmış linkler yerine tam adres kullanın)
- **hedef_fiyat** — isteğe bağlı. Yazarsanız fiyat bu rakamın altına indiğinde ayrıca özel bildirim gelir

Virgül içeren ürün adlarını tırnak içine alın: `"Nevresim, 200x220",https://...`

22. **CSV UTF-8** olarak kaydedin (Excel → Farklı Kaydet → dosya türü listesinde bu seçenek var).
    Türkçe karakterler bozulmasın diye önemlidir.
23. GitHub'da dosyayı güncelleyin: `urunler.csv` üzerine tıklayın → kalem simgesi → içeriği yapıştırın → **Commit changes**

---

## Bölüm 6 — İlk test

24. **Actions** sekmesi → sol menüden **Fiyat Takip** → sağdaki **Run workflow** → yeşil **Run workflow**
25. 1-2 dakika bekleyin, çalışan işe tıklayın, **Fiyatları kontrol et** adımını açın

Her ürünün yanında bulunan fiyatı ve hangi yöntemle bulunduğunu görürsünüz.
`fiyat bulunamadi` yazan siteler varsa not alın — o siteler için özel ayar eklenmesi gerekir.

**İlk çalışmada bildirim gelmez** — bu normaldir. Fiyatlar kaydedilir, karşılaştırma ikinci çalışmadan itibaren başlar.

Bundan sonra bot günde 3 kez otomatik çalışır: TR saatiyle 08:00, 14:00, 20:00.

---

## Nasıl çalışıyor?

Bot fiyatı bulmak için sırayla 5 yöntem dener:

1. **JSON-LD** — sitelerin Google için koyduğu yapısal veri (en güvenilir, çoğu sitede var)
2. **Meta etiketleri** — `product:price:amount` gibi
3. **itemprop="price"** işaretli alanlar
4. **Siteye özel seçiciler** — Trendyol, Hepsiburada, N11, Amazon TR, IKEA, Koçtaş, Vatan, MediaMarkt, Boyner, LCW, English Home, Madame Coco için hazır tanımlı
5. **Tahmin** — sınıf adında "price" geçen ve TL içeren en küçük değer

Fiyat geçmişi `fiyat_gecmisi.json` dosyasında birikir — ürün başına son 300 kayıt. Böylece "gerçekten indirim mi, yoksa önce zamlanıp sonra mı indirilmiş" görebilirsiniz.

---

## Maliyet

Tamamen ücretsizdir.

- **Telegram:** limitsiz ücretsiz
- **GitHub:** private depoda ayda 2.000 dakika ücretsiz. 200 ürün × günde 3 kontrol ≈ ayda 1.440 dakika — sınırın altında kalır

Ücretsiz hesapta harcama limiti varsayılan olarak 0 dolardır: kota biterse işler ücretlendirilmek yerine basitçe durur. Kredi kartı tanımlamadığınız sürece borç oluşmaz. Kota her ay başında sıfırlanır.

Kotayı hiç düşünmek istemezseniz depoyu Public yapabilirsiniz (public depolarda sınır yoktur), ancak o zaman ürün listeniz herkese görünür olur. Token'ınız secret'ta durduğu için gizli kalmaya devam eder.

Kullanımınızı **Settings → Billing** sayfasından takip edebilirsiniz.

---

## Ayarlar

**Kontrol sıklığı** — `.github/workflows/fiyat-takip.yml` içindeki cron satırı.
Saatler **UTC** yazılır, Türkiye UTC+3'tür.

```yaml
- cron: "0 5,11,17 * * *"     # günde 3 kez (TR 08:00, 14:00, 20:00)
- cron: "0 */6 * * *"          # 6 saatte bir
- cron: "0 6 * * *"            # günde 1 kez (TR 09:00)
```

**Gürültü eşiği** — workflow dosyasındaki `MIN_DUSUS_YUZDE`. Varsayılan `1.0`, yani %1'den küçük düşüşler bildirilmez. Kuruşluk oynamalardan rahatsız olursanız `3.0` yapın.

---

## Sorun giderme

**"Fiyat okunamadı" diyor** — Site fiyatı JavaScript ile yüklüyor veya bot koruması var. Trendyol ve Amazon TR bunda en zorlayıcı olanlardır. Çözüm: `fiyat_takip.py` içindeki `SITE_SECICILER` sözlüğüne o site için doğru CSS seçicisini ekleyin. Tarayıcıda fiyata sağ tık → *İncele* → elemanın `class` adına bakın.

**Yarıdan fazla üründe hata varsa** bot size ayrıca uyarı mesajı gönderir. Genelde tek bir sitenin engellemesinden kaynaklanır.

**Hiç bildirim gelmiyor** — Botun gruba eklendiğinden, gruba `/start` yazıldığından ve chat ID'nin eksi işaretiyle girildiğinden emin olun. Actions kayıtlarında "TELEGRAM_BOT_TOKEN tanimli degil" uyarısı varsa secret eksiktir.

**Yanlış fiyat okuyor** — Muhtemelen "tahmin" yöntemine düşmüştür (çalışma kaydında yönteme bakın). Sepet indirimi veya taksitli fiyat gibi rakamları yakalayabilir. O site için özel seçici eklemek çözer.

**Bildirimler durdu** — GitHub, 60 gün hiç hareket görmeyen depolarda zamanlanmış işleri devre dışı bırakır. Bu bot her çalışmada fiyat geçmişini depoya kaydettiği için normalde bu sorun oluşmaz.

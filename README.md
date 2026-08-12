# CK Blender Tools

Oyun/asset pipeline'ı için yazdığım üç bağımsız Blender eklentisi: UV hotspot mapping,
vertex renk boyama ve LOD hiyerarşisi düzenleyip toplu FBX export.

**Gereksinim:** Blender 3.2 veya üzeri
**Lisans:** GPL-3.0-or-later

> *English summary at the bottom.*

---

## Kurulum

Her dosya bağımsız bir eklenti — sadece istediğini kurabilirsin.

1. `Edit > Preferences > Add-ons > Install...`
2. İlgili `.py` dosyasını seç
3. Listeden eklentiyi işaretle

---

## 1. CK_UV_TOOL (`ck_uv_duzleyici.py`)

**Konum:** UV Editor > N paneli > `Custom Tools`
**Gereklilik:** Edit Mode

| Araç | İşlevi |
|---|---|
| **Straighten UV Strip** | Aktif quad'ı birim kareye çeker, sonra Follow Active Quads ile tüm şeridi ona göre hizalar |
| **Match UV Bounds** | Seçili adacıkları en büyüğünün boyutlarına getirip üst üste bindirir |
| **Standart Hotspot** | Adacıkları sadece sınırlı hotspot hücrelerine sığdırır (stretch) |
| **Sınırsız (Trim)** | Adacığın kısa kenarını hücreye kilitler, uzun kenar tile ederek serbest kalır |
| **Otomatik** | Her adacık için hotspot mu trim mi daha uygun, kendisi karar verir |

### Hotspot atlas'ı nasıl hazırlanır

Bu kısım kritik — atlas doğru hazırlanmazsa araç çalışmaz.

1. Bir **plane** oluştur, hücrelerine böl ve UV'sini aç.
2. **Hücre sınırlarını seam olarak işaretle.** Araç adacıkları UV çakışmasına göre
   değil, seam'e göre bulur. Boşluksuz grid'de komşu hücrelerin UV'leri birebir
   çakıştığı için seam olmadan hepsi tek bir dev adacık sanılır.
3. Hücre tiplerini **vertex rengi** ile işaretle (CK Vertex Painter ile boyayabilirsin):
   - **Kırmızı** → `TRIM` (tile eden, uzun kenarı sınırsız)
   - **Mavi / boyasız** → `HOTSPOT` (sınırlı, sabit oran)
4. Atlas objesini panelden `Atlas` alanına ata.
5. Çalışacağın mesh'te de adacık sınırlarının seam'li olduğundan emin ol,
   Edit Mode'da yüzleri seç, butona bas.

### Parametreler

- **İç Boşluk (Offset)** — Adacığı hücre kenarlarından içeri çeker (bleed/padding için).
  Eşleştirmeyi etkilemez, sadece son yerleştirmeyi. Trim hücrelerinde uygulanmaz.
- **Çeşitlilik** — `0` = her zaman en iyi eşleşen tek hücre. Yükseltince benzer
  boyuttaki hücreler de aday olur, adacıklar aralarına dağılır.
- **Rastgele Flip (U/V)** — Yerleştirmeden sonra rastgele aynalar, tekrarı kırar.
- **Seed** — Rastgeleliğin tohumu. Aynı seed aynı sonucu verir; beğenmediğin bir
  dağılım çıkarsa seed'i değiştir.

---

## 2. CK Vertex Painter (`ck_vertex_painter.py`)

**Konum:** 3D View > N paneli > `Vertex Paint`
**Gereklilik:** Edit Mode, yüz seçimi

Seçili yüzlere face-corner vertex rengi uygular. 10 hazır gradyan palet + 25 slotluk
düzenlenebilir özel palet. Paletlerdeki renk seçicileri değiştirebilirsin; uygula
butonu düzenlediğin değeri kullanır. `Sabit Paletleri Sıfırla` ile varsayılana dönersin.

- Renkler `Color` adlı, `BYTE_COLOR` tipinde, `CORNER` domain'inde bir Color Attribute'a
  yazılır. Aynı isimde vertex (POINT) domain'inde bir attribute varsa ona dokunulmaz,
  yeni bir face-corner attribute açılır.
- Attribute ilk kez oluşturulduğunda tüm mesh beyaza boyanır (yeni katman siyah başlar).
  Var olan bir katmana yazarken mevcut boyamaya dokunulmaz.
- Solid shading'deyken görüntüleme otomatik olarak `Vertex` renk moduna geçer.

---

## 3. CK Varlık Bazlı LOD Düzenleyici ve Exporter (`ck_asset_duzenleyici_ve_exporter.py`)

**Konum:** 3D View > N paneli > `Asset Araçları`
**Gereklilik:** Export için Object Mode

### Beklenen hiyerarşi

```
EXPORTS/                  <- "Ana Collection" alanına yazdığın isim
├── SandalyeA/            <- her asset bir alt collection
│   ├── SandalyeA_ASSET/  <- otomatik oluşur, LOD0 buraya taşınır, asset olarak işaretlenir
│   └── SandalyeA_LOD/    <- otomatik oluşur, LOD1+ buraya taşınır
└── MasaB/
```

**Sistemi Senkronize Et** — `_ASSET` ve `_LOD` alt collection'larını oluşturur (eksikse
tamamlar), objeleri isimlerine göre dağıtır (`_LOD0` → ASSET, diğer `_LOD*` → LOD),
mesh datablock isimlerini obje ismiyle eşitler ve `_ASSET`'i asset olarak işaretleyip
ön izleme üretir.

> İsminde `_LOD` geçmeyen objeler taşınmaz ve **export edilmez**. Araç bunu uyarı
> olarak bildirir — collider/pivot gibi objelerin varsa isimlendirmeye dikkat et.

**FBX Olarak Dışa Aktar** — Her asset collection'ını ayrı bir `.fbx` olarak kaydeder.
Gizli veya excluded olan collection/objeleri geçici olarak açar, export sonrası
(hata alsa bile) eski hâline döndürür. FBX ayarları Unity yönelimlidir
(`-Z` forward, `Y` up, bake space transform açık).

---

## Bilinen sınırlar

- **Çoklu obje edit mode desteklenmiyor.** UV araçları yalnızca aktif obje üzerinde
  çalışır; birlikte edit mode'a alınmış diğer objelerdeki seçim sessizce atlanır.
- **Adacık tespiti seam'e bağlı.** Seam işaretlemeden ayrılmış adacıklar (rip/split)
  tek adacık sayılır.
- **Flip = 90° dönüş + ayna.** Hücre eşleşmesinde oran ters olduğunda u/v takas edilir;
  bu bir transpozedir. Yönlü detay içeren dokularda (yazı, tek yönlü gradyan) görüntü
  aynalanmış çıkar.
- **Renk yönetimi.** Palet renkleri `subtype='COLOR'` (scene-linear) olarak tanımlı,
  hedef ise `BYTE_COLOR` attribute. Kendi projendeki renk boru hattında beklediğin
  tonu verdiğini bir kez göz kontrolüyle doğrula.
- Blender 4.2+ *Extensions* sistemi için henüz `blender_manifest.toml` yok; eklentiler
  legacy add-on olarak kurulur.

---

## Lisans

GPL-3.0-or-later — tam metin için [`LICENSE`](LICENSE) dosyasına bakın.

Blender'ın Python API'si (`bpy`) GPL kapsamında olduğundan, onu kullanan ve dağıtılan
eklentiler de GPL uyumlu bir lisansla yayınlanmak zorundadır.

Katkılara açığım: hata bildirimi veya öneri için
[issue açabilirsiniz](https://github.com/cankutk/ck-blender-tools/issues).

---

## English summary

Three standalone Blender add-ons (3.2+) for a game asset pipeline:

- **CK_UV_TOOL** — UV hotspot/trim mapping against an atlas mesh. Atlas cells are
  defined by **seams**; cell type comes from **vertex color** (red = tiling trim,
  blue/unpainted = fixed hotspot). Also includes strip straightening and island
  bounds matching.
- **CK Vertex Painter** — Palette-driven face-corner vertex color painting.
- **CK LOD Organizer & Exporter** — Builds an `_ASSET` / `_LOD` collection hierarchy
  and batch-exports each asset to FBX, temporarily unhiding excluded collections.

UI and reports are in Turkish. Licensed GPL-3.0-or-later.

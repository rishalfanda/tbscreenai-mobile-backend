# Desain Keamanan — Penyimpanan dan Perpindahan Citra

Status: **usulan**. Beberapa keputusan masih menunggu konfirmasi tim, ditandai di
bagian akhir.

## Model ancaman

Sistem ini dirancang dengan asumsi bahwa setiap batas pertahanan suatu saat akan
ditembus. Pertanyaannya bukan bagaimana mencegah pelanggaran, melainkan apa yang
didapat penyerang setelah ia berhasil masuk.

| Skenario | Yang seharusnya didapat penyerang |
|---|---|
| Tablet hilang atau dicuri | tidak ada yang dapat dibaca |
| Salinan basis data server bocor | metadata terenkripsi, tanpa citra dan tanpa identitas |
| Isi object storage bocor | ciphertext tanpa kunci |
| Kredensial satu perangkat bocor | akses satu perangkat, dapat dicabut, tidak menjangkau perangkat lain |
| Media USB berisi berkas berbahaya dicolokkan | ditolak di titik masuk |

Prinsip yang mendasarinya: **tidak ada satu pun kebocoran tunggal yang boleh
menghasilkan citra medis yang dapat dibaca dan dikaitkan ke pasien.**

---

## Lapis 1 — Penyimpanan di perangkat

Perangkat dibawa ke lapangan, dipegang tangan, dan dapat hilang. Seluruh isi
penyimpanan internal diperlakukan sebagai data yang suatu saat akan berada di tangan
orang lain.

**Yang dienkripsi:** basis data lokal beserta antrean outbox, dan seluruh berkas citra —
baik foto kamera maupun berkas DICOM hasil impor.

**Kunci tidak pernah tersimpan di disk.** Kunci diturunkan dari PIN petugas yang
digabungkan dengan kunci perangkat. Penyerang yang mencabut penyimpanan dan memasangnya
di komputer lain hanya menemukan data acak; tidak ada berkas kunci untuk diambil.

**Sesi terkunci otomatis setelah perangkat tidak digunakan.** Perangkat yang ditinggal
dalam keadaan menyala akan menutup sesinya sendiri, dan diperlukan PIN untuk membukanya
kembali.

**Citra dihapus setelah sinkronisasi berhasil**, dengan masa tenggang. Semakin sedikit
data yang menetap di perangkat, semakin kecil kerugian bila perangkat hilang.

**Akses dicabut ketika perangkat kembali terhubung.** Perangkat yang dilaporkan hilang
ditandai pada registry; begitu ia menghubungi server, aksesnya dihentikan dan data lokal
dihapus.

### Mengapa kunci tidak dibuat kedaluwarsa karena lama tidak sinkron

Rancangan awal dokumen ini mengusulkan agar kunci lokal berhenti berlaku bila perangkat
tidak menyinkronkan diri melampaui batas waktu tertentu, sehingga perangkat yang hilang
mengunci dirinya sendiri.

Usulan itu ditarik. Di wilayah 3T, perangkat dapat berada di luar jangkauan jaringan
untuk waktu yang lama sementara masih menyimpan pemeriksaan yang belum terkirim.
Mekanisme tersebut tidak dapat membedakan perangkat yang dicuri dari perangkat yang
sekadar jauh dari sinyal, dan akibatnya adalah kehilangan permanen atas data pasien yang
sah — kerugian yang lebih besar daripada risiko yang hendak dicegahnya.

Penguncian sesi, enkripsi penyimpanan, penghapusan setelah sinkronisasi, dan pencabutan
akses saat perangkat kembali terhubung memberi perlindungan yang memadai tanpa
mempertaruhkan data yang belum sempat dikirim.

Satu batasan tetap perlu dinyatakan terbuka: bila perangkat dicuri dalam keadaan menyala
dan sesi sedang terbuka, kunci berada di memori dan data dapat terbaca. Penguncian
otomatis mempersempit jendela itu, tetapi tidak menghapusnya. Keterbatasan ini berlaku
pada semua sistem enkripsi penyimpanan.

---

## Lapis 2 — Perpindahan data

TLS 1.3 dengan certificate pinning. Pinning diperlukan karena perangkat beroperasi di
jaringan yang tidak dikelola oleh tim, dan sertifikat palsu pada jaringan semacam itu
bukan skenario yang jauh.

Berkas besar diunggah dalam potongan yang dapat dilanjutkan. **Urutannya: enkripsi
lebih dulu, pemotongan untuk transportasi menyusul.** Enkripsi adalah urusan
penyimpanan, pemotongan adalah urusan jaringan; keduanya berada di lapisan berbeda dan
tidak digabungkan.

---

## Lapis 3 — Penyimpanan di server

Setiap citra dienkripsi dengan kunci data yang unik. Setiap kunci data dibungkus oleh
kunci induk yang disimpan di layanan kunci terpisah.
Akibatnya, salinan penuh basis data maupun seluruh isi object storage tidak menghasilkan
apa pun yang dapat dibaca. Penyerang memerlukan kunci induk, dan kunci induk tidak
berada di tempat itu.

Karena setiap citra memiliki kunci sendiri, satu kunci yang bocor tidak membuka seluruh
arsip.

**Penempatan kunci induk.** Kunci induk tidak boleh berada pada mesin yang sama dengan
data yang dilindunginya — penyerang yang menembus mesin tersebut akan memperoleh
keduanya sekaligus, dan seluruh manfaat lapisan ini hilang.

Selama masa pengujian, kunci induk ditempatkan pada layanan kunci yang berjalan dalam
keadaan tersegel: setelah dimulai ulang, layanan tidak dapat membuka kuncinya sendiri.
Kunci pembuka dipecah menjadi beberapa bagian yang dipegang orang berbeda. Penyerang
yang memperoleh salinan disk tidak mendapatkan apa pun yang dapat digunakan.

Untuk tahap pengujian, encryption-at-rest bawaan object storage sudah memenuhi baseline.
Envelope encryption per citra beserta layanan kunci tersegel tercantum sebagai pengerasan
lanjutan, dan diterapkan bila sistem berlanjut ke produksi.

Pemisahan ke mesin tersendiri direncanakan bila sistem berlanjut ke tahap produksi.

**Algoritma.** Enkripsi memakai algoritma standar yang telah lama diuji publik. Skema
buatan sendiri tidak dipakai, betapapun menariknya secara konseptual, karena keamanan
sebuah algoritma bersandar pada bertahun-tahun percobaan serangan yang gagal — bukan
pada kecerdasan perancangnya. Algoritma non-standar juga akan menjadi temuan pada audit
keamanan mana pun.

---

## Lapis 4 — De-identifikasi

Lapisan ini memberi perlindungan terbesar terhadap kerugian nyata, dan paling sering
diremehkan.

Berkas DICOM dari mesin X-ray membawa identitas pasien di dalam header: nama, tanggal
lahir, nomor rekam medis, dan nama institusi. Foto kamera tidak membawa keduanya.

Atribut identitas dihapus sebelum citra disimpan, dan pseudonim dipakai sebagai
penghubung. Pemetaan pseudonim ke identitas asli disimpan terpisah dengan kontrol akses
yang berbeda, dan tidak pernah dapat diambil melalui endpoint mana pun yang dipanggil
aplikasi.

> Citra rontgen yang bocor tanpa identitas pasien adalah insiden. Citra yang sama bocor
> lengkap dengan nama dan nomor rekam medis adalah pelanggaran serius terhadap
> perlindungan data pribadi dan kode etik kedokteran.

Untuk melatih model, identitas pasien tidak dibutuhkan sama sekali. Yang diperlukan
hanya citra dan labelnya.

---

## Lapis 5 — Kontrol akses dan jejak audit

Pembukaan citra untuk keperluan pelatihan model berjalan melalui jalur yang disetujui
dan tercatat: siapa, kapan, berapa banyak, untuk keperluan apa.

Tugas dipisahkan. Pihak yang mengelola basis data bukan pihak yang memegang kunci
induk. Ini menjawab versi yang masuk akal dari gagasan "server tidak boleh tahu isinya":
operator server tidak dapat membuka citra sembarangan, meskipun sistem secara
keseluruhan dapat membukanya ketika memang diperlukan dan tercatat.

---

## Mengapa bukan enkripsi ujung-ke-ujung

Enkripsi ujung-ke-ujung sebagaimana pada aplikasi pesan mengandaikan dua ujung yang
sama-sama manusia, sama-sama ingin membaca isinya, dan sama-sama memegang kunci. Server
hanya meneruskan.

Sistem ini memiliki pihak ketiga yang secara sah perlu membaca citra: radiolog yang
menganotasi, dan tim yang melatih ulang model. Bila server benar-benar tidak pernah
memegang kunci, maka target validasi lanjutan tidak dapat dicapai, model tidak dapat
diperbaiki dengan data lapangan, dan inferensi ulang saat model naik versi menjadi
mustahil.

Enkripsi ujung-ke-ujung karena itu bertentangan dengan tujuan penelitian sistem ini —
bukan karena sulit diterapkan, melainkan karena persyaratannya berlawanan. Envelope
encryption memberi perlindungan setara terhadap kebocoran penyimpanan, tanpa mematikan
siklus perbaikan model.

---

## Pembagian lingkup — baseline dan pengerasan lanjutan

Tidak seluruh lapisan di atas merupakan prasyarat penyimpanan awal. Sebagian adalah
arah pengembangan bila sistem berlanjut ke tahap produksi komersial. Pemisahan ini
dinyatakan eksplisit agar implementasi tidak tertunda menunggu hal yang belum
diperlukan.

### Baseline — prasyarat sebelum citra pertama disimpan

| Kontrol | Lapis |
|---|---|
| Enkripsi penyimpanan pada perangkat | 1 |
| Penguncian sesi otomatis | 1 |
| Penghapusan citra setelah sinkronisasi berhasil | 1 |
| HTTPS untuk seluruh perpindahan data | 2 |
| Encryption-at-rest pada object storage | 3 |
| De-identifikasi berkas DICOM | 4 |
| Kontrol akses berbasis peran | 5 |
| Jejak audit atas akses data | 5 |
| Cadangan data | 5 |
| Masa retensi yang dapat dikonfigurasi | — |

### Pengerasan lanjutan — bila berlanjut ke produksi

| Kontrol | Alasan ditunda |
|---|---|
| Kunci data unik per citra dengan pembungkusan berlapis | Encryption-at-rest sudah memenuhi kebutuhan pengujian |
| Layanan kunci tersegel | Memerlukan prosedur operasional yang belum ada |
| Pembagian kunci pembuka ke beberapa orang | Bergantung pada pembagian peran yang belum ditetapkan |
| Pemisahan layanan kunci ke mesin tersendiri | Bergantung pada keputusan arsitektur penempatan |
| Chip keamanan perangkat keras pada perangkat | Ketersediaan komponen belum dikonfirmasi |

Pemisahan ini tidak mengurangi perlindungan pada tahap pengujian. Baseline sudah
memastikan bahwa perangkat yang hilang tidak terbaca, data yang berpindah terenkripsi,
citra tersimpan dalam keadaan terenkripsi, dan identitas pasien tidak melekat pada citra.


## Retensi data

Retensi bukan satu angka, melainkan beberapa masa berlaku yang berjalan terpisah.

| Data | Lokasi | Usulan | Dasar |
|---|---|---|---|
| Citra di perangkat | penyimpanan tablet | dihapus 30 hari setelah sinkronisasi berhasil, atau saat kapasitas mencapai 80% | membatasi kerugian bila perangkat hilang |
| Citra klinis | server | **menunggu konfirmasi** | kewajiban regulasi rekam medis |
| Kolam data pelatihan | server, terpisah | mengikuti masa berlaku persetujuan etik | etik penelitian dan consent pasien |
| Berkas cadangan | terpisah | 90 hari bergulir, ditambah arsip tahunan | pemulihan bencana |

Masa retensi citra klinis mengikuti ketentuan yang berlaku bagi rekam medis elektronik
dan bukan keputusan teknis. Sistem dirancang agar masa retensi dapat dikonfigurasi,
sehingga penetapan angkanya tidak menghambat pengembangan.

---

## Yang masih menunggu keputusan

| Pertanyaan | Kepada | Menghambat |
|---|---|---|
| Masa retensi citra klinis menurut ketentuan rekam medis | Pak Wahyono / tim klinis | kebijakan penghapusan, ukuran penyimpanan |
| Ketersediaan chip keamanan pada perangkat | Mas Fikry | kekuatan Lapis 1 |
| Prosedur ekspor menyeluruh saat masa sewa server berakhir | tim | keselamatan data pengujian |
| Siapa yang memegang bagian kunci pembuka layanan kunci | Pak Wahyono | penerapan Lapis 3 |
| Siapa berhak membuka citra untuk pelatihan, melalui prosedur apa | tim | penerapan Lapis 5 |
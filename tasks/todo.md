# TBScreen.AI — Task List

Turunan dari `tasks/plan.md`. Urutan mengikuti graf dependensi: fondasi dulu,
tiap task meninggalkan sistem dalam keadaan jalan.

Legenda ukuran: **XS** 1 file · **S** 1–2 · **M** 3–5 · **L** 5–8.

---

## Fase A — Kebersihan & keamanan segera

### Task 1: Penjagaan produksi untuk mock inference
**Deskripsi:** `run_mock_inference` memutuskan positif/negatif TB dengan `random.random()`
dan tidak ada apa pun yang mencegahnya jalan saat `ENV=production`. Ini mode kegagalan
terburuk yang bisa dipunyai sistem ini: vonis acak yang tampil sebagai hasil klinis.

**Acceptance criteria:**
- [ ] `run_mock_inference` menolak jalan (raise) bila `settings.env == "production"`
- [ ] Pesan error menyebut bahwa model asli belum terpasang, bukan stack trace mentah
- [ ] `is_mock: true` tetap ada di respons untuk env non-produksi

**Verification:**
- [ ] Test: `pytest tests/test_security_controls.py -q`
- [ ] Test baru: `ENV=production` + panggil `/diagnoses/infer` → bukan 200
- [ ] `ruff check . && mypy` bersih

**Dependencies:** None
**Files:** `app/services/inference.py`, `tests/test_security_controls.py`
**Ukuran:** XS

---

### Task 2: Merge `fix/critical-data-integrity` ke `master`
**Deskripsi:** Empat commit pengerasan (integritas data, RBAC, rate limit, validasi
upload) hanya ada di branch. `master` masih commit awal, dan CI hanya terpicu di
`master`/`main` + PR — jadi tidak ada yang menjaga kode terkini.

**Acceptance criteria:**
- [ ] `master` berisi seluruh 5 commit
- [ ] CI hijau di `master` (job `quality` **dan** `migrations`)
- [ ] Branch lama dihapus atau ditandai merged

**Verification:**
- [ ] `git log --oneline master` menampilkan `68f4ecf` di puncak
- [ ] GitHub Actions run di `master` hijau

**Dependencies:** Task 1 (masuk dalam merge yang sama)
**Files:** —
**Ukuran:** XS

---

### Task 3: Satukan penamaan versi model
**Deskripsi:** `/diagnoses/infer` mengembalikan `TBScreen v2.1.0`; `/sync/model-version`
mengembalikan `v1.3.1`. Dua skema penamaan untuk satu model berarti layar Result dan
Sync Center melaporkan versi berbeda, dan audit klinis tidak bisa memastikan model mana
yang menghasilkan sebuah putusan.

**Acceptance criteria:**
- [ ] `/infer` mengembalikan versi yang sama dengan `model_versions.is_latest`
- [ ] Sumber kebenaran tunggal: `MOCK_MODEL_VERSION` dibaca dari katalog, bukan konstanta
- [ ] Diagnosis tersimpan membawa versi yang bisa dicocokkan ke baris `model_versions`

**Verification:**
- [ ] Test: `/infer` lalu `/sync/model-version` → `model_version` identik
- [ ] `pytest -q` hijau

**Dependencies:** None
**Files:** `app/services/inference.py`, `app/api/routes/diagnoses.py`, `tests/test_patients_diagnoses.py`
**Ukuran:** S

---

### Task 4: Pindahkan JWT ke penyimpanan aman perangkat
**Deskripsi:** `access_token` dan `refresh_token` (umur 7 hari) tersimpan sebagai TEXT
polos di tabel `AppSettings` drift. Tablet rumah sakit adalah perangkat bersama dan
gampang hilang; siapa pun yang pegang `tbscreen_local.sqlite` punya sesi dokter selama
seminggu. `flutter_secure_storage` membungkus Android Keystore / iOS Keychain — bukan
storage layer yang menyaingi drift, jadi tidak melanggar larangan paket di `CLAUDE.md`.

**Acceptance criteria:**
- [ ] Token dibaca/ditulis lewat `flutter_secure_storage`, bukan `AppSettings`
- [ ] `AppSettings` tetap dipakai untuk `installed_model_version` + `last_sync_at`
- [ ] Migrasi: token lama di drift dihapus saat pertama kali app baru jalan
- [ ] `logout()` menghapus token dari Keystore **dan** cache drift

**Verification:**
- [ ] `flutter test` hijau (test `SettingsStore` disesuaikan)
- [ ] Manual: login → kill app → buka lagi → masih login; lalu logout → tidak ada residu

**Dependencies:** None
**Files:** `pubspec.yaml`, `lib/data/local/settings_store.dart`, `lib/data/http/api_client.dart`, `lib/main.dart`, `test/`
**Ukuran:** M

---

### Task 4b: Perbaiki konfigurasi rilis Android
**Deskripsi:** `android.permission.INTERNET` hanya ada di `android/app/src/debug/AndroidManifest.xml`
dan `profile/`, tidak di `main/`. Manifest hasil merge untuk build **release** karenanya tidak
punya izin jaringan sama sekali — APK release tidak bisa login, sync, maupun inferensi. Cacat ini
tak terlihat di seluruh pengujian yang ada karena `flutter test` dan `flutter run` selalu debug.

Sekalian: `applicationId` masih `com.example.myapp`, `android:label` masih `myapp`, dan build
release ditandatangani dengan kunci debug.

**Acceptance criteria:**
- [ ] `android.permission.INTERNET` dideklarasikan di `main/AndroidManifest.xml`
- [ ] `applicationId` dan `namespace` diganti ke identitas nyata (mis. `id.tbscreen.doctor`)
- [ ] `android:label` = "TBScreen.AI"
- [ ] Signing config rilis memakai keystore sungguhan, kredensialnya di luar version control
- [ ] Orientasi dikunci landscape bila memang itu target perangkatnya

**Verification:**
- [ ] `./gradlew :app:processReleaseMainManifest` lalu manifest merge memuat `INTERNET`
- [ ] `flutter build apk --release` lalu pasang di emulator → login berhasil
- [ ] Jalankan `apksigner verify --print-certs` → bukan kunci debug

**Dependencies:** None
**Files:** `android/app/src/main/AndroidManifest.xml`, `android/app/build.gradle.kts`, `android/key.properties` (di-gitignore), `pubspec.yaml`
**Ukuran:** S

---

### Task 4c: Hentikan Result menampilkan data klinis karangan
**Deskripsi:** Dengan formulir Diagnosis dikosongkan sepenuhnya, layar Result tetap merender
Gender `Female`, Comorbidity `None`, Smoking Status `No`, TB Contact `Unknown`, Sputum (BTA)
`Negative`, dan Culture `Negative`. Tidak satu pun dimasukkan pengguna. Layar yang sama punya
tombol Save, Export PDF, dan Print — jadi nilai-nilai itu bisa keluar sebagai dokumen klinis.
Dashboard juga menyapa "Dr. Anderson" alih-alih pengguna yang benar-benar login.

**Acceptance criteria:**
- [ ] Field yang tidak diisi tampil sebagai "—", bukan nilai default yang tampak faktual
- [ ] Result menolak dirender tanpa pasien terpilih, atau menandai dirinya "Draf / Tanpa Pasien"
- [ ] Sapaan Dashboard memakai `UserProfile` dari sesi, bukan konstanta mock
- [ ] Save / Export PDF / Print dinonaktifkan selama hasil belum terikat ke pasien

**Verification:**
- [ ] Widget test: outcome tanpa data pasien → nol nilai klinis terkarang
- [ ] Manual: login → inferensi tanpa isi formulir → Result tidak menampilkan "Negative" mana pun

**Dependencies:** None
**Files:** `lib/features/result/presentation/result_screen.dart`, `lib/features/dashboard/presentation/dashboard_screen.dart`, `lib/state/diagnosis_provider.dart`, `test/result_screen_test.dart`
**Ukuran:** M

---

## Checkpoint A
- [ ] `pytest -q` 134+ hijau · coverage ≥ 95 %
- [ ] `flutter build apk --release` terpasang dan bisa login (bukan hanya debug)
- [ ] `flutter test` 40+ hijau
- [ ] `ruff` + `mypy` bersih
- [ ] `master` = kode terkini, CI hijau
- [ ] Tinjau bersama sebelum lanjut Fase B

---

## Fase B — Sambungkan input klinis nyata

### Task 5: Capture X-ray asli menggantikan placeholder
**Deskripsi:** `CameraScreen` tidak punya plugin kamera — ia menggambar kotak fokus di
atas `Container` hitam. Inferensi dijalankan atas `XrayImage.placeholder()`, PNG 1×1
transparan. Alur `capture → infer` tersambung secara kode, tapi belum pernah membawa
citra nyata satu kali pun.

**Acceptance criteria:**
- [ ] `camera` + `image_picker` terpasang; `CameraScreen` menampilkan preview asli
- [ ] Hasil capture menjadi `XrayImage` dengan bytes nyata + mime type benar
- [ ] Ada jalur alternatif "pilih dari galeri" (rontgen sering difoto lebih dulu)
- [ ] Permission ditangani: ditolak → pesan jelas, bukan layar hitam

**Verification:**
- [ ] Widget test dengan `camera` di-mock
- [ ] Manual di tablet Android: capture → `/infer` → hasil tampil
- [ ] `flutter analyze` bersih

**Dependencies:** Task 4 (sama-sama menyentuh `pubspec.yaml`)
**Files:** `pubspec.yaml`, `lib/features/camera/presentation/camera_screen.dart`, `lib/domain/models/xray_image.dart`, `android/app/src/main/AndroidManifest.xml`, `test/`
**Ukuran:** M

---

### Task 6: Object storage (MinIO) di stack dev
**Deskripsi:** Belum ada tempat menyimpan citra. Keputusan ADR-001 sudah diambil —
MinIO, alasan penentunya kedaulatan data (citra medis tidak keluar dari infrastruktur RS),
bukan skalabilitas.

**Acceptance criteria:**
- [ ] MinIO di `docker-compose.yml` dengan healthcheck
- [ ] `boto3` client terbungkus service, endpoint dari config
- [ ] Bucket dibuat otomatis saat startup bila belum ada
- [ ] `.env.example` mendokumentasikan variabel baru

**Verification:**
- [ ] `docker compose up -d` → MinIO healthy
- [ ] Test: put lalu get object kembali utuh
- [ ] `mypy` bersih

**Dependencies:** None
**Files:** `docker-compose.yml`, `requirements.txt`, `app/core/config.py`, `app/services/storage.py`, `.env.example`
**Ukuran:** M

---

### Task 7: Simpan citra & isi `Diagnosis.image_path`
**Deskripsi:** `read_validated_image` sudah memegang bytes-nya — itu fungsi yang tepat
untuk menyerahkannya ke storage. Sekarang bytes itu dibuang begitu respons terkirim,
sehingga re-inferensi saat model naik versi mustahil dan audit klinis tidak bisa
menampilkan citra di samping putusan AI.

**Acceptance criteria:**
- [ ] `/diagnoses/infer` menyimpan citra dan mengembalikan referensi objeknya
- [ ] `POST /diagnoses` menerima referensi itu dan mengisi `image_path`
- [ ] Ada endpoint ambil citra, tunduk pada isolasi tenant yang sama (RS lain → 404)
- [ ] Citra yatim (di-infer tapi diagnosis tidak pernah disimpan) punya kebijakan pembersihan

**Verification:**
- [ ] Test: infer → simpan → ambil → bytes identik
- [ ] Test isolasi tenant untuk endpoint ambil citra
- [ ] `pytest -q --cov` ≥ 95 %

**Dependencies:** Task 6
**Files:** `app/api/routes/diagnoses.py`, `app/services/storage.py`, `app/schemas/diagnosis.py`, `tests/test_patients_diagnoses.py`, `tests/test_tenant_isolation.py`
**Ukuran:** M

---

### Task 8: Test E2E alur klinis penuh
**Deskripsi:** `TESTING.md` menyatakan jujur bahwa alur `login → diagnosis → result → sync`
belum pernah jadi test otomatis. Setelah Task 5 dan 7, alur itu akhirnya punya semua
bagian nyatanya.

**Acceptance criteria:**
- [ ] `integration_test/` menjalankan login → capture → infer → simpan → validasi → sync
- [ ] Berjalan lawan backend nyata (bukan stub)
- [ ] Kegagalan menunjuk langkah spesifik, bukan "test gagal"

**Verification:**
- [ ] `flutter test integration_test/` hijau di emulator dengan backend hidup
- [ ] Terdokumentasi di `TESTING.md`, termasuk apa yang masih tidak tercakup

**Dependencies:** Task 5, Task 7
**Files:** `integration_test/clinical_flow_test.dart`, `TESTING.md`
**Ukuran:** M

---

## Checkpoint B
- [ ] Satu X-ray asli bisa ditelusuri utuh: capture → infer → simpan → tampil ulang
- [ ] Nol `XrayImage.placeholder()` di jalur produksi
- [ ] E2E hijau
- [ ] Tinjau bersama sebelum lanjut Fase C

---

## Fase C — Kelengkapan peran & data nyata

### Task 9: Endpoint `admin_rs` dan `super_admin`
**Deskripsi:** Dua dari tiga peran di spesifikasi belum punya backend sama sekali —
tidak ada CRUD user maupun hospital. `require_roles` sudah terpasang di semua route
yang ada, jadi pondasinya siap.
**Ukuran:** L → **pecah lebih dulu** menjadi: (a) CRUD user dalam satu tenant untuk
`admin_rs`; (b) CRUD hospital + user lintas tenant untuk `super_admin`.
**Dependencies:** Task 2

### Task 10: Cabut mock Dashboard / Validation / Dataset
**Deskripsi:** Tiga dari delapan item NavRail menampilkan data karangan meski backend
hidup. Di aplikasi medis, angka fiktif yang tampak resmi adalah bahaya demo.
**Ukuran:** L → pecah per repository (satu endpoint + satu impl HTTP per task).
**Dependencies:** Task 9 (endpoint analitik menyusul CRUD)

### Task 11: Tabel audit akses
**Deskripsi:** `sync_logs` mencatat operasi sinkronisasi, bukan siapa membaca atau
mengubah apa lewat REST. Regulasi rekam medis menuntut jejak itu. `structlog` +
request-id sudah direkomendasikan ADR-001.
**Ukuran:** M
**Dependencies:** Task 2

### Task 12: Denylist refresh token + rate limit lintas-proses
**Deskripsi:** Logout hanya membuang token di sisi klien; token curian tetap sah 7 hari.
Rate limit `slowapi` dihitung per-proses, jadi N worker = N kali budget.
**Ukuran:** M
**Dependencies:** Task 2

---

## Checkpoint C
- [ ] Nol repository mock saat `USE_HTTP=true`
- [ ] Setiap baca/tulis rekam medis punya jejak audit
- [ ] Ketiga peran punya klien yang berfungsi

---

## Fase D — Model AI asli (bergantung tim AI)

### Task 13: Ganti `run_mock_inference` dengan `onnxruntime` + `Pillow`
**Blocker:** artefak model `.onnx` belum ada. Perlu jawaban Pertanyaan Terbuka #2.
**Ukuran:** L

### Task 14: Metrik Prometheus untuk latensi inferensi
**Ukuran:** S · **Dependencies:** Task 13

### Task 15: Load test sync (500+ item)
**Ukuran:** S · **Dependencies:** Task 2

---

## Checkpoint D — Siap pilot
- [ ] Seluruh acceptance criteria Fase A–D terpenuhi
- [ ] Nol jalur kode yang bisa menghasilkan vonis TB acak
- [ ] Kebijakan retensi citra terjawab dan terimplementasi
- [ ] Siap tinjauan klinis

---

## Fase E — Lingkup Backend Engineer (usulan)

Turunan dari arahan Pak Wahyono dan Mas Risha pada rapat 24 Agustus 2026.
Task-task ini adalah mandat langsung yang belum tercatat di plan.md.

Catatan urutan: Task 16 adalah fondasi — Task 17 dan 18 bergantung padanya.
Ditaruh di akhir agar penomoran Fase A–D tidak bergeser; silakan diatur ulang
kalau lebih baik disisipkan sesuai graf dependensi.

---

### Task 16: Device Registry
**Deskripsi:** `sync_logs.device_id` bertipe `String(100)`, nullable, dan tidak
terhubung ke tabel mana pun. Perangkat tidak pernah diregistrasi, sehingga server
tidak bisa menjawab tiga pertanyaan yang menentukan operasional lapangan: perangkat
mana yang masih memakai model versi lama, perangkat mana yang belum sinkron berhari-hari,
dan perangkat mana yang harus di-rollback bila rilis model bermasalah. Tanpa registry,
rollout model bertahap dan pencabutan akses perangkat hilang tidak mungkin dilakukan.

Aturan relasi dari Pak Wahyono: satu rumah sakit boleh mengoperasikan dua perangkat
atau lebih, tetapi satu perangkat tidak pernah melayani dua rumah sakit. Aturan itu
diterjemahkan menjadi foreign key non-nullable dari `devices` ke `hospitals`.

**Acceptance criteria:**
- [ ] Tabel `devices` dengan `device_code` unik dan `hospital_id` FK non-nullable
- [ ] Kolom status siklus hidup: `pending | active | suspended | decommissioned`
- [ ] `sync_logs.device_id` menjadi FK ke `devices.id`, dengan migrasi data lama
- [ ] `POST /devices` provisioning — khusus `super_admin`
- [ ] `GET /devices` — `admin_rs` hanya melihat perangkat rumah sakitnya
- [ ] `POST /devices/{id}/revoke` mencabut akses perangkat hilang
- [ ] Perangkat memiliki kredensial sendiri, terpisah dari JWT dokter
- [ ] `GET /devices/fleet-status` melaporkan versi model dan waktu sinkron terakhir

**Verification:**
- [ ] Test: kredensial perangkat RS A ditolak saat dipakai untuk data RS B
- [ ] Test: perangkat yang dicabut tidak bisa lagi melakukan sync push
- [ ] Test: `hospital_id` tidak boleh null pada level database
- [ ] `alembic downgrade -1 && alembic upgrade head` berjalan bersih
- [ ] `pytest -q --cov` ≥ 95 %

**Dependencies:** None
**Files:** `app/models/device.py`, `app/models/sync_log.py`, `app/schemas/device.py`, `app/api/routes/devices.py`, `app/api/deps.py`, `alembic/versions/`, `tests/test_devices.py`
**Ukuran:** L

---

### Task 17: Distribusi artefak model ke perangkat
**Deskripsi:** `model_versions.download_url` bertipe nullable dan tidak pernah diisi,
termasuk di `scripts/seed.py`. Yang tersedia sekarang adalah *pengecekan versi*, bukan
*transfer model*: server dapat memberitahu bahwa v1.3.1 berukuran 47,2 MB tersedia,
lalu perangkat bertanya "unduh dari mana" dan jawabannya kosong. Di sisi Flutter,
`ModelUpdateCard` sudah memiliki state machine idle → checking → updateAvailable
beserta indikator progres unduhan — rangkanya siap, mesinnya belum ada.

Katalog juga belum memuat cara memverifikasi artefak. Tanpa checksum, artefak yang
rusak separuh saat diunduh di jaringan 3T tidak akan terdeteksi. Tanpa tanda tangan,
kanal update model menjadi jalur injeksi perilaku ke alat kesehatan: siapa pun yang
dapat mendorong artefak palsu ke perangkat sedang mengubah hasil skrining pasien.

Selain itu `is_latest` tidak memiliki constraint keunikan. Dua baris dapat sama-sama
bernilai `True`, dan `db.scalar()` di `app/api/routes/sync.py` akan mengembalikan
salah satunya secara acak tanpa menimbulkan error — perangkat yang berbeda bisa
mengunduh artefak yang berbeda tanpa ada yang menyadari.

**Acceptance criteria:**
- [ ] Kolom baru di `model_versions`: `sha256`, `signature`, `artifact_key`, `min_app_version`, `target_hardware`
- [ ] Partial unique index memastikan hanya satu baris `is_latest = true`
- [ ] Artefak disimpan di object storage, bukan sebagai kolom biner di database
- [ ] Endpoint penyaji artefak mengembalikan presigned URL berumur pendek
- [ ] Unduhan bersifat resumable — putus di tengah dilanjut, bukan diulang dari nol
- [ ] Perangkat memverifikasi checksum **dan** tanda tangan sebelum artefak diaktifkan
- [ ] Pergantian artefak bersifat atomik, diikuti health check, dan otomatis rollback ke versi sebelumnya bila gagal
- [ ] Rollout bertahap per cohort perangkat (10 % → 50 % → 100 %)
- [ ] Setiap hasil inferensi mencatat versi artefak, bukan hanya versi model logis

**Verification:**
- [ ] Test: dua baris `is_latest = true` ditolak di level database
- [ ] Test: artefak dengan checksum tidak cocok ditolak dan tidak pernah diaktifkan
- [ ] Test: perangkat yang tertinggal beberapa versi langsung menerima versi terbaru, tidak berurutan satu per satu
- [ ] Test: perangkat di luar cohort canary tetap menerima versi lama
- [ ] `alembic downgrade -1 && alembic upgrade head` berjalan bersih
- [ ] `pytest -q --cov` ≥ 95 %

**Dependencies:** Task 6 (object storage), Task 16 (registry perangkat untuk cohort dan rollback)
**Files:** `app/models/model_version.py`, `app/schemas/model_version.py`, `app/api/routes/sync.py`, `app/services/storage.py`, `alembic/versions/`, `scripts/seed.py`, `tests/test_model_distribution.py`
**Ukuran:** L

---

### Task 18: Upload citra chunked & resumable
**Deskripsi:** Sinkronisasi saat ini hanya menangani record; citra belum punya jalur
sinkronisasi sama sekali. Rontgen berukuran megabyte, dan perangkat beroperasi di
wilayah dengan konektivitas terputus-putus. Upload sekali kirim berarti kegagalan di
detik terakhir memaksa mengulang dari nol — pada jaringan 3T, citra semacam itu
berpotensi tidak pernah terkirim sama sekali.

Metadata dan citra juga perlu dipisahkan jalurnya. Metadata berukuran kilobyte dan
harus lolos lebih dulu, sehingga server minimal mengetahui bahwa skrining terjadi
meski citranya menyusul belakangan.

**Acceptance criteria:**
- [ ] `POST /blobs/init` mengembalikan `upload_id` dan daftar chunk yang sudah diterima
- [ ] `PUT /blobs/{upload_id}/chunks/{n}` menerima potongan dengan `Content-Range`
- [ ] `POST /blobs/{upload_id}/complete` merakit dan memverifikasi SHA-256 keseluruhan
- [ ] Sesi yang terputus dilanjut dari chunk terakhir yang di-ACK, bukan dari nol
- [ ] Antrian berprioritas: metadata mendahului citra
- [ ] Throttling bandwidth agar tidak menghabiskan jaringan puskesmas
- [ ] Retry memakai exponential backoff dengan jitter
- [ ] Error diklasifikasi: 5xx di-retry, payload cacat ditandai poisoned dan dihentikan

**Verification:**
- [ ] Test: upload diputus di tengah lalu dilanjut → berkas akhir identik
- [ ] Test: checksum tidak cocok → server meminta chunk yang rusak dikirim ulang
- [ ] Test: chunk dikirim dua kali tidak menghasilkan duplikasi
- [ ] `pytest -q --cov` ≥ 95 %

**Dependencies:** Task 6, Task 16
**Files:** `app/api/routes/blobs.py`, `app/schemas/blob.py`, `app/services/storage.py`, `tests/test_blob_upload.py`
**Ukuran:** M

---

### Task 19: Backup server dan pemulihan terverifikasi
**Deskripsi:** Belum ada mekanisme backup sama sekali. "Backup" dan "sinkronisasi"
adalah dua hal berbeda dan keduanya diminta: sinkronisasi memindahkan data lapangan
ke server, backup melindungi server itu sendiri dari kehilangan data.

Backup yang tidak pernah diuji pemulihannya bukan backup, melainkan asumsi. Karena
itu latihan restore masuk sebagai kriteria, bukan sekadar catatan.

**Acceptance criteria:**
- [ ] Postgres WAL archiving aktif dengan point-in-time recovery
- [ ] Backup object storage terjadwal
- [ ] Berkas backup terenkripsi, kuncinya disimpan terpisah dari backup
- [ ] Katalog `model_versions` ikut ter-backup, sehingga hasil skrining lama tetap dapat ditelusuri ke artefak yang menghasilkannya
- [ ] Kebijakan retensi backup terdokumentasi

**Verification:**
- [ ] Latihan restore ke instance kosong berhasil dan datanya utuh
- [ ] Point-in-time recovery ke satu jam sebelumnya berhasil
- [ ] Prosedur restore terdokumentasi dan dapat diikuti orang lain

**Dependencies:** Task 6
**Files:** `docker-compose.prod.yml`, `docs/BACKUP_RECOVERY.md`, `scripts/backup.sh`
**Ukuran:** M

---

### Task 20: Akses admin darurat saat perangkat offline
**Deskripsi:** Bila perangkat berada di lokasi tanpa konektivitas dan administrator
perlu masuk, tidak ada jalur sama sekali saat ini. Diperlukan mekanisme akses darurat
yang tidak bergantung pada jaringan.

TOTP kurang tepat untuk konteks ini karena bergantung pada jam yang tersinkron,
sementara perangkat di wilayah 3T sering beroperasi tanpa NTP dan jamnya melenceng.
Challenge–response tidak memiliki ketergantungan itu: perangkat menampilkan kode
tantangan, administrator menghitung jawabannya, petugas memasukkannya.

**Acceptance criteria:**
- [ ] Mekanisme challenge–response, bukan kode statis
- [ ] Kode berumur pendek dan hanya berlaku satu kali
- [ ] Setiap pemakaian tercatat di audit log beserta identitas perangkat
- [ ] Percobaan gagal dibatasi lajunya
- [ ] Akses dapat dicabut saat perangkat kembali online

**Verification:**
- [ ] Test: kode yang sudah dipakai ditolak pada percobaan kedua
- [ ] Test: kode milik perangkat lain ditolak
- [ ] Test: percobaan gagal berulang memicu pembatasan
- [ ] `pytest -q --cov` ≥ 95 %

**Dependencies:** Task 16
**Files:** `app/services/break_glass.py`, `app/api/routes/devices.py`, `app/models/audit_log.py`, `tests/test_break_glass.py`
**Ukuran:** M

---

### Task 21: De-identifikasi PHI sebelum data masuk kolam pelatihan
**Deskripsi:** Forum diseminasi merekomendasikan validasi lanjutan hingga 40.000 citra,
dan citra itu akan datang dari lapangan. Data pasien tidak boleh berpindah ke kolam
pelatihan dalam bentuk teridentifikasi.

Jawaban yang tepat bukan mengeluarkan record pasien dari sinkronisasi — citra tanpa
record tidak dapat diaudit secara klinis dan tidak bernilai sebagai data pelatihan.
Yang tepat adalah membuang atribut identitas dan menyimpan pseudonim sebagai penghubung,
sehingga privasi terjaga tanpa kehilangan ketertelusuran.

**Acceptance criteria:**
- [ ] Tag PHI pada berkas DICOM dihapus sebelum data masuk kolam pelatihan
- [ ] Pemetaan pseudonim ke identitas asli disimpan terpisah dengan kontrol akses berbeda
- [ ] Flag consent per pasien menentukan boleh atau tidaknya data dipakai untuk pelatihan
- [ ] Data tanpa consent tidak pernah masuk kolam pelatihan
- [ ] Pencabutan consent memicu penghapusan dari kolam pelatihan

**Verification:**
- [ ] Test: berkas DICOM hasil de-identifikasi tidak lagi memuat tag identitas
- [ ] Test: pasien tanpa consent tidak muncul di ekspor data pelatihan
- [ ] Test: pencabutan consent menghapus data terkait

**Dependencies:** Task 7
**Files:** `app/services/deidentify.py`, `app/models/patient.py`, `app/api/routes/exports.py`, `alembic/versions/`, `tests/test_deidentification.py`
**Ukuran:** M
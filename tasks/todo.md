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

# TBScreenAI — Architecture Review, Code Review & Roadmap ke Produksi

**Tanggal:** 2026-07-29
**Cakupan:** `tbscreenai-backend` (FastAPI) + `tbscreenai-mobile-frontend` (Flutter)
**Basis:** `docs/SUPER_PROMPT_TBSCREENAI.md`
**Status verifikasi:** backend `pytest` → **50 passed** (89 s). frontend `flutter test` → **30 passed** (15 s).

---

## 0. Ringkasan Eksekutif

Arsitekturnya **sehat**. Layering (domain → repository interface → impl mock/http/offline) konsisten,
tenant isolation dirancang di dependency layer bukan ditempel per-handler, dan sync idempotency
(`client_op_id` + `UNIQUE(tenant_id, client_op_id)`) dikerjakan dengan benar di kedua sisi.
Ini bukan scaffold asal jadi.

Tiga hal yang membuat proyek ini **belum bisa disebut selesai**, dan semuanya struktural, bukan kosmetik:

1. **Alur klinis inti tidak tersambung.** Frontend tidak pernah memanggil `POST /diagnoses/infer`.
   `CameraScreen` 100 % palsu (tidak ada plugin kamera, `attachMockImage('captured_xray.png')`).
   Jadi rantai *capture → infer → save → validate* belum pernah jalan end-to-end sekali pun.
2. **RBAC tidak ada.** `require_roles()` ditulis di `app/api/deps.py:60` lalu **tidak pernah dipakai
   satu kali pun.** Semua endpoint terbuka untuk ketiga peran. `admin_rs` dan `super_admin` juga tidak
   punya endpoint sama sekali (tidak ada CRUD user/hospital) — dua dari tiga peran di spesifikasi
   belum punya backend.
3. **Aturan bisnis bisa dilewati lewat jalur sync.** Validasi yang dipaksakan di REST tidak
   dipaksakan di `/sync/push`. Detail di temuan **C-1**.

Sisanya — rate limiting, soft delete, image storage, CORS — sudah kamu catat sendiri di SUPER_PROMPT
dan memang benar. Dokumen ini menambahkan yang **belum** ada di daftar itu.

---

# 1. ADR-001 — Tech Stack Tambahan untuk Menuntaskan TBScreenAI

**Status:** Proposed
**Deciders:** kamu (owner), + tim AI saat model asli siap

## Context

Stack sekarang minimalis dan itu keputusan yang tepat untuk fase MVP. Tapi ada empat gaya
kebutuhan yang stack sekarang **tidak bisa** penuhi tanpa dependency baru:

| Kebutuhan | Kenapa stack sekarang tidak cukup |
|---|---|
| Simpan citra X-ray | Tidak ada object storage. `Diagnosis.image_path` selalu `NULL`. |
| Rate limit login | FastAPI tidak punya throttle bawaan; butuh store hitungan lintas-worker. |
| Ambil foto rontgen | Flutter tanpa plugin kamera tidak bisa akses hardware. |
| Bukti kualitas | Tidak ada linter, type-checker, coverage, atau CI di kedua repo. |

Constraint yang saya pegang: **jangan tambah dependency kalau kode 30 baris menyelesaikannya**,
dan hormati larangan storage di SUPER_PROMPT (§ Forbidden Packages).

## Decision

Adopsi **12 dependency** dalam 3 gelombang. Sisanya (Kafka, Celery, GraphQL, microservices,
Kubernetes) **ditolak eksplisit** — lihat § Ditolak.

---

### Gelombang 1 — Wajib sebelum bisa disebut "selesai"

| # | Tech | Repo | Menggantikan / mengisi |
|---|---|---|---|
| 1 | **`ruff`** | backend | Tidak ada linter sama sekali |
| 2 | **`mypy`** | backend | Type hints ada tapi tidak pernah dicek |
| 3 | **`pytest-cov`** | backend | Angka "50 test" tidak berarti tanpa tahu apa yang tersentuh |
| 4 | **`camera`** + **`image_picker`** | frontend | `CameraScreen` yang palsu |
| 5 | **`flutter_secure_storage`** | frontend | JWT sekarang plaintext di SQLite |
| 6 | **GitHub Actions** | keduanya | Tidak ada CI; backend malah **belum punya satu commit pun** |

**Catatan #5 vs § Forbidden Packages.** SUPER_PROMPT melarang `shared_preferences`/`hive` — dan itu
benar, keduanya *storage layer* yang menyaingi drift. `flutter_secure_storage` bukan itu: ia
wrapper ke **Android Keystore / iOS Keychain**, khusus untuk secret. Saya rekomendasikan
pengecualian tertulis: **token pindah ke Keystore, semua data lain tetap di drift.** Tabel
`AppSettings` tetap ada untuk `installed_model_version` dan `last_sync_at`.

Alasannya konkret: tablet rumah sakit itu perangkat bersama dan gampang hilang. Sekarang
`access_token` + `refresh_token` (masa hidup 7 hari) tersimpan sebagai TEXT polos di
`tbscreen_local.sqlite`. Siapa pun yang pegang file itu punya sesi dokter selama seminggu.

**Catatan #6.** `git log` di `tbscreenai-backend` mengembalikan
`your current branch 'master' does not have any commits yet`. Seluruh backend — 39 file Python,
50 test — **tidak ter-version-control.** Ini risiko tertinggi di seluruh proyek dan perbaikannya
paling murah. Kerjakan pertama:

```bash
cd /c/Users/devel/tbscreenai-backend && git add -A && git commit -m "Initial commit: FastAPI backend, 50 tests green"
```

---

### Gelombang 2 — Pra-produksi (checklist SUPER_PROMPT § 8)

| # | Tech | Untuk | Alternatif yang dipertimbangkan |
|---|---|---|---|
| 7 | **`slowapi`** | Rate limit `/auth/login` | `fastapi-limiter` (butuh Redis) — tolak dulu, in-memory cukup untuk 1 worker |
| 8 | **`boto3` + MinIO** | Simpan X-ray | Filesystem lokal — tolak, tidak bisa multi-instance |
| 9 | **`python-magic`** | Validasi magic-byte citra | Lihat temuan **S-3**; `content_type` dari klien tidak bisa dipercaya |
| 10 | **`structlog`** + middleware request-id | Audit trail data medis | Logging stdlib — bisa, tapi structlog memberi JSON siap-agregasi |
| 11 | **`testcontainers`** | Test lawan Postgres asli | Lihat temuan **T-1** — jalur JSONB sekarang **tidak pernah diuji** |
| 12 | **`sentry-sdk`** + **`sentry_flutter`** | Error tracking | Sudah kamu tandai "Sentry?" di SUPER_PROMPT — ya, ambil |

---

### Gelombang 3 — Saat model AI asli tiba

| Tech | Peran |
|---|---|
| **`onnxruntime`** | Runtime inferensi. Portabel CPU→GPU tanpa ganti kode; ganti `run_mock_inference` saja. |
| **`Pillow`** | Preprocessing (resize, normalize) sebelum tensor. |
| **`prometheus-fastapi-instrumentator`** | Latensi p95 inferensi — angka yang akan ditanya rumah sakit. |
| **`redis`** | Naik kelas dari `slowapi` in-memory + denylist refresh token (lihat **S-2**). |

---

### Trade-off Analysis: penyimpanan citra

Ini satu-satunya keputusan Gelombang 2 yang punya konsekuensi jangka panjang nyata.

#### Opsi A: MinIO (S3-compatible), self-hosted

| Dimensi | Penilaian |
|---|---|
| Kompleksitas | Sedang — satu service tambahan di compose |
| Biaya | Nol saat dev; saat prod = biaya disk saja |
| Skalabilitas | Baik — API identik dengan S3, migrasi ke cloud nol perubahan kode |
| Familiaritas tim | Rendah, tapi permukaan API-nya kecil (`put_object`/`get_object`) |
| Kedaulatan data | **Tinggi** — citra medis tidak pernah meninggalkan infrastruktur RS |

**Pros:** data pasien tetap on-premise (relevan untuk regulasi kesehatan Indonesia); satu baris
ganti endpoint untuk pindah ke S3 nanti; sudah kamu rencanakan di SUPER_PROMPT § 8.
**Cons:** kamu yang bertanggung jawab atas backup dan ketahanan disk.

#### Opsi B: Simpan langsung ke kolom `BYTEA` Postgres

| Dimensi | Penilaian |
|---|---|
| Kompleksitas | Rendah — tanpa service baru |
| Biaya | Nol tambahan |
| Skalabilitas | **Buruk** — rontgen 5–20 MB akan membengkakkan DB, memperlambat backup dan `pg_dump` |
| Familiaritas tim | Tinggi |

**Pros:** transaksional bersama baris `Diagnosis`; tanpa infra tambahan.
**Cons:** ukuran DB meledak; backup jadi berjam-jam; blob besar merusak cache Postgres.

#### Opsi C: S3/DO Spaces terkelola sejak awal

**Pros:** tanpa operasional. **Cons:** citra medis pasien Indonesia keluar dari infrastruktur RS —
hambatan pengadaan yang serius, dan tidak bisa jalan di deployment on-premise.

### Keputusan

**Opsi A (MinIO).** Faktor penentunya bukan skalabilitas, tapi **kedaulatan data**: MinIO adalah
satu-satunya opsi yang jalan baik di on-premise maupun cloud tanpa ganti kode aplikasi. Opsi B
gagal justru pada beban yang pasti datang (rontgen itu besar dan tidak pernah dihapus).

## Consequences

**Jadi lebih mudah:** `Diagnosis.image_path` akhirnya punya arti; re-inferensi saat model naik versi
jadi mungkin (citra tersimpan); audit klinis bisa menampilkan citra asli di samping putusan AI.

**Jadi lebih sulit:** satu service lagi di dev-loop; butuh kebijakan retensi (berapa lama rontgen
disimpan — ini pertanyaan legal, bukan teknis, dan perlu jawaban sebelum produksi);
`docker compose up` jadi lebih berat.

**Yang perlu ditinjau ulang:** kalau volume tembus ~5 TB, MinIO single-node jadi bottleneck →
evaluasi mode terdistribusi atau S3.

## Ditolak eksplisit

| Tech | Alasan |
|---|---|
| Kafka / RabbitMQ | Sync sudah request/response idempoten. Antrian menambah *at-least-once delivery* yang justru sudah kamu selesaikan lewat `client_op_id`. Nol keuntungan. |
| Celery | Satu-satunya kerja async adalah inferensi. Jalankan sinkron dulu; ukur; baru pertimbangkan `BackgroundTasks`. |
| Microservices | Dua repo, satu tim. Modular monolith itu jawaban yang benar di sini. |
| GraphQL | Kliennya satu (tablet), kontraknya stabil. REST lebih tepat. |
| `freezed` / `json_serializable` | Model manual sekarang ~12–44 baris dan terbaca. Codegen menambah beban build_runner untuk keuntungan tipis. |
| Riverpod | `provider` sudah jalan dan diuji. Migrasi = risiko tanpa imbalan. |

---

# 2. Code Review — Temuan Berurut Keparahan

Kolom **Sumber** menandai apakah ini temuan baru atau sudah kamu catat di SUPER_PROMPT.

## Kritis — bisa merusak / menghilangkan data medis

### C-1 · Aturan validasi klinis dilewati lewat jalur sync
**File:** [`app/services/sync.py:130-133`](../app/services/sync.py) vs [`app/api/routes/diagnoses.py:108-113`](../app/api/routes/diagnoses.py) · **Sumber:** baru

Jalur REST memaksakan dua aturan pada update diagnosis:
- `status` harus salah satu dari `pending|agreed|disagreed` (pola Pydantic)
- `disagreed` **wajib** disertai `doctor_note` (422 kalau tidak)

Jalur sync melewatkan keduanya:

```python
# app/services/sync.py:130-133
allowed = {k: v for k, v in item.payload.items()
           if k in ("status", "doctor_note")}
fields = allowed          # ← tanpa validasi Pydantic sama sekali
```

Tablet bisa mendorong `{"status": "sembuh_kok"}` atau menandai *disagreed* tanpa catatan klinis,
dan itu masuk ke DB. Karena tidak ada CHECK constraint di level DB, tidak ada jaring pengaman.
Ini bukan hipotetis: tablet adalah klien utama dan jalur offline-nya *selalu* lewat sini.

**Perbaikan:** pakai skema yang sama di kedua jalur — `DiagnosisStatusUpdate.model_validate(item.payload)`,
lalu angkat aturan "disagree wajib note" ke satu fungsi service yang dipanggil keduanya.
`ValidationError` sudah ditangkap di `app/api/routes/sync.py:37` sehingga otomatis jadi `conflict`.

### C-2 · Deteksi konflik buta pada jendela 1 detik
**File:** [`app/services/sync.py:29-36`](../app/services/sync.py) · **Sumber:** baru

```python
return aware.replace(microsecond=0)   # kedua sisi dipotong ke detik
```

Perbandingan `row.updated_at > item.base_updated_at` jadi hanya sensitif pada beda ≥ 1 detik.
Kalau dokter B menyimpan di server pada `10:00:00.100` dan dokter A mendorong perubahan offline
dengan `base_updated_at = 10:00:00.000`, keduanya jadi `10:00:00` → konflik **tidak terdeteksi**
→ perubahan A **menimpa diam-diam** perubahan B. Persis yang dijanjikan tidak akan terjadi di
docstring modul ("medical data is never auto-overwritten").

Truncation itu ditambahkan untuk menghindari crash beda-presisi SQLite vs Postgres, dan itu masalah
nyata — tapi obatnya salah sasaran. **Perbaikan:** normalisasi timezone saja (bagian `if dt.tzinfo is None`),
buang `.replace(microsecond=0)`. Kalau presisi SQLite bikin test flaky, perbaiki di lapisan test, bukan
dengan melemahkan logika produksi.

### C-3 · Hapus pasien = 500, atau kehilangan riwayat medis
**File:** [`app/api/routes/patients.py:85-90`](../app/api/routes/patients.py) · **Sumber:** sebagian (soft delete sudah dicatat)

Yang **belum** tercatat: tidak ada FK `ondelete` di mana pun (dikonfirmasi — nol kecocokan
`ondelete` di `app/` dan `alembic/`). Jadi `DELETE /patients/{id}` untuk pasien yang punya diagnosis
memicu `IntegrityError` yang tidak tertangkap → **HTTP 500**, bukan pesan yang berguna.
Endpoint ini praktis rusak untuk pasien mana pun yang pernah discreening.

Soft delete (`deleted_at`) memperbaiki dua hal sekaligus: kepatuhan rekam medis *dan* bug 500 ini.
Ingat tambahkan `WHERE deleted_at IS NULL` ke `list_patients`, `_get_owned_patient`, dan `/sync/pull`.

### C-4 · Satu op sync rusak menjatuhkan seluruh batch
**File:** [`app/api/routes/sync.py:29-42`](../app/api/routes/sync.py) · **Sumber:** baru

Loop menangkap `ValidationError` dan `ValueError` — tapi **bukan** `IntegrityError`.
Pemicunya nyata: jalur create menerima id yang ditentukan klien (`app/services/sync.py:150-152`);
UUID yang bentrok, atau `code` pasien yang melanggar `UNIQUE(tenant_id, code)`, melempar
`IntegrityError` yang lolos → 500, dan hasil untuk item-item sebelumnya **hilang** meski sudah
di-commit. Klien tidak tahu mana yang berhasil.

**Perbaikan:** tangkap `IntegrityError` juga → `db.rollback()` → catat sebagai `conflict`. Idempotency
sudah membuat retry aman; yang kurang cuma jaringnya.

---

## Tinggi

### H-1 · RBAC ditulis tapi tidak pernah dipasang
**File:** [`app/api/deps.py:60`](../app/api/deps.py) · **Sumber:** baru

`require_roles()` nol pemakaian di seluruh `app/` dan `tests/`. Setiap endpoint terbuka untuk
ketiga peran. `super_admin` bisa membuat pasien; `doctor` bisa melakukan apa pun yang `admin_rs`
bisa. Model peran di § 1 SUPER_PROMPT belum ditegakkan di kode mana pun.

Ini juga **belum ada testnya** — `test_tenant_isolation.py` menguji isolasi *tenant* (dan menguji
dengan baik), bukan otorisasi *peran*.

### H-2 · Tidak ada endpoint untuk 2 dari 3 peran
**Sumber:** baru

Tidak ada CRUD user, tidak ada CRUD hospital, tidak ada endpoint dashboard/analitik. Satu-satunya
cara membuat user adalah `scripts/seed.py`. Web admin untuk `admin_rs` dan `super_admin` belum
punya backend untuk dipanggil. Ini bukan bug — ini **scope yang belum dikerjakan**, dan perlu
masuk rencana secara eksplisit karena SUPER_PROMPT menjanjikan ketiga peran.

### H-3 · Alur klinis inti tidak tersambung
**File:** [`lib/features/camera/presentation/camera_screen.dart`](../../tbscreenai-mobile-frontend/lib/features/camera/presentation/camera_screen.dart) · **Sumber:** sebagian

`grep` untuk `infer|multipart|FormData` di seluruh `lib/` → **nol hasil di kode non-mock.**
Frontend tidak pernah memanggil `POST /diagnoses/infer`. `CameraScreen` menampilkan teks
`'Live camera preview'` di atas `Container` hitam dan mengirim `attachMockImage('captured_xray.png')`.

Jadi endpoint inferensi backend — mock sekalipun — **belum pernah dipanggil klien mana pun.**
Kontraknya belum tervalidasi meski kedua sisi mengklaim mengimplementasikannya.

### H-4 · Refresh token gagal → aplikasi menggantung tanpa sesi
**File:** [`lib/data/http/api_client.dart:66-98`](../../tbscreenai-mobile-frontend/lib/data/http/api_client.dart) · **Sumber:** baru

```dart
} on DioException {
  await tokens.clear();   // sesi berakhir — tapi tidak ada yang diberi tahu
}
```

`AuthProvider.isLoggedIn` tetap `true`, jadi `redirect` di go_router tidak mengarahkan ke `/login`.
Dokter tinggal di layar yang setiap requestnya gagal 401 tanpa penjelasan. **Perbaikan:** beri
`ApiClient` callback `onSessionExpired` yang memanggil `AuthProvider.logout()`.

Di file yang sama: N request 401 berbarengan memicu N refresh paralel. Rotasi refresh token
membuat semua kecuali satu jadi tidak valid → logout beruntun acak. Bungkus dengan
single-flight (satu `Future` refresh yang di-share).

---

## Sedang

| ID | Temuan | Lokasi |
|---|---|---|
| M-1 | `pending.firstWhere(...)` melempar `StateError` kalau server balas op id tak dikenal → seluruh push crash | `sync_engine.dart:171` |
| M-2 | 4 dari 8 repo tetap mock walau `USE_HTTP=true` (Dashboard, Diagnosis, Validation, Dataset) — separuh aplikasi menampilkan data palsu terhadap backend live | `lib/app/app.dart:88-92` |
| M-3 | `limit`/`offset` tanpa `Query(ge=0)`; `offset=-1` → error SQL Postgres | `patients.py:31-32`, `diagnoses.py:57-58` |
| M-4 | `/sync/pull` tanpa `since` mengembalikan snapshot penuh tanpa batas — sync pertama RS besar = satu respons raksasa | `sync.py:47-72` |
| M-5 | Tidak ada indeks pada `patients.updated_at` / `diagnoses.updated_at`, padahal itu justru kolom filter `/sync/pull` (dikonfirmasi dari migrasi) | `alembic/versions/1342b192e0cd` |
| M-6 | `UUID(payload["sub"])` tanpa guard → token cacat jadi 500, bukan 401 | `deps.py:44` |
| M-7 | `X-Tenant-Id` dari `super_admin` tidak diverifikasi merujuk hospital yang ada → pelanggaran FK saat write | `deps.py:83-88` |
| M-8 | `AuthProvider._displayName` di-hardcode `'Dr. Maya Rizki'`; sesi yang dipulihkan tidak membaca nama dari `SettingsStore` | `auth_provider.dart:12` |
| M-9 | `_monthsId` bulan Indonesia buatan tangan; pakai `intl` `DateFormat` locale `id_ID` | `offline_sync_repository.dart:12-15` |
| M-10 | `unawaited()` custom menelan semua error diam-diam dan membayangi versi `dart:async` | `offline_patient_repository.dart:51-53` |

---

# 3. Security Review

Yang sudah **benar** dan layak disebut: pesan login seragam (anti-enumerasi), tenant scope dari JWT
bukan dari body, 404 lintas-tenant alih-alih 403, pemisahan tipe token access/refresh, bcrypt,
`is_active` dicek di setiap request. Ini di atas rata-rata untuk proyek fase ini.

| ID | Isu | Keparahan | Sumber |
|---|---|---|---|
| **S-1** | Tidak ada rate limit di `/auth/login` — brute force tanpa hambatan | Tinggi | tercatat |
| **S-2** | Logout hanya di sisi klien. Refresh token yang dicuri tetap sah **7 hari**; tidak ada `jti`, tidak ada denylist. Tidak ada cara mencabut sesi tablet yang hilang. | Tinggi | **baru** |
| **S-3** | `/diagnoses/infer` percaya `content_type` kiriman klien; tanpa cek magic-byte, **tanpa batas ukuran** → DoS via upload besar | Tinggi | **baru** |
| **S-4** | JWT tersimpan plaintext di SQLite tablet (`AppSettings`), begitu juga cache pasien | Tinggi | **baru** |
| **S-5** | RBAC tidak ditegakkan (lihat H-1) | Tinggi | **baru** |
| **S-6** | Tidak ada audit trail. Regulasi rekam medis menuntut jejak siapa-melihat/mengubah-apa-kapan. `SyncLog` mencatat op sync, tapi bukan akses baca atau perubahan via REST. | Sedang | **baru** |
| **S-7** | `CORS_ORIGINS=["*"]` | Sedang | tercatat |
| **S-8** | Default `jwt_secret_key` yang lemah di `config.py:19` tanpa penjagaan startup untuk prod | Sedang | **baru** |
| **S-9** | Tanpa kebijakan password; `scripts/seed.py` menanam kredensial yang bisa ditebak | Sedang | **baru** |

**S-3 dan S-8 paling murah diperbaiki.** S-8 itu satu validator:

```python
# app/core/config.py
@model_validator(mode="after")
def _guard_prod_secret(self):
    if self.env == "production" and len(self.jwt_secret_key) < 32:
        raise ValueError("JWT_SECRET_KEY must be ≥32 chars in production")
    return self
```

**Kekhawatiran privasi terpisah:** `run_mock_inference` memakai `random.random()` untuk memutuskan
positif/negatif. Aman selama jelas-jelas mock — tapi pastikan ada penjagaan yang mencegahnya jalan
di produksi. Putusan TB acak yang tampil sebagai hasil klinis adalah mode kegagalan terburuk yang
bisa dipunyai sistem ini. Sarankan: `is_mock: true` sudah ada di respons (bagus) — tambahkan
`assert settings.env != "production"` di `run_mock_inference`.

---

# 4. Testing Strategy

## Kondisi sekarang — terverifikasi, bukan klaim

| Suite | Hasil | Waktu | Catatan |
|---|---|---|---|
| Backend `pytest` | **50 passed** | 89 s | 1 warning deprecation (`HTTP_422_UNPROCESSABLE_ENTITY`) |
| Frontend `flutter test` | **30 passed** | 15 s | — |

Yang diuji sudah diuji dengan baik. `test_tenant_isolation.py` khususnya menguji *hal yang benar*
(404 bukan 403, kode pasien sama di RS berbeda). Ini bukan test teater.

## T-1 · Jalur JSONB Postgres tidak pernah diuji — celah terpenting

`tests/conftest.py:33-36` mengompilasi `JSONB` → `JSON` untuk SQLite. Artinya `Patient.history` dan
`Diagnosis.findings` — dua kolom yang membawa **data klinis** — dijalankan lewat tipe kolom yang
berbeda di test dibanding produksi. Migrasi itu sendiri juga tidak pernah dijalankan di test.

`testcontainers` menyelesaikan ini: satu file test integrasi yang menyalakan Postgres asli,
menjalankan `alembic upgrade head`, dan mengeksekusi satu siklus penuh push/pull. Tidak perlu
memindahkan 50 test yang ada — SQLite tetap tepat untuk suite cepat.

## T-2 · Suite backend 12× lebih lambat dari seharusnya

89 s untuk 50 test = ~1,8 s/test. Penyebabnya bcrypt: fixture `users` menghasilkan 5 hash
(12 rounds default) **per test**, plus login untuk setiap fixture header. Turunkan cost di conftest:

```python
# tests/conftest.py — hanya untuk test
import bcrypt
_orig = bcrypt.gensalt
bcrypt.gensalt = lambda rounds=4, **kw: _orig(rounds)
```

Harusnya turun ke di bawah 10 s. Ini penting bukan demi kenyamanan — suite lambat itu suite yang
tidak dijalankan.

## Test yang kurang, berurut prioritas

| Prioritas | Test | Menutup |
|---|---|---|
| **P0** | Sync menolak `status` diagnosis tidak sah | C-1 |
| **P0** | Deteksi konflik pada edit sub-detik | C-2 |
| **P0** | `DELETE /patients/{id}` untuk pasien yang punya diagnosis | C-3 |
| **P0** | Batch sync dengan satu item rusak → item lain tetap dilaporkan | C-4 |
| **P1** | Matriks otorisasi peran (setiap peran × setiap endpoint) | H-1, S-5 |
| **P1** | Integrasi Postgres via testcontainers | T-1 |
| **P1** | `/infer` menolak non-citra ber-`content_type` palsu; menolak file besar | S-3 |
| **P2** | E2E `login → infer → save → validate → sync` (setelah H-3 diperbaiki) | H-3 |
| **P2** | Interceptor refresh: 401 berbarengan → satu refresh; refresh gagal → logout | H-4 |
| **P2** | Rate limit login (setelah S-1) | S-1 |
| **P3** | Widget test `CameraScreen` dengan `camera` yang di-mock | pra-rilis |

**Tambahan tooling:** `mocktail` + `http_mock_adapter` di frontend (sekarang lapisan HTTP hampir
tidak ter-cover); `pytest-cov` dengan gerbang minimum — set baseline pada angka yang terukur
sekarang, jangan angka aspiratif.

---

# 5. Tech Debt Register

Diberi skor **dampak × urgensi**, bukan nilai kerapian kode.

## Bayar sekarang

| Utang | Biaya kalau ditunda |
|---|---|
| **Backend tanpa git** | Satu `rm -rf` yang salah = seluruh backend hilang. Nol test, nol riwayat, nol pemulihan. |
| **Tidak ada CI** | Kedua suite hijau *hari ini*; tidak ada yang menjaga besok. |
| **Tidak ada linter/type-checker** (backend) | Type hint sudah ditulis rapi di seluruh kode — tapi tidak pernah diverifikasi, jadi nilainya cuma dokumentasi. |
| **C-1 s.d. C-4** | Empat jalur berbeda menuju data medis rusak atau hilang. |

## Bayar sebelum produksi

| Utang | Catatan |
|---|---|
| RBAC tidak ditegakkan (H-1) | `require_roles` sudah ditulis — tinggal dipasang. Perbaikan termurah dengan dampak tertinggi. |
| Endpoint admin_rs / super_admin (H-2) | Scope belum dikerjakan; perlu estimasi, bukan sekadar tambal. |
| Alur kamera + inferensi (H-3) | Ini *produk*-nya. Semua yang lain adalah pendukung. |
| Penyimpanan citra | Sekarang inferensi berjalan di atas citra yang langsung dibuang. |
| Soft delete + audit trail (C-3, S-6) | Kepatuhan, bukan preferensi. |
| Rate limit + pencabutan token (S-1, S-2) | |

## Bayar saat nyaman

- 4 repo mock di balik `USE_HTTP` (M-2) — sudah didokumentasikan dan disengaja, tapi jangan sampai lolos ke rilis
- `pubspec.yaml` masih bernama `myapp` dengan deskripsi "UI scaffold"
- `flutter_lints` masih default; pertimbangkan aturan yang lebih ketat
- Warning deprecation `HTTP_422_UNPROCESSABLE_ENTITY`
- Progress `downloadModel()` palsu (`offline_sync_repository.dart:88-98`) — sudah jujur ditandai di komentar
- M-8 s.d. M-10

## Utang yang sengaja diambil dan **benar** untuk diambil

Ini layak dicatat supaya tidak ada yang "memperbaikinya" belakangan tanpa konteks:

- **Inferensi mock** — batas scope yang eksplisit, ditandai `is_mock: true` di respons. Benar.
- **Repository mock sebagai default** — demo jalan tanpa backend. Keputusan bagus.
- **SQLite untuk test** — cepat dan hermetik; masalahnya hanya cakupan (T-1), bukan pendekatannya.
- **Peran sebagai string, bukan enum** — enum akan lebih rapi, tapi ini bukan yang menahan proyek.

---

# 6. Roadmap

## Sprint 1 — Stabilkan (~2–3 hari)
1. `git init` + commit backend ← **pertama, sebelum apa pun**
2. Perbaiki C-1, C-2, C-3, C-4 + test P0 untuk masing-masing
3. Tambah `ruff` + `mypy` + `pytest-cov`; perbaiki temuannya
4. GitHub Actions: `ruff → mypy → pytest` (backend), `analyze → test` (frontend)
5. Turunkan bcrypt rounds di test (T-2)

## Sprint 2 — Tutup lubang keamanan (~3–4 hari)
6. Pasang `require_roles` di semua route + matriks test peran (H-1)
7. `slowapi` di `/auth/login` (S-1)
8. Validasi magic-byte + batas ukuran di `/infer` (S-3)
9. `flutter_secure_storage` untuk token (S-4)
10. CORS eksplisit + penjagaan secret produksi (S-7, S-8)
11. Perbaiki propagasi sesi kedaluwarsa + single-flight refresh (H-4)

## Sprint 3 — Sambungkan produk (~1 minggu)
12. Plugin `camera` → capture asli → `POST /diagnoses/infer` (H-3)
13. MinIO + simpan citra + isi `Diagnosis.image_path`
14. Soft delete + audit trail (C-3, S-6)
15. E2E test lintas alur klinis penuh

## Sprint 4 — Kelengkapan peran (perlu estimasi)
16. CRUD user + hospital untuk `admin_rs` / `super_admin` (H-2)
17. Endpoint dashboard/analitik → cabut mock Dashboard & Validation (M-2)
18. `structlog` + request id + Sentry

## Sprint 5 — Integrasi model AI (bergantung tim AI)
19. `onnxruntime` + `Pillow` menggantikan `run_mock_inference`
20. Ganti strategi test: mock model, bukan mock hasil (sesuai SUPER_PROMPT § 6)
21. Metrik Prometheus untuk latensi inferensi
22. Load test sync (500+ item, sesuai checklist SUPER_PROMPT)

---

## Lampiran — Perintah verifikasi

```bash
cd /c/Users/devel/tbscreenai-backend && ./.venv/Scripts/python.exe -m pytest -q
```

```bash
cd /c/Users/devel/tbscreenai-mobile-frontend && /c/Users/devel/flutter/bin/flutter test
```

# TBScreen.AI — Breakdown Proyek & Rencana Lanjutan

**Tanggal:** 2026-08-14
**Cakupan:** `tbscreenai-backend` (FastAPI) + `tbscreenai-mobile-frontend` (Flutter)
**Basis verifikasi:** semua angka di dokumen ini dijalankan, bukan dikutip dari dokumen lama.

> Catatan lokasi: `tasks/` ditaruh di repo backend karena `docs/` di sini sudah
> jadi rumah dokumentasi lintas-repo (`ARCHITECTURE_REVIEW.md` mencakup keduanya).

---

## 0. Ringkasan Status

| Dimensi | Backend | Frontend |
|---|---|---|
| Commit | 5 (branch `fix/critical-data-integrity`, **4 commit di depan `master`, belum merge**) | 12 (branch `main`) |
| Test | **134 passed** (4,8 s) | **40 passed** (~14 s) |
| Coverage | **97,85 %** (floor 95 % ditegakkan) | tidak diukur |
| Lint / type | `ruff` clean · `mypy` clean (35 file) | `flutter analyze` di CI |
| CI | GitHub Actions: quality + migrations (Postgres asli) | GitHub Actions: analyze + codegen drift + test |
| Endpoint | 15 API + `/health` | 9 layar + shell |

**Kesimpulan satu kalimat:** infrastrukturnya matang, produknya belum —
rantai *capture → infer → simpan citra* masih memakai gambar placeholder 1×1 piksel
dan model AI palsu tanpa penjagaan produksi.

---

## 1. Breakdown — Backend (`tbscreenai-backend`)

### 1.1 Tools & dependency

| Kategori | Paket | Versi |
|---|---|---|
| Web framework | `fastapi`, `uvicorn[standard]`, `python-multipart` | 0.116 / 0.35 / 0.0.20 |
| Database | `sqlalchemy`, `alembic`, `psycopg[binary]` | 2.0.41 / 1.16 / 3.2.9 |
| Config & validasi | `pydantic`, `pydantic-settings`, `email-validator` | 2.11 / 2.10 / 2.2 |
| Auth | `PyJWT`, `bcrypt` | 2.10 / 4.3 |
| Rate limit | `slowapi` (in-memory, per-proses) | 0.1.10 |
| Test | `pytest`, `httpx`, `pytest-cov` | 8.4 / 0.28 / 7.1 |
| Quality gate | `ruff`, `mypy` | 0.16 / 2.3 |
| Infra dev | Docker Compose → `postgres:16` | — |

Ditolak eksplisit di ADR-001: Kafka, Celery, microservices, GraphQL, Riverpod, `freezed`.

### 1.2 Skema database — 6 tabel, 2 migrasi

Migrasi: `1342b192e0cd` (skema awal) → `a1f4c7d92b30` (version counter + soft delete).

| Tabel | Tenant-bound | Peran |
|---|---|---|
| `hospitals` | — (root tenant) | Rumah sakit; `code` unik global |
| `users` | ya (`NULL` untuk super_admin) | bcrypt password, role string, `is_active` dicek tiap request |
| `patients` | ya | `version` (optimistic lock), `deleted_at` (soft delete), `history` JSONB |
| `diagnoses` | ya (denormalisasi sengaja) | `findings` JSONB 5 metrik, `status`, `doctor_note`, `image_path` **selalu NULL** |
| `sync_logs` | ya | Ledger idempotency — `UNIQUE (tenant_id, client_op_id)` |
| `model_versions` | tidak (katalog global) | Versi model AI + changelog |

Keputusan desain yang penting:
1. **Isolasi tenant di lapisan API, bukan RLS** — `tenant_id` selalu dari JWT, tidak pernah dari body. Data RS lain dijawab **404, bukan 403**.
2. **`version` sebagai sinyal konflik, bukan `updated_at`** — drift menyimpan `DateTime` sebagai unix detik, jadi dua edit di detik yang sama tidak terbedakan.
3. **Soft delete + partial unique index** — `WHERE deleted_at IS NULL`, supaya kode pasien bisa dipakai ulang dan FK diagnosis tidak pecah.
4. **`UNIQUE (tenant_id, client_op_id)`** — jantung sinkronisasi offline.

### 1.3 Endpoint (15 + health)

```
POST   /api/v1/auth/login                   (rate-limited 10/menit/IP)
POST   /api/v1/auth/refresh
GET    /api/v1/patients                     READ_ROLES
POST   /api/v1/patients                     PATIENT_WRITE_ROLES
GET    /api/v1/patients/{id}                READ_ROLES
PUT    /api/v1/patients/{id}                PATIENT_WRITE_ROLES
DELETE /api/v1/patients/{id}                PATIENT_DELETE_ROLES  (soft delete)
POST   /api/v1/diagnoses/infer              CLINICAL_ROLES  ← mock model
GET    /api/v1/diagnoses                    READ_ROLES
POST   /api/v1/diagnoses                    CLINICAL_ROLES
GET    /api/v1/diagnoses/{id}               READ_ROLES
PATCH  /api/v1/diagnoses/{id}/status        CLINICAL_ROLES  (disagreed wajib note)
POST   /api/v1/sync/push                    CLINICAL_ROLES  (idempoten)
GET    /api/v1/sync/pull                    CLINICAL_ROLES  (snapshot / delta ?since=)
GET    /api/v1/sync/model-version           READ_ROLES
GET    /health
```

---

## 2. Breakdown — Frontend (`tbscreenai-mobile-frontend`)

### 2.1 Tools & dependency

| Kategori | Paket | Versi |
|---|---|---|
| Framework | Flutter (CI pin **3.44.7**) / Dart SDK | ^3.11.4 |
| Routing | `go_router` (ShellRoute untuk NavRail) | 15.1.2 |
| State | `provider` | 6.1.2 |
| HTTP | `dio` (+ interceptor refresh-on-401) | 5.7 |
| Local DB | `drift` + `drift_flutter` | 2.34 |
| Codegen | `build_runner`, `drift_dev` | 2.15 / 2.34 |
| Lint | `flutter_lints` (masih default) | 6.0 |
| Web runtime | `sqlite3.wasm` + `drift_worker.js` di `web/` | — |

**Tidak ada:** `camera`, `image_picker`, `flutter_secure_storage` — meski ketiganya
direkomendasikan Gelombang 1 di ADR-001. `CLAUDE.md` masih menyebut `camera: ^0.10.6`
di daftar tech stack; itu tidak ada di `pubspec.yaml`.

### 2.2 Arsitektur berlapis

```
domain/models          ← immutable, nol import Flutter
domain/repositories    ← 7 interface; layar & provider hanya bergantung ke sini
  ├── data/mock/       ← default, SynchronousFuture (tanpa loading flash)
  ├── data/http/       ← dio → FastAPI
  └── data/offline/    ← drift + SyncEngine (offline-first)
```

Toggle: `--dart-define=USE_HTTP=true` (+ `API_BASE_URL`).

### 2.3 Layar (9) & status implementasi data

| Route | Layar | NavRail | Sumber data saat `USE_HTTP=true` |
|---|---|---|---|
| `/login` | Login | — | **HTTP** |
| `/dashboard` | Dashboard | 0 | **MOCK** |
| `/patients` | Patients | 1 | **Offline (drift + sync)** |
| `/diagnosis` | Diagnosis | 2 | **HTTP** (`/diagnoses/infer`) |
| `/result` | Result | 3 | dari provider |
| `/validation` | Validation | 4 | **MOCK** |
| `/dataset` | Dataset | 5 | **MOCK** |
| `/sync` | Sync Center | 6 | **Offline + HTTP** |
| `/account` | Account | 7 | dari provider |
| `/camera` | Camera | — | **PALSU** — tidak ada plugin kamera |

3 dari 8 item NavRail menampilkan data karangan meski backend hidup.

### 2.4 Skema lokal (drift) — 4 tabel

`LocalPatients`, `LocalDiagnoses` (keduanya punya `hasConflict`), `SyncQueue`
(`clientOpId`, `baseUpdatedAt`, `status`, `detail`), `AppSettings` (key-value).

`SyncEngine` menjamin dua hal, kembar dengan backend: setiap mutasi lokal masuk
antrean dulu dengan `client_op_id`; op dengan base version basi dijawab `conflict`
dan **tidak pernah** di-merge otomatis.

---

## 3. Tahapan yang Sudah Dilewati

| Fase | Isi | Bukti commit |
|---|---|---|
| **1 — UI scaffold** | Tema, routing, NavRail, mock inline | `e9d5963` |
| **2 — Kelengkapan layar** | Sync Center, dark-mode Result, polish tablet, dokumen setup | `538fa54`, `38dd049` |
| **3 — Repository pattern** | Domain model, 7 interface, impl mock + http (dio) | `85c57a4` |
| **4 — Offline-first** | drift, SyncEngine, offline repo, settings store, runtime web | `85c57a4`, `8249dee` |
| **5 — Quality & CI** | pytest/flutter test, ruff, mypy, coverage floor, GH Actions | `d70f431`, `7898934` |
| **6 — Pengerasan pasca-review** | 4 cacat integritas data, RBAC, rate limit, validasi upload, inference tersambung | `235626d`, `68f4ecf`, `f3ea12c` |

Yang **belum** pernah dimasuki: Fase penyimpanan citra (MinIO), Fase kelengkapan
peran (`admin_rs` / `super_admin`), Fase model AI asli.

---

## 4. Hasil Menjalankan (2026-08-14, terverifikasi)

### Backend
```
pytest -q                → 134 passed in 4.80s
pytest -q --cov          → 97.85 %  (floor 95 % terpenuhi)
ruff check .             → All checks passed!
mypy                     → Success: no issues found in 35 source files
docker compose up -d     → tbscreenai-db  Up (healthy)
alembic upgrade head     → ok
python -m scripts.seed   → seed complete
uvicorn :8000            → {"status":"ok"}
```

### Smoke test API end-to-end

| Skenario | Hasil | Harapan |
|---|---|---|
| Buat pasien (doctor) | 201 + row | ✔ |
| `POST /diagnoses/infer` PNG asli | `is_mock:true`, confidence 88 | ✔ |
| `POST /diagnoses/infer` teks berlabel `image/png` | **415** | ✔ magic-byte menang atas header |
| Simpan diagnosis | 201 | ✔ |
| `disagreed` **tanpa** catatan | **422** | ✔ |
| `disagreed` **dengan** catatan | `version` naik 1→2 | ✔ optimistic lock jalan |
| RS Bethesda baca pasien RS Sardjito | **404** | ✔ bukan 403 |
| `admin_rs` panggil `/diagnoses/infer` | **403** | ✔ RBAC ditegakkan |
| `super_admin` tanpa `X-Tenant-Id` | **400** | ✔ |
| Sync push `client_op_id` sama 2× | `applied` → `skipped` | ✔ idempoten |
| Rate limit login | `401×5` → `429×8` | ✔ |

### Frontend
```
flutter test             → 40 passed
flutter run -d web-server --web-port=5051 --dart-define=USE_HTTP=true
                         → app boot, drift web runtime termuat
```

**Probe integrasi langsung** (repository Http asli milik Flutter → backend hidup):
```
LOGIN      -> Dr. Maya Rizki / doctor / doctor@sardjito.co.id
PATIENTS   -> 4 rows
INFER      -> positive=false confidence=76 model=TBScreen v2.1.0 time=2.9s
FINDINGS   -> consolidation=4.4 cavity=0.0 effusion=1.2 fibrotic=0.6 calcification=0.1
MODEL SYNC -> latest=v1.3.1 size=47.2 MB released=10 Juni 2025
```

Rantai `login → list pasien → inferensi multipart → cek versi model` **berjalan nyata**
lewat kode produksi frontend, bukan stub. (Tangkapan layar UI tidak diambil: panel
browser tidak sedang ditampilkan sehingga halaman tidak meng-*composite* frame.)

---

## 5. Temuan Baru dari Sesi Ini

| ID | Temuan | Dampak |
|---|---|---|
| **B-1** | `run_mock_inference` **tidak punya penjagaan produksi**. `random.random()` menentukan positif/negatif TB. `ARCHITECTURE_REVIEW.md` sudah menyarankan `assert settings.env != "production"`; belum dipasang. | **Kritis** — vonis TB acak bisa tayang sebagai hasil klinis |
| **B-2** | Dua skema penamaan versi model hidup berdampingan: `/infer` mengembalikan `TBScreen v2.1.0`, `/sync/model-version` mengembalikan `v1.3.1`. Layar Result dan Sync Center akan menampilkan versi berbeda untuk model yang sama. | Sedang — membingungkan audit klinis |
| **B-3** | Kerja backend menumpuk di branch `fix/critical-data-integrity`; `master` hanya berisi commit awal. CI hanya terpicu di `master`/`main` + PR. | Sedang — 4 commit pengerasan belum jadi baseline |
| **B-4** | `XrayImage.placeholder()` PNG 1×1 dipakai sebagai input inferensi. Alur tersambung, **input-nya belum**. | Tinggi — produk intinya masih kosong |
| **B-5** | JWT (access + refresh, umur 7 hari) tersimpan TEXT polos di `AppSettings` drift. | Tinggi — tablet RS itu perangkat bersama |
| **B-6** | `pubspec.yaml` masih `name: myapp`, deskripsi "TBScreen tablet UI scaffold". | Rendah — tapi ini nama paket yang ikut ke APK |
| **B-7** | **Build release tidak punya `android.permission.INTERNET`.** Izin itu hanya ada di `android/app/src/debug/` dan `profile/`; manifest `main/` tidak punya. Terverifikasi lewat manifest hasil merge. | **Kritis** — APK release tidak bisa login, sync, maupun inferensi sama sekali |
| **B-8** | `applicationId` = `com.example.myapp`, `android:label` = `myapp`, dan build release **ditandatangani dengan kunci debug**. | Tinggi — tidak bisa dirilis ke Play Store maupun MDM rumah sakit |
| **B-9** | Layar Result menampilkan Clinical Data karangan (Comorbidity `None`, Smoking `No`, Sputum `Negative`, Culture `Negative`) dan Gender `Female` untuk pasien tanpa nama — padahal formulir dikosongkan. Layar ini punya tombol Save / Export PDF / Print. | **Tinggi** — laporan diagnosis yang bisa dicetak berisi fakta klinis yang tidak pernah dimasukkan siapa pun |
| **B-10** | Dashboard menyapa "Welcome back, **Dr. Anderson**" padahal yang login `Dr. Maya Rizki`, dan menampilkan 1.248 pasien / 3.567 diagnosis padahal basis data berisi 4 pasien. | Sedang — nama dan angka mock tampil di layar pertama setelah login |

Utang lama yang **masih terbuka**: `image_path` selalu NULL (citra dibuang setelah
inferensi), tidak ada tabel audit akses, tidak ada denylist refresh token,
tidak ada endpoint untuk `admin_rs` / `super_admin`, rate limit per-proses.

---

## 6. Roadmap

### Fase A — Kebersihan & keamanan segera (~1 hari)
Task 1–4. Blokir vonis acak di produksi, merge branch, satukan versi model, pindahkan token.

### Checkpoint A
- [ ] `pytest` + `flutter test` hijau
- [ ] `master` = kode terkini, CI hijau di `master`

### Fase B — Sambungkan input klinis nyata (~1 minggu)
Task 5–8. Plugin kamera → capture asli → MinIO → `image_path` terisi.

### Checkpoint B
- [ ] Satu X-ray asli bisa ditelusuri: capture → infer → simpan → tampil ulang

### Fase C — Kelengkapan peran & data nyata (perlu estimasi)
Task 9–12. Endpoint `admin_rs`/`super_admin`, cabut mock Dashboard/Validation/Dataset, audit trail.

### Checkpoint C
- [ ] Nol repository mock saat `USE_HTTP=true`
- [ ] Setiap baca/tulis rekam medis punya jejak audit

### Fase D — Model AI asli (bergantung tim AI)
Task 13–15. `onnxruntime` + `Pillow`, metrik latensi, load test sync.

---

## 7. Risiko

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Mock inference lolos ke produksi | **Fatal** — vonis TB acak ke pasien | Task 1: penjagaan `env` + test regresi |
| Citra X-ray tidak pernah disimpan | Tinggi — tidak bisa re-inferensi saat model naik versi, tidak bisa audit klinis | Task 7 (MinIO) sebelum pilot RS |
| Tablet hilang = sesi dokter 7 hari | Tinggi | Task 4 (Keystore) + Task 12 (denylist) |
| Dashboard mock dikira data nyata saat demo RS | Sedang — risiko reputasi | Task 10, atau beri label "DEMO" sampai selesai |
| MinIO single-node tembus ~5 TB | Rendah (nanti) | Evaluasi mode terdistribusi / S3 |

---

## 8. Pertanyaan Terbuka (butuh jawaban non-teknis)

1. **Retensi citra X-ray** — berapa lama disimpan? Ini pertanyaan legal, dan menentukan desain storage.
2. **Siapa pemilik model AI** dan kapan artefak `.onnx` tersedia? Fase D tidak bisa diestimasi tanpa ini.
3. **Apakah `admin_rs` dan `super_admin` masuk MVP**, atau boleh ditunda ke rilis kedua? Ini menentukan apakah Fase C blocking.
4. **Target deployment**: on-premise per RS, atau terpusat? Menentukan apakah rate limit in-memory cukup.

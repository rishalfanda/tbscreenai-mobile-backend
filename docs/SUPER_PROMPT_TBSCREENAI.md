# SUPER PROMPT — TBScreenAI Full Architecture

> Prompt komprehensif buat AI coding agent (Claude Code, Codex, Cursor, dsb.)
> untuk memahami dan melanjutkan proyek TBScreenAI tanpa bolak-balik tanya.

---

## 📋 PROJECT IDENTITY

Aplikasi AI screening TB (Tuberculosis) dari chest X-ray. Multi-tenant (per RS).
3 peran: **doctor** (tablet Flutter), **admin_rs** (web), **super_admin** (web).

| Role | Client | Tenant-bound |
|------|--------|-------------|
| `doctor` | Flutter tablet app | ya (1 RS) |
| `admin_rs` | Web admin RS | ya (1 RS) |
| `super_admin` | Web platform admin | tidak (wajib header `X-Tenant-Id`) |

---

## 🗂️ REPOSITORI

| Repo | Path | Stack |
|------|------|-------|
| Frontend | `tbscreenai-mobile-frontend` | Flutter 3.x, Dart ^3.11.4, go_router, provider, dio, drift |
| Backend | `tbscreenai-backend` | FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL, PyJWT, bcrypt |

---

## 1️⃣ FRONTEND ARCHITECTURE (Flutter)

### Layer Structure

```
lib/
├── main.dart                        # Entry point
├── app/
│   ├── app.dart                     # MaterialApp.router + MultiProvider
│   └── router/app_router.dart       # go_router config (ShellRoute)
├── core/
│   ├── config/app_config.dart       # AppConfig (USE_HTTP, API_BASE_URL dari --dart-define)
│   ├── config/scroll_behavior.dart
│   ├── theme/app_theme.dart         # AppColors, spacing, component themes
│   └── utils/uuid.dart
├── domain/
│   ├── models/                      # Immutable data classes
│   │   ├── patient.dart
│   │   ├── diagnosis_outcome.dart
│   │   ├── user_profile.dart
│   │   ├── dashboard_metric.dart
│   │   ├── dataset.dart
│   │   ├── trend_data_point.dart
│   │   ├── activity_item.dart
│   │   ├── validation_case.dart
│   │   ├── model_version_info.dart
│   │   ├── system_status.dart
│   │   ├── sync_summary.dart
│   │   └── models.dart             # Barrel
│   └── repositories/               # Interfaces — screens tahu ini saja
│       ├── auth_repository.dart
│       ├── dashboard_repository.dart
│       ├── patient_repository.dart
│       ├── diagnosis_repository.dart
│       ├── dataset_repository.dart
│       ├── validation_repository.dart
│       ├── sync_repository.dart
│       └── repositories.dart       # Barrel
├── data/
│   ├── mock/                        # Default (SynchronousFuture = instant, no flash)
│   │   ├── mock_auth_repository.dart
│   │   ├── mock_dashboard_repository.dart
│   │   ├── mock_patient_repository.dart
│   │   ├── mock_diagnosis_repository.dart
│   │   ├── mock_dataset_repository.dart
│   │   ├── mock_validation_repository.dart
│   │   ├── mock_sync_repository.dart
│   │   ├── mock_seed_data.dart
│   │   └── mock_repositories.dart
│   ├── http/                        # Real API impl (dio)
│   │   ├── api_client.dart          # Dio instance, interceptors, refresh-on-401
│   │   ├── http_auth_repository.dart
│   │   ├── http_patient_repository.dart
│   │   ├── http_sync_repository.dart
│   │   ├── patient_json.dart
│   │   └── http_repositories.dart
│   ├── offline/                     # Local-first (drift)
│   │   ├── offline_patient_repository.dart
│   │   └── offline_sync_repository.dart
│   ├── local/                       # Drift DB
│   │   ├── app_database.dart        # AppDatabase class
│   │   ├── app_database.g.dart      # Generated
│   │   ├── tables.dart              # Drift table definitions
│   │   ├── mappers.dart
│   │   └── settings_store.dart
│   └── sync/
│       └── sync_engine.dart         # Push queue + conflict resolution
├── state/                           # Provider (ChangeNotifier)
│   ├── auth_provider.dart
│   ├── dashboard_provider.dart
│   └── diagnosis_provider.dart
└── features/
    ├── auth/presentation/           → login_screen.dart
    ├── dashboard/presentation/      → dashboard_screen.dart
    ├── patients/presentation/       → patients_screen.dart
    ├── diagnosis/presentation/      → diagnosis_screen.dart
    ├── camera/presentation/         → camera_screen.dart
    ├── result/presentation/         → result_screen.dart
    ├── validation/presentation/     → validation_screen.dart
    ├── dataset/presentation/        → dataset_screen.dart
    ├── sync/presentation/           → sync_center_screen.dart
    ├── account/presentation/        → account_screen.dart
    └── shared/presentation/
        ├── app_shell.dart           # NavRail + ShellRoute
        └── widgets/                 # AppCard, StatusBadge, SectionHeader, EmptyState, XrayPreview
```

### Routing (go_router)

| Route | Screen | NavRail | Index |
|-------|--------|---------|-------|
| `/login` | LoginScreen | No | - |
| `/dashboard` | DashboardScreen | Yes | 0 |
| `/patients` | PatientsScreen | Yes | 1 |
| `/diagnosis` | DiagnosisScreen | Yes | 2 |
| `/result` | ResultScreen | Yes | 3 |
| `/validation` | ValidationScreen | Yes | 4 |
| `/dataset` | DatasetScreen | Yes | 5 |
| `/sync` | SyncCenterScreen | Yes | 6 |
| `/account` | AccountScreen | Yes | 7 |
| `/camera` | CameraScreen | No (full screen) | - |

Primary flow: `Login → Dashboard → Diagnosis → (Camera) → Result`

NavRail icons: `space_dashboard / people_alt / biotech / analytics / verified_user (badge) / table_chart / cloud_sync / person`

### Theme

- Tablet-first, landscape ≥ 1024px (LayoutBuilder)
- Material 3 (`useMaterial3: true`)
- NavRail: 84px, navy→navyDark gradient
- Max content width: 1600px
- Touch targets: min 48×48px
- Source of truth: `app_theme.dart` — never hardcode hex in widgets

### Color System

| Token | Hex |
|-------|-----|
| `primary` | `#4FC3F7` |
| `primaryDark` | `#0288D1` |
| `secondary` | `#1E3A5F` |
| `success` | `#22C55E` |
| `warning` | `#F59E0B` |
| `error` | `#EF4444` |
| `background` | `#F9FAFB` |
| `surface` | `#FFFFFF` |
| `textPrimary` | `#111827` |
| `textSecondary` | `#6B7280` |

### Data Layer Toggle

Gunakan `--dart-define`:
```bash
flutter run -d chrome --dart-define=USE_HTTP=true --dart-define=API_BASE_URL=http://localhost:8000/api/v1
```
Tanpa flag → mock (default). `AppConfig` di `lib/core/config/app_config.dart`.

### Domain Models → Backend Schema Mapping

| Model | Fields |
|-------|--------|
| `Patient` | id, code, name, age, gender, status, confidence?, lastVisit?, history[], tenantId |
| `DiagnosisOutcome` | isPositive, confidence, processingTimeMs, modelVersion, findings (Findings) |
| `Findings` | consolidation, cavity, effusion, fibrotic, calcification |
| `UserProfile` | id, email, fullName, role, hospitalId?, hospitalName? |
| `ModelVersionInfo` | version, fileSize, releaseDate, changelog[], isLatest |
| `SyncSummary` | lastSync, patientsCount, pendingOps, lastSyncStatus |

### Test Files (flutter test — 30 test)

| File | Cakupan |
|------|---------|
| `test/widget_test.dart` | Smoke test boot → redirect login |
| `test/diagnosis_form_test.dart` | Dropdown: Sunlight Yes/No, Model Type, Model Version 1-3 |
| `test/result_screen_test.dart` | Empty state, outcome render |
| `test/repository_contract_test.dart` | 6 repository kontrak data |
| `test/sync_center_test.dart` | State machine ModelUpdateCard, consent dialog |
| `test/sync_engine_test.dart` | Push idempotency, conflict, retry, offline queue |

### Forbidden Packages (fase ini)
~~supabase, firebase_core, sqflite, hive, shared_preferences~~
Dio dan drift DIIZINKAN. Jangan tambah storage lain.

---

## 2️⃣ BACKEND ARCHITECTURE (FastAPI)

### Stack
- Python 3.11+, FastAPI 0.116+, uvicorn
- SQLAlchemy 2.x ORM + Alembic migrations
- PostgreSQL (psycopg 3.x) — test pakai SQLite in-memory
- Auth: PyJWT (HS256) + bcrypt
- Pydantic v2 + pydantic-settings

### Structure
```
app/
├── main.py                 # FastAPI app, CORS, /health
├── core/
│   ├── config.py           # Settings (pydantic-settings), @lru_cache
│   ├── database.py         # engine, SessionLocal, get_db
│   └── security.py         # hash_password, verify_password, create_access_token,
│                           # create_refresh_token, decode_token
├── api/
│   ├── deps.py             # get_current_user, require_roles, CurrentTenant, DbSession
│   ├── router.py           # APIRouter aggregator
│   └── routes/
│       ├── auth.py         # /auth/login, /auth/refresh
│       ├── patients.py     # /patients CRUD + search
│       ├── diagnoses.py    # /diagnoses CRUD + /infer, /{id}/status
│       └── sync.py         # /sync/push, /sync/pull, /sync/model-version
├── models/
│   ├── base.py             # Base, UuidPkMixin, TimestampMixin, TenantMixin
│   ├── hospital.py         # Hospital (tenant)
│   ├── user.py             # User (role: doctor/admin_rs/super_admin)
│   ├── patient.py          # Patient (tenant-scoped)
│   ├── diagnosis.py        # Diagnosis (tenant-scoped, fk→patient)
│   ├── model_version.py    # ModelVersion (global)
│   └── sync_log.py         # SyncLog (idempotency ledger)
├── schemas/
│   ├── auth.py             # LoginRequest, RefreshRequest, TokenPair, UserOut
│   ├── patient.py          # PatientCreate, PatientUpdate, PatientOut
│   ├── diagnosis.py        # DiagnosisCreate, DiagnosisStatusUpdate, InferenceResult, Findings, DiagnosisOut
│   ├── model_version.py    # ModelVersionOut
│   └── sync.py             # SyncPushItem, SyncPushRequest/Response, SyncPullResponse
└── services/
    ├── inference.py        # run_mock_inference — ganti pas model AI real siap
    └── sync.py             # apply_push_item — idempotent push + conflict detection
```

### Dev Setup (Windows)
```bash
# 1. Postgres
docker compose up -d

# 2. Python env
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env

# 3. Schema + seed
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m scripts.seed

# 4. Run
.venv\Scripts\python -m uvicorn app.main:app --port 8000 --reload
```

Swagger UI: `http://127.0.0.1:8000/docs`

### Test (50 test, semua hijau)
```bash
.venv\Scripts\python -m pytest -q
```
- SQLite in-memory per-test (Postgres JSONB → JSON compatibility mode)
- 4 file: `test_auth.py`, `test_patients_diagnoses.py`, `test_sync.py`, `test_tenant_isolation.py`

### Security

| Aspek | Detail |
|-------|--------|
| Auth | Bearer JWT (access + refresh token) |
| Password | bcrypt (`hash_password` / `verify_password`) |
| Login | Same message for wrong email OR password (anti-enumeration) |
| Tenant isolation | `CurrentTenant` dependency — handler tidak pernah terima `tenant_id` dari body |
| Role guard | `require_roles("doctor")` — decorator pattern |
| disabled account | `is_active == false` → 403 |
| Token type | Access token ≠ Refresh token (diperiksa di `refresh` endpoint) |
| Signed token | JWT signature verifikasi; token palsu → 401 |

### ⚠️ Belum terimplementasi (gap)
- **Rate limiting** — `POST /auth/login` rawan brute force
- **Soft delete** — `DELETE /patients/{id}` hard delete, data medis hilang
- **Image storage** — X-ray dikirim ke `/infer` tapi tidak disimpan
- **CORS explicit origins** — masih `["*"]` (safe karena Bearer auth, tapi ganti sebelum prod)

---

## 3️⃣ DATABASE SCHEMA

### Entity Relationship

```
Hospital (tenant)
  └── User (role: doctor/admin_rs/super_admin)
       └── tenant_id → Hospital.id (nullable for super_admin)
  └── Patient (tenant-scoped)
       └── tenant_id → Hospital.id
       └── UniqueConstraint(tenant_id, code)
  └── Diagnosis (tenant-scoped)
       └── tenant_id → Hospital.id
       └── patient_id → Patient.id
       └── created_by → User.id
  └── SyncLog (idempotency ledger)
       └── tenant_id → Hospital.id
       └── UniqueConstraint(tenant_id, client_op_id)

ModelVersion (global — tdk tenant-scoped)
```

### Models Detail

#### Hospital (tenant root)
```python
id: UUID (PK)
name: str(255)
code: str(50) — unique
address: str(500) — nullable
created_at, updated_at: datetime (via TimestampMixin)
```

#### User
```python
id: UUID (PK)
email: str(255) — unique
hashed_password: str(255)
full_name: str(255)
role: str(20) — "doctor" | "admin_rs" | "super_admin"
tenant_id: UUID — FK→Hospitals, nullable (super_admin ga punya RS)
is_active: bool — default True
created_at, updated_at: datetime
```

#### Patient
```python
id: UUID (PK)
tenant_id: UUID — FK→Hospitals (wajib)
code: str(20) — unique per tenant (TB000001...)
name: str(255)
age: int
gender: str(10) — "Male" | "Female"
status: str(20) — default "Normal"
confidence: int — nullable
last_visit: date — nullable
history: JSONB — list of strings
created_at, updated_at: datetime
__table_args__: UniqueConstraint(tenant_id, code)
```

#### Diagnosis
```python
id: UUID (PK)
tenant_id: UUID — FK→Hospitals (wajib)
patient_id: UUID — FK→Patients (indexed)
created_by: UUID — FK→Users, nullable
is_positive: bool
confidence: int (0-100)
model_version: str(50)
processing_time_ms: int — nullable
findings: JSONB — {"consolidation": float, "cavity": float, "effusion": float, "fibrotic": float, "calcification": float}
status: str(20) — default "pending" | "agreed" | "disagreed"
doctor_note: text — nullable
image_path: str(500) — nullable
diagnosed_at: datetime — server_default=now(), timezone-aware
created_at, updated_at: datetime
```

#### ModelVersion
```python
id: UUID (PK)
version: str(20) — unique
file_size_mb: float
release_date: date
changelog: JSONB — list of strings
is_latest: bool — default True
created_at: datetime
```

#### SyncLog (idempotency ledger)
```python
id: UUID (PK)
tenant_id: UUID — FK→Hospitals (wajib)
client_op_id: UUID — dari device
device_id: str(100) — nullable
entity_type: str(20) — "patient" | "diagnosis"
entity_id: UUID — nullable
operation: str(20) — "create" | "update"
status: str(20) — "applied" | "skipped" | "conflict"
payload: JSONB
created_at: datetime
__table_args__: UniqueConstraint(tenant_id, client_op_id)
```

---

## 4️⃣ API SCHEMA

Prefix: `/api/v1`

### Auth

| Method | Path | Auth | Request | Response |
|--------|------|------|---------|----------|
| POST | `/auth/login` | No | `{email, password}` | `{access_token, refresh_token, user}` |
| POST | `/auth/refresh` | No | `{refresh_token}` | `{access_token, refresh_token, user}` |

**POST /auth/login**
```json
// Request
{ "email": "dr.andi@rs.pkuskab.go.id", "password": "..." }
// Response 200
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "user": {
    "id": "uuid", "email": "...", "full_name": "dr. Andi",
    "role": "doctor", "tenant_id": "uuid"
  }
}
// Response 401 — same msg both cases
{ "detail": "Incorrect email or password" }
```

### Patients

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET | `/patients` | Bearer | `?q=&limit=100&offset=0` |
| POST | `/patients` | Bearer | Create (tenant dari JWT) |
| GET | `/patients/{id}` | Bearer | 404 jika bukan tenant-nya |
| PUT | `/patients/{id}` | Bearer | Partial update |
| DELETE | `/patients/{id}` | Bearer | ⚠️ HARD DELETE (perlu soft delete) |

**POST /patients**
```json
// Request
{
  "code": "TB000001", "name": "Siti Rahmawati", "age": 34,
  "gender": "Female", "status": "Normal", "confidence": null,
  "last_visit": null, "history": []
}
// Response 201 — seperti di atas + "id", "tenant_id", "created_at", "updated_at"
// Response 409 — { "detail": "Patient code TB000001 already exists in this hospital" }
```

### Diagnoses

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/diagnoses/infer` | Bearer | Upload image → mock result (simulasi AI) |
| GET | `/diagnoses` | Bearer | `?patient_id=&status_filter=&limit=&offset=` |
| POST | `/diagnoses` | Bearer | Save diagnosis result |
| GET | `/diagnoses/{id}` | Bearer | Detail |
| PATCH | `/diagnoses/{id}/status` | Bearer | Validation (pending→agreed/disagreed) |

**POST /diagnoses/infer**
```json
// Request: multipart/form-data, field "image" (png/jpeg/dicom)
// Response 200
{
  "is_positive": true,
  "confidence": 87,
  "processing_time_ms": 2850,
  "model_version": "TBScreen v2.1.0",
  "findings": {
    "consolidation": 28.4, "cavity": 3.2,
    "effusion": 6.1, "fibrotic": 0.8, "calcification": 1.2
  }
}
```

**PATCH /diagnoses/{id}/status**
```json
// Request
{ "status": "agreed" }
// atau
{ "status": "disagreed", "doctor_note": "False positive — scar tissue from old infection" }
// Response 422 jika disagree tanpa doctor_note
```

### Sync (Offline-first)

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/sync/push` | Bearer | Batch push offline changes |
| GET | `/sync/pull` | Bearer | `?since=ISO8601` (delta atau full snapshot) |
| GET | `/sync/model-version` | Bearer | Latest model version info |

**POST /sync/push**
```json
// Request
{
  "device_id": "tablet-rs-01",
  "items": [
    {
      "client_op_id": "uuid-dari-tablet",
      "entity_type": "patient",
      "operation": "create",
      "payload": { "code": "TB000002", "name": "...", ... }
    },
    {
      "client_op_id": "uuid-lain",
      "entity_type": "diagnosis",
      "operation": "update",
      "entity_id": "uuid",
      "base_updated_at": "2025-06-10T09:00:00Z",
      "payload": { "status": "agreed" }
    }
  ]
}
// Response 200
{
  "results": [
    { "client_op_id": "uuid", "status": "applied", "entity_id": "uuid" },
    { "client_op_id": "uuid", "status": "skipped", "entity_id": "uuid", "detail": "Duplicate op" },
    { "client_op_id": "uuid", "status": "conflict", "entity_id": "uuid",
      "detail": "Server version is newer — manual review required" }
  ]
}
```

**GET /sync/pull**
```json
// ?since=2025-06-10T09:00:00Z
// Response 200
{
  "server_time": "2025-06-11T10:00:00Z",
  "patients": [...],
  "diagnoses": [...],
  "latest_model": {
    "version": "v2.1.0", "file_size_mb": 47.2,
    "release_date": "2025-06-10", "changelog": [...],
    "is_latest": true
  }
}
```

### Meta

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| GET | `/health` | No | `{"status": "ok"}` |

### Tenant Isolation Rules

1. **doctor / admin_rs**: query scope = `tenant_id` dari JWT. Header `X-Tenant-Id` diabaikan.
2. **super_admin**: tidak punya tenant. WAJIB kirim `X-Tenant-Id` header.
3. **Cross-tenant lookup**: 404 (bukan 403) — jangan leak keberadaan data RS lain.
4. **Pasien dengan kode sama** boleh ada di RS berbeda (composite unique `(tenant_id, code)`).

---

## 5️⃣ TEST SCHEMA

### Frontend (flutter test — target: ≥30 test)

```bash
flutter test
```

| File | Level | Cakupan |
|------|-------|---------|
| `widget_test.dart` | Smoke | Boot app → redirect login |
| `diagnosis_form_test.dart` | Widget | Dropdown options (Sunlight Yes/No, Model Type, Model Version 1-3) |
| `result_screen_test.dart` | Widget | Empty state, outcome render |
| `repository_contract_test.dart` | Unit | 6 repo: data well-formed, id unik, status valid, progres download monoton→1.0 |
| `sync_center_test.dart` | Widget | State machine ModelUpdateCard, consent dialog logic |
| `sync_engine_test.dart` | Unit | Drift + Dio stub: push bawa client_op_id, retry=skipped, conflict mark, retry after fail, refresh cache safe |

### Backend (pytest — target: >60 test)

```bash
.venv\Scripts\python -m pytest -q
```

| File | Level | Cakupan (existing 50) |
|------|-------|----------------------|
| `test_auth.py` | Unit/API | Login benar/salah, email tak dikenal, akun nonaktif, refresh token, access≠refresh, fake signature, semua endpoint wajib auth |
| `test_tenant_isolation.py` | Integration | RS lain 404 bukan 403, kode pasien sama di RS beda, diagnosis lintas-tenant ditolak, super_admin wajib X-Tenant-Id, doctor ignore header |
| `test_sync.py` | Integration | Idempotency (op id applied→skipped, retry 5×, unique per tenant), conflict (basi→conflict, unknown entity→conflict bukan crash), pull (server_time, delta since) |
| `test_patients_diagnoses.py` | Integration | CRUD pasien, 409 duplicate code, search, validation, diagnosis CRUD, disagree wajib note, inference mock (is_mock=true, reject non-image) |

### Gap Test (perlu ditambah)

| Test | Reason | Priority |
|------|--------|----------|
| Integration E2E login→diagnosis→result→sync | Manual verified, risk regresi | Medium |
| Camera screen | Butuh physical device | High (pre-launch) |
| Rate limiting auth | Brute force protection | Medium |
| Soft delete audit | Data medis compliance | Medium |
| Web build drift+sqlite3.wasm | After drift integration | Medium |
| Concurrent sync conflict scenario | Real-world race condition | Low |

---

## 6️⃣ AI INFERENCE INTEGRATION (future — oleh tim AI)

Saat model AI real siap:

1. Terima model artifact (ONNX / TensorRT / PyTorch)
2. Ganti `app/services/inference.py`:
   - Load model di startup (singleton)
   - Preprocessing pipeline (resize, normalize, format → tensor)
   - Run inference → parse output ke `InferenceResult`
   - (Optional) GPU acceleration kalau di server
3. Simpan X-ray image ke object storage (MinIO/S3) sebelum inference
4. Update `Diagnosis.image_path` dengan reference ke stored image
5. Update test mocking strategy: mock model, bukan mock result

---

## 7️⃣ KODE KONVENSI

### Flutter
- Satu screen = satu file di `features/{name}/presentation/`
- Sub-widget >100 baris → extract ke file terpisah
- `const` constructor prefered
- `Theme.of(context)` — never hardcode hex di widget
- Hover guard (web): selalu `if (!mounted) return;` sebelum `setState`
- Variable/constant files untuk semua string UI — no hardcoded strings

### Backend (Python)
- Type hints ALL params and returns
- Pydantic schemas untuk request/response (validasi di layer API)
- Route handler tipis — logika bisnis di `services/`
- Tenant query wajib lewat `CurrentTenant` — jangan dari body/query
- Magic constants → module-level constants atau enum

### Test
- `test_` prefix untuk file dan fungsi
- Backend: SQLite in-memory per-test (conftest fixture)
- Frontend: `TestWidgetsFlutterBinding.ensureInitialized()` di setUp
- `addTearDown(tester.view.reset)` setelah setup viewport

---

## 8️⃣ DEPLOYMENT (future)

### Staging (minimal)
```
Docker Compose:
  app:    FastAPI + uvicorn
  db:     PostgreSQL 16
  proxy:  nginx (CORS explicit origins, TLS termination)
  object: MinIO (X-ray images)
```

### Production
- FastAPI behind Gunicorn + uvicorn workers
- PostgreSQL RDS / Cloud SQL
- MinIO → S3-compatible (AWS S3 / Backblaze / DO Spaces)
- Redis (optional: rate limiting, cache)
- CI/CD: GitHub Actions (lint → test → build → deploy)

### Checklist Pre-Production
- [ ] CORS explicit origins (bukan `"*"`)
- [ ] JWT secret key minimal 32 chars random
- [ ] Rate limiting login endpoint
- [ ] Soft delete + audit trail data medis
- [ ] HSTS + HTTPS only
- [ ] Image storage pipeline (MinIO/S3)
- [ ] Logging + monitoring (Sentry?)
- [ ] Load test sync (500+ push item)

---

## 9️⃣ FILE REFERENCE

### Frontend files kunci
| Path | Purpose |
|------|---------|
| `lib/app/app.dart` | MultiProvider setup |
| `lib/app/router/app_router.dart` | All routes + ShellRoute |
| `lib/core/config/app_config.dart` | USE_HTTP toggle |
| `lib/core/theme/app_theme.dart` | Color + typography source of truth |
| `lib/data/http/api_client.dart` | Dio + auth interceptor + refresh-on-401 |
| `lib/data/sync/sync_engine.dart` | Offline push queue + conflict strategy |
| `lib/data/local/tables.dart` | Drift table definitions |
| `lib/data/local/app_database.dart` | Drift DB class |
| `lib/features/shared/presentation/app_shell.dart` | NavRail layout |
| `lib/domain/repositories/*.dart` | Interface contracts |

### Backend files kunci
| Path | Purpose |
|------|---------|
| `app/core/config.py` | All env config |
| `app/core/security.py` | JWT + bcrypt |
| `app/api/deps.py` | Auth + tenant guard |
| `app/models/base.py` | Mixins (UuidPk, Timestamp, Tenant) |
| `app/services/inference.py` | Mock AI — ganti pas real model siap |
| `app/services/sync.py` | Idempotent push + conflict detection |
| `app/api/routes/sync.py` | Sync endpoints |
| `alembic/versions/1342b192e0cd_initial_schema.py` | Initial migration |

---

## END OF SUPER PROMPT

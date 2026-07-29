# TBScreenAI Backend

Shared FastAPI backend for TBScreenAI. Serves three roles from one API:

| Role | Client | Tenant-bound |
|------|--------|--------------|
| `doctor` | Flutter tablet app | ya (1 RS) |
| `admin_rs` | Web admin RS | ya (1 RS) |
| `super_admin` | Web platform admin | tidak — wajib kirim header `X-Tenant-Id` untuk akses data |

**Tenant isolation** ditegakkan di dependency layer (`app/api/deps.py`):
route handler tidak pernah menerima `tenant_id` dari client; selalu diambil
dari JWT. Uji isolasi ada di smoke test & (nanti) pytest.

**AI inference = MOCK** (`app/services/inference.py`) — kontraknya sudah final
(mirror `DiagnosisOutcome` di Flutter), implementasi model menyusul.

## Dev setup (Windows)

```powershell
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

Swagger UI: http://127.0.0.1:8000/docs · Kontrak beku: `docs/openapi.json`

Kredensial dev (JANGAN dipakai di deployment): lihat `scripts/seed.py`.

## Struktur

```
app/
├── main.py          FastAPI app, CORS, /health
├── core/            config (pydantic-settings), database, security (JWT+bcrypt)
├── api/
│   ├── deps.py      get_current_user, require_roles, CurrentTenant  ← kunci isolasi
│   └── routes/      auth, patients, diagnoses (+/infer mock), sync (push/pull)
├── models/          SQLAlchemy: Hospital, User, Patient, Diagnosis,
│                    ModelVersion, SyncLog (idempotency ledger)
├── schemas/         Pydantic request/response
└── services/        inference (mock), sync (idempotent push + conflict detect)
```

## Sync semantics (offline-first)

- Push item membawa `client_op_id` (UUID dari device) → retry tidak pernah
  double-apply (unique constraint `(tenant_id, client_op_id)`).
- Update membawa `base_updated_at`; jika server lebih baru → status `conflict`,
  data TIDAK ditimpa — review manual dokter (FASE 4).
- Pull: snapshot / delta via `?since=`, plus versi model AI terbaru.

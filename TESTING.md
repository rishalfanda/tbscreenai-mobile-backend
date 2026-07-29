# Testing — TBScreenAI

Ringkasan cakupan uji per 2026-07-24 (FASE 5). Semua test hijau.

## Backend (pytest) — 50 test

```bash
cd C:/Users/devel/tbscreenai-backend
.venv/Scripts/python -m pytest -q
```

Berjalan di SQLite in-memory per-test (tidak menyentuh Postgres dev, tidak butuh
Docker). Type Postgres-spesifik (`JSONB`) di-compile ke `JSON` untuk SQLite.

| File | Cakupan |
|---|---|
| `test_tenant_isolation.py` | RS lain tidak bisa read/list/update/delete pasien (404, bukan 403); kode pasien sama boleh di RS berbeda; diagnosis lintas-tenant ditolak; pull hanya data sendiri; super_admin wajib `X-Tenant-Id`; **doctor tidak bisa ganti tenant lewat header** (scope dari JWT) |
| `test_sync.py` | Idempotency (op id sama → applied lalu skipped; retry 5× tetap 1 record; op id per-tenant; batch campur baru+lama); konflik (base_updated_at basi → conflict, data tidak ditimpa; entity tak dikenal → conflict bukan crash; base terkini → applied); pull (server_time, delta `since`) |
| `test_auth.py` | Login benar/salah; email tak dikenal = pesan sama dengan password salah (anti-enumeration); akun nonaktif ditolak; refresh token; access token ≠ refresh token; tanda tangan dipalsukan ditolak; semua endpoint wajib auth |
| `test_patients_diagnoses.py` | CRUD pasien; kode dobel 409; search nama+kode; validasi field (gender/age); **tenant_id di body diabaikan**; diagnosis CRUD; disagree wajib catatan; filter; inference mock (is_mock=true, tolak content-type non-gambar) |

**Bug asli yang ditemukan pytest:** perbandingan `datetime` naive vs aware di
deteksi konflik sync (`app/services/sync.py`) → crash saat base_updated_at dari
klien tz-aware sedangkan row tz-naive. Diperbaiki dengan helper `_as_utc()`.

## Frontend (flutter test) — 30 test

```bash
cd C:/Users/devel/tbscreenai-mobile-frontend
C:/Users/devel/flutter/bin/flutter.bat test
```

| File | Cakupan |
|---|---|
| `sync_engine_test.dart` | (drift in-memory + Dio stub) push bawa client_op_id; retry "skipped" = sukses bukan dobel; konflik menandai row & tidak menimpa data lokal; gagal jaringan tetap bisa retry; refresh cache tidak buang row antrean |
| `sync_center_test.dart` | State machine ModelUpdateCard (idle→checking→updateAvailable); consent dialog (Lanjutkan disabled sampai dicentang, tidak bisa dismiss barrier, Batal menutup, consent→selection) |
| `repository_contract_test.dart` | Kontrak 6 repository (data well-formed, id unik, status valid, progres download monoton→1.0, upload count naik) |
| `result_screen_test.dart` | Empty state saat belum ada hasil (bukan vonis palsu); render outcome asli |
| `diagnosis_form_test.dart` | Opsi dropdown Sunlight Yes/No, Model Type, Model Version 1-3 |
| `widget_test.dart` | Smoke test boot app → redirect login |

## Test yang DI-SKIP / tidak dijalankan (dinyatakan eksplisit)

Sesuai aturan proyek — dilaporkan, bukan diam-diam dilewati:

1. **Integration login→diagnosis→result→sync end-to-end** BELUM ditulis sebagai
   test otomatis. Alur ini SUDAH diverifikasi manual di emulator (lihat log
   2026-07-24), tapi belum jadi `integration_test/`. Alasan: butuh device/emulator
   hidup + backend, tidak jalan di `flutter test` biasa.
2. **CameraScreen** tidak diuji — butuh kamera fisik; di emulator/CI tidak ada
   sensor. Capture X-ray lewat `camera` package hanya bisa diverifikasi di
   perangkat nyata.
3. **Web build (drift + sqlite3.wasm)** belum diuji ulang setelah drift masuk.
   Target utama Android sudah terbukti; mode web perlu `sqlite3.wasm` + worker
   di `web/`.
4. **Alur refresh-on-401 nyata** (token access kedaluwarsa lalu auto-refresh)
   diuji unit di sync_engine, tapi belum end-to-end dengan token yang benar-benar
   expired dari server.

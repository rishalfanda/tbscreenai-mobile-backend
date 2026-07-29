"""Dev seed: hospitals, users (one per role), and model versions.

Run:  python -m scripts.seed
Idempotent — safe to re-run.

DEV CREDENTIALS (change before any real deployment):
  doctor@sardjito.co.id   / password123  (doctor,   RS Sardjito)
  admin@sardjito.co.id    / password123  (admin_rs, RS Sardjito)
  doctor@bethesda.co.id   / password123  (doctor,   RS Bethesda)
  super@tbscreen.co.id    / password123  (super_admin, no tenant)
"""

from datetime import date

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import Hospital, ModelVersion, User

HOSPITALS = [
    {"name": "RS Sardjito", "code": "RSS"},
    {"name": "RS Bethesda", "code": "RSB"},
]

MODEL_VERSIONS = [
    {
        "version": "v1.2.0",
        "file_size_mb": 44.8,
        "release_date": date(2025, 3, 2),
        "changelog": ["Rilis awal model produksi"],
        "is_latest": False,
    },
    {
        # Mirrors the frontend mock so the Sync Center flow lines up end-to-end.
        "version": "v1.3.1",
        "file_size_mb": 47.2,
        "release_date": date(2025, 6, 10),
        "changelog": [
            "Peningkatan akurasi deteksi TB aktif sebesar 3.2%",
            "Perbaikan false positive pada pasien pediatrik",
            "Optimasi kecepatan inferensi pada perangkat low-end",
        ],
        "is_latest": True,
    },
]


def main() -> None:
    db = SessionLocal()
    try:
        hospitals: dict[str, Hospital] = {}
        for spec in HOSPITALS:
            hospital = db.scalar(select(Hospital).where(Hospital.code == spec["code"]))
            if hospital is None:
                hospital = Hospital(**spec)
                db.add(hospital)
                db.flush()
                print(f"created hospital {spec['code']}")
            hospitals[spec["code"]] = hospital

        users = [
            ("doctor@sardjito.co.id", "Dr. Maya Rizki", "doctor", hospitals["RSS"].id),
            ("admin@sardjito.co.id", "Admin Sardjito", "admin_rs", hospitals["RSS"].id),
            ("doctor@bethesda.co.id", "Dr. Bima Pratama", "doctor", hospitals["RSB"].id),
            ("super@tbscreen.co.id", "Platform Admin", "super_admin", None),
        ]
        for email, full_name, role, tenant_id in users:
            if db.scalar(select(User).where(User.email == email)) is None:
                db.add(
                    User(
                        email=email,
                        full_name=full_name,
                        role=role,
                        tenant_id=tenant_id,
                        hashed_password=hash_password("password123"),
                    )
                )
                print(f"created user {email} ({role})")

        # Named apart from the hospital loop above: same word previously stood
        # for two different dict shapes, which is what the type checker caught.
        for model_spec in MODEL_VERSIONS:
            if db.scalar(
                select(ModelVersion).where(
                    ModelVersion.version == model_spec["version"]
                )
            ) is None:
                db.add(ModelVersion(**model_spec))
                print(f"created model version {model_spec['version']}")

        db.commit()
        print("seed complete")
    finally:
        db.close()


if __name__ == "__main__":
    main()

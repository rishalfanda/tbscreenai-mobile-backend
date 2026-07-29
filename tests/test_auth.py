"""Authentication, token handling, and endpoint guards."""

import pytest


class TestLogin:
    def test_valid_credentials_return_token_pair_and_profile(self, client, users):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "doctor.a@rs.co.id", "password": "secret123"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["access_token"] and body["refresh_token"]
        assert body["user"]["role"] == "doctor"
        assert body["user"]["tenant_id"] is not None
        # The hash must never leave the server.
        assert "hashed_password" not in body["user"]

    def test_wrong_password_is_rejected(self, client, users):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "doctor.a@rs.co.id", "password": "salah"},
        )

        assert response.status_code == 401

    def test_unknown_email_gives_the_same_message_as_wrong_password(
        self, client, users
    ):
        """Different messages would let an attacker enumerate valid accounts."""
        unknown = client.post(
            "/api/v1/auth/login",
            json={"email": "tidakada@rs.co.id", "password": "secret123"},
        )
        wrong = client.post(
            "/api/v1/auth/login",
            json={"email": "doctor.a@rs.co.id", "password": "salah"},
        )

        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["detail"] == wrong.json()["detail"]

    def test_disabled_account_cannot_log_in(self, client, users):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "disabled@rs.co.id", "password": "secret123"},
        )

        assert response.status_code == 403

    def test_super_admin_has_no_tenant(self, client, users):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "super@tbscreen.co.id", "password": "secret123"},
        )

        assert response.json()["user"]["tenant_id"] is None


class TestRefresh:
    def test_refresh_token_issues_a_new_pair(self, client, users):
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "doctor.a@rs.co.id", "password": "secret123"},
        ).json()

        response = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]}
        )

        assert response.status_code == 200
        assert response.json()["access_token"]

    def test_access_token_is_not_accepted_as_refresh_token(self, client, users):
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "doctor.a@rs.co.id", "password": "secret123"},
        ).json()

        response = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": login["access_token"]}
        )

        assert response.status_code == 401

    def test_garbage_token_is_rejected(self, client, users):
        response = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "bukan.token.valid"}
        )

        assert response.status_code == 401


class TestGuards:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/api/v1/patients"),
            ("post", "/api/v1/patients"),
            ("get", "/api/v1/diagnoses"),
            ("get", "/api/v1/sync/pull"),
            ("post", "/api/v1/sync/push"),
        ],
    )
    def test_endpoints_require_authentication(self, client, users, method, path):
        # GET takes no body; POST does. Either way, no Authorization header.
        response = (
            client.get(path) if method == "get" else client.post(path, json={})
        )

        assert response.status_code == 401

    def test_refresh_token_cannot_be_used_as_bearer(self, client, users):
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "doctor.a@rs.co.id", "password": "secret123"},
        ).json()

        response = client.get(
            "/api/v1/patients",
            headers={"Authorization": f"Bearer {login['refresh_token']}"},
        )

        assert response.status_code == 401

    def test_tampered_signature_is_rejected(self, client, users):
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "doctor.a@rs.co.id", "password": "secret123"},
        ).json()
        tampered = login["access_token"][:-4] + "AAAA"

        response = client.get(
            "/api/v1/patients", headers={"Authorization": f"Bearer {tampered}"}
        )

        assert response.status_code == 401


def test_health_needs_no_auth(client):
    assert client.get("/health").status_code == 200

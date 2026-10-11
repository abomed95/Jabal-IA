import uuid

from sqlalchemy import text

from tests.conftest import PASSWORD, auth_header, login, new_device, register


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_register_normalizes_phone_and_rejects_duplicates(client):
    r = register(client, phone="77 69 48 12")
    assert r.status_code == 201
    assert r.json()["phone"] == "+25377694812"
    again = register(client, phone="+253 77 69 48 12")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "account_exists"


def test_register_validation(client):
    assert register(client, role="student").status_code == 422             # niveau manquant
    assert register(client, role="guardian", level="premiere_s").status_code == 422
    assert register(client, phone="12").status_code == 422
    assert register(client, password="court").status_code == 422
    assert register(client, role="admin").status_code == 422               # pas d'admin par l'API


def test_login_and_me(client):
    register(client)
    r = login(client)
    assert r.status_code == 200
    tokens = r.json()
    assert tokens["user"]["role"] == "guardian"
    me = client.get("/v1/me", headers=auth_header(tokens))
    assert me.status_code == 200
    assert me.json()["full_name"] == "Parent Test"
    assert me.json()["children"] == []


def test_me_requires_a_valid_token(client):
    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/me", headers={"Authorization": "Bearer nimportequoi"}).status_code == 401


def test_wrong_password_and_unknown_phone_look_the_same(client):
    register(client)
    a = login(client, password="mauvais-mot-de-passe")
    b = login(client, phone="77 99 99 99")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_login_is_throttled_after_five_failures(client):
    register(client)
    for _ in range(5):
        assert login(client, password="mauvais-mot-de-passe").status_code == 401
    blocked = login(client, password=PASSWORD)  # même le bon mot de passe est refusé pendant le blocage
    assert blocked.status_code == 429


def test_device_limit_is_two_and_relogin_on_same_device_is_free(client):
    register(client)
    d1, d2, d3 = new_device(), new_device(), new_device()
    assert login(client, device_id=d1).status_code == 200
    assert login(client, device_id=d2).status_code == 200
    assert login(client, device_id=d1).status_code == 200     # même téléphone : pas un 3e appareil
    third = login(client, device_id=d3)
    assert third.status_code == 409
    assert third.json()["error"]["code"] == "device_limit_reached"


def test_revoking_a_device_frees_a_slot(client):
    register(client)
    t1 = login(client).json()
    t2 = login(client).json()
    devices = client.get("/v1/devices", headers=auth_header(t1)).json()
    assert len(devices) == 2 and sum(d["current"] for d in devices) == 1
    other = next(d for d in devices if not d["current"])
    assert client.delete(f"/v1/devices/{other['id']}", headers=auth_header(t1)).status_code == 204
    assert login(client).status_code == 200                    # un nouvel appareil passe maintenant
    # le jeton de l'appareil révoqué ne marche plus tout de suite
    assert client.get("/v1/me", headers=auth_header(t2)).status_code == 401


def test_cannot_revoke_someone_elses_device(client):
    register(client, name="Parent Un", phone="77 00 00 01")
    register(client, name="Parent Deux", phone="77 00 00 02")
    a = login(client, phone="77 00 00 01").json()
    b = login(client, phone="77 00 00 02").json()
    b_dev = client.get("/v1/devices", headers=auth_header(b)).json()[0]["id"]
    assert client.delete(f"/v1/devices/{b_dev}", headers=auth_header(a)).status_code == 404
    assert client.get("/v1/me", headers=auth_header(b)).status_code == 200


def test_refresh_rotates_the_token(client):
    register(client)
    t = login(client).json()
    r = client.post("/v1/auth/refresh", json={"refresh_token": t["refresh_token"]})
    assert r.status_code == 200
    new = r.json()
    assert new["refresh_token"] != t["refresh_token"]
    again = client.post("/v1/auth/refresh", json={"refresh_token": t["refresh_token"]})  # ancien jeton
    assert again.status_code == 401
    assert client.get("/v1/me", headers=auth_header(new)).status_code == 200


def test_logout_revokes_the_device(client):
    register(client)
    t = login(client).json()
    assert client.post("/v1/auth/logout", headers=auth_header(t)).status_code == 204
    assert client.get("/v1/me", headers=auth_header(t)).status_code == 401
    assert client.post("/v1/auth/refresh", json={"refresh_token": t["refresh_token"]}).status_code == 401


def test_guardian_adds_child_with_consent_and_child_can_log_in(client):
    register(client)
    parent = login(client).json()
    r = client.post(
        "/v1/guardians/children",
        headers=auth_header(parent),
        json={"full_name": "Enfant Un", "level": "premiere_s", "password": "mot-de-passe-enfant"},
    )
    assert r.status_code == 201
    assert r.json()["phone"] == "+25377000001"                 # repris du parent par défaut
    me = client.get("/v1/me", headers=auth_header(parent)).json()
    assert [c["full_name"] for c in me["children"]] == ["Enfant Un"]
    assert me["children"][0]["has_access"] is False

    child = login(client, password="mot-de-passe-enfant")      # mot de passe distinct : pas d'ambiguïté
    assert child.status_code == 200
    assert child.json()["user"]["role"] == "student"
    child_me = client.get("/v1/me", headers=auth_header(child.json())).json()
    assert child_me["has_access"] is False
    assert [g["full_name"] for g in child_me["guardians"]] == ["Parent Test"]


def test_student_cannot_add_children(client):
    register(client, role="student", name="Eleve Seul", level="terminale_s")
    t = login(client).json()
    r = client.post("/v1/guardians/children", headers=auth_header(t),
                    json={"full_name": "Autre Enfant", "level": "premiere_s", "password": PASSWORD})
    assert r.status_code == 403


def test_same_phone_and_same_password_is_ambiguous_until_disambiguated(client):
    register(client, name="Parent Test")
    register(client, role="student", name="Enfant Test", level="premiere_s")   # même numéro, même mot de passe
    amb = login(client)
    assert amb.status_code == 409
    assert amb.json()["error"]["code"] == "ambiguous_account"
    assert login(client, role="student").json()["user"]["full_name"] == "Enfant Test"
    assert login(client, full_name="parent test").json()["user"]["role"] == "guardian"


def test_has_access_follows_the_payment_activation_logic(client, db):
    """Relie l'API au SQL du paiement : une fois activé, l'élève voit has_access = true."""
    register(client, role="student", name="Eleve Payeur", level="premiere_s")
    tokens = login(client).json()
    student_id = tokens["user"]["id"]
    assert client.get("/v1/me", headers=auth_header(tokens)).json()["has_access"] is False

    sub_id = db.execute(
        text("INSERT INTO subscriptions (student_id, plan_id, amount_djf) VALUES (:s, 1, 2000) RETURNING id"),
        {"s": student_id},
    ).scalar_one()
    pay_id = db.execute(
        text("INSERT INTO payments (subscription_id, provider, status, amount_djf, idempotency_key) "
             "VALUES (:sub, 'manual', 'succeeded', 2000, :k) RETURNING id"),
        {"sub": sub_id, "k": str(uuid.uuid4())},
    ).scalar_one()
    db.execute(text("SELECT activate_payment(:p)"), {"p": pay_id})
    db.commit()
    assert client.get("/v1/me", headers=auth_header(tokens)).json()["has_access"] is True


def test_all_tables_have_row_level_security(db):
    missing = db.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
             "AND NOT rowsecurity AND tablename <> 'alembic_version'")
    ).scalars().all()
    assert missing == []

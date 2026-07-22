"""Step 27 sub-item -- portfolios (wireframes 4.png, 8.png). A manager's
personal, named grouping of their own visible projects -- real entity
(CRUD + project add/remove), not a saved filter. See
app/controlplane/models.py::Portfolio's docstring for the design fork
this resolves."""
import uuid

from app.controlplane.models import SessionLocal as ControlPlaneSessionLocal, Project as RegistryProject


def _make_project(manager_id, name="Alpha", kind="team"):
    db = ControlPlaneSessionLocal()
    try:
        p = RegistryProject(id=uuid.uuid4().hex, name=name, kind=kind, manager_user_id=manager_id)
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id
    finally:
        db.close()


def test_create_and_list_portfolio(client):
    r = client.post("/api/portfolios", json={"name": "Health Domain"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Health Domain"
    assert body["projects"] == []

    r = client.get("/api/portfolios")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()]
    assert names == ["Health Domain"]


def test_add_and_remove_project(client):
    project_id = _make_project(client.manager_id)
    r = client.post("/api/portfolios", json={"name": "Finance"})
    portfolio_id = r.json()["id"]

    r = client.patch(f"/api/portfolios/{portfolio_id}", json={"add_project_ids": [project_id]})
    assert r.status_code == 200, r.text
    assert [p["id"] for p in r.json()["projects"]] == [project_id]

    r = client.patch(f"/api/portfolios/{portfolio_id}", json={"remove_project_ids": [project_id]})
    assert r.status_code == 200
    assert r.json()["projects"] == []


def test_add_project_re_add_is_noop(client):
    project_id = _make_project(client.manager_id)
    r = client.post("/api/portfolios", json={"name": "Efficiency"})
    portfolio_id = r.json()["id"]

    client.patch(f"/api/portfolios/{portfolio_id}", json={"add_project_ids": [project_id]})
    r = client.patch(f"/api/portfolios/{portfolio_id}", json={"add_project_ids": [project_id]})
    assert len(r.json()["projects"]) == 1


def test_add_inaccessible_project_rejected(client):
    other_id = uuid.uuid4().hex
    r = client.post("/api/portfolios", json={"name": "X"})
    portfolio_id = r.json()["id"]
    r = client.patch(f"/api/portfolios/{portfolio_id}", json={"add_project_ids": [other_id]})
    assert r.status_code == 400


def test_rename_portfolio(client):
    r = client.post("/api/portfolios", json={"name": "Old Name"})
    portfolio_id = r.json()["id"]
    r = client.patch(f"/api/portfolios/{portfolio_id}", json={"name": "New Name"})
    assert r.json()["name"] == "New Name"


def test_delete_portfolio(client):
    r = client.post("/api/portfolios", json={"name": "Gone Soon"})
    portfolio_id = r.json()["id"]
    r = client.delete(f"/api/portfolios/{portfolio_id}")
    assert r.status_code == 204
    r = client.get(f"/api/portfolios/{portfolio_id}")
    assert r.status_code == 404


def test_portfolio_scoped_to_owner(client):
    """A second manager cannot see or mutate the first manager's portfolio."""
    from fastapi.testclient import TestClient
    from app.main import app

    r = client.post("/api/portfolios", json={"name": "Mine"})
    portfolio_id = r.json()["id"]

    other = TestClient(app)
    other.post("/api/auth/dev-login", json={"email": f"other-{uuid.uuid4().hex}@test.local", "name": "Other"})

    r = other.get(f"/api/portfolios/{portfolio_id}")
    assert r.status_code == 404
    r = other.get("/api/portfolios")
    assert r.json() == []


def test_get_nonexistent_portfolio_404(client):
    r = client.get(f"/api/portfolios/{uuid.uuid4().hex}")
    assert r.status_code == 404


def test_create_portfolio_without_login_is_401():
    from fastapi.testclient import TestClient
    from app.main import app

    c = TestClient(app)
    r = c.post("/api/portfolios", json={"name": "Nope"})
    assert r.status_code == 401

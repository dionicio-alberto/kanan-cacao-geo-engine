import tempfile
from fastapi.testclient import TestClient
from app.main import app
from app.services.local_persistence_engine import init_local_db


def test_public_logbook_config_excludes_harvest_and_defaults_labor_enabled():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        init_local_db(tmp.name)
        client = TestClient(app)
        res = client.get("/api/v1/logbook/config")
        assert res.status_code == 200
        data = res.json()
        assert "config" in data
        assert "activities" in data
        assert "inputs" in data

        config = data["config"]
        # Labor and costs enabled by default as requested by user
        assert config["enable_labor_and_costs"] is True
        assert config["version"] >= 1

        # Must have default agronomic activities and zero harvest/acopio
        activities = data["activities"]
        assert len(activities) >= 8
        activity_keys = [a["activity_key"] for a in activities]
        assert "MANEJO_SOMBRA" in activity_keys
        assert "PODA_CACAO" in activity_keys
        assert "DESMOMIFICACION_FITOSANITARIA" in activity_keys
        assert "FERTILIZACION_ORGANICA" in activity_keys
        assert "COSECHA" not in activity_keys
        assert "ACOPIO" not in activity_keys
        assert "COSECHA_CORTE" not in activity_keys


def test_admin_logbook_toggles_rbac_and_persistence():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        init_local_db(tmp.name)
        client = TestClient(app)

        # Producer without permissions must be forbidden
        forbidden_res = client.put(
            "/api/v1/admin/logbook/toggles",
            json={"toggles": {"enable_labor_and_costs": False}, "requester_role": "productor"},
        )
        assert forbidden_res.status_code == 403

        # Admin must succeed and bump version
        ok_res = client.put(
            "/api/v1/admin/logbook/toggles",
            json={"toggles": {"enable_labor_and_costs": False}, "requester_role": "admin_astra"},
        )
        assert ok_res.status_code == 200
        data = ok_res.json()
        assert data["config"]["enable_labor_and_costs"] is False
        assert data["config"]["version"] >= 2

        # Re-enable toggle
        reenable_res = client.put(
            "/api/v1/admin/logbook/toggles",
            json={"toggles": {"enable_labor_and_costs": True}, "requester_role": "admin_astra"},
        )
        assert reenable_res.status_code == 200
        assert reenable_res.json()["config"]["enable_labor_and_costs"] is True


def test_admin_activities_crud_and_intelligent_archiving():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        init_local_db(tmp.name)
        client = TestClient(app)

        # 1. Create a custom activity
        create_res = client.post(
            "/api/v1/admin/logbook/activities",
            json={
                "activity": {
                    "activity_key": "INJERTACION_CLONAL",
                    "name": "Injertación Clonal de Varetas Criollas",
                    "category": "PROPAGACION",
                    "icon": "🌱",
                    "description": "Injerto de púa lateral o aproximación en patrones de cacao.",
                    "requires_inputs": True,
                    "requires_dose": False,
                    "sort_order": 9,
                },
                "requester_role": "admin_astra",
            },
        )
        assert create_res.status_code == 200
        activities = create_res.json()["activities"]
        matching = next((a for a in activities if a["activity_key"] == "INJERTACION_CLONAL"), None)
        assert matching is not None
        assert matching["name"] == "Injertación Clonal de Varetas Criollas"

        # 2. Add a field logbook entry referencing INJERTACION_CLONAL
        log_res = client.post(
            "/api/v1/logbook",
            json={
                "parcel_id": "PAR-CHONTALPA-01",
                "activity_type": "INJERTACION_CLONAL",
                "activity_label": "Injertación Clonal",
                "notes": "Injertación de 50 varetas de clon carrizal.",
                "dose": "50 varetas",
                "cost_mxn": 750.0,
                "labor_hours": 6.0,
                "workers_count": 2,
            },
        )
        assert log_res.status_code == 200
        created_entry = log_res.json()["entry"]
        assert created_entry["activity_type"] == "INJERTACION_CLONAL"
        assert created_entry["cost_mxn"] == 750.0

        # 3. Delete INJERTACION_CLONAL -> MUST be ARCHIVED (intelligent archiving) because history references it
        del_res = client.delete(
            "/api/v1/admin/logbook/activities/INJERTACION_CLONAL",
            params={"requester_role": "admin_astra"},
        )
        assert del_res.status_code == 200
        del_data = del_res.json()
        assert del_data["action"] == "ARCHIVED"
        assert del_data["referenced_logs_count"] >= 1

        # Public config should NOT list the archived activity
        public_res = client.get("/api/v1/logbook/config")
        public_keys = [a["activity_key"] for a in public_res.json()["activities"]]
        assert "INJERTACION_CLONAL" not in public_keys

        # 4. Create an unreferenced temporary activity and delete it -> MUST be hard DELETED
        client.post(
            "/api/v1/admin/logbook/activities",
            json={
                "activity": {
                    "activity_key": "ACTIVIDAD_TEMP_ELIMINAR",
                    "name": "Actividad Temporal a Eliminar",
                    "category": "GENERAL",
                    "icon": "⏳",
                },
                "requester_role": "admin_astra",
            },
        )
        del_temp_res = client.delete(
            "/api/v1/admin/logbook/activities/ACTIVIDAD_TEMP_ELIMINAR",
            params={"requester_role": "admin_astra"},
        )
        assert del_temp_res.status_code == 200
        assert del_temp_res.json()["action"] == "DELETED"
        assert del_temp_res.json()["referenced_logs_count"] == 0


def test_logbook_entry_patch_and_delete():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        init_local_db(tmp.name)
        client = TestClient(app)

        # Create entry
        create_res = client.post(
            "/api/v1/logbook",
            json={
                "parcel_id": "PAR-CHONTALPA-01",
                "activity_type": "PODA_CACAO",
                "notes": "Poda ligera inicial",
                "labor_hours": 3.0,
            },
        )
        assert create_res.status_code == 200
        entry_id = create_res.json()["entry"]["id"]

        # Edit entry via PATCH
        patch_res = client.patch(
            f"/api/v1/logbook/{entry_id}",
            json={
                "notes": "Poda fitosanitaria profunda completada",
                "labor_hours": 5.5,
                "workers_count": 3,
                "cost_mxn": 900.0,
            },
        )
        assert patch_res.status_code == 200
        updated = patch_res.json()["entry"]
        assert updated["notes"] == "Poda fitosanitaria profunda completada"
        assert updated["labor_hours"] == 5.5
        assert updated["workers_count"] == 3
        assert updated["cost_mxn"] == 900.0

        # Delete entry via DELETE
        del_res = client.delete(f"/api/v1/logbook/{entry_id}")
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "DELETED"

        # Subsequent delete should 404
        del_again = client.delete(f"/api/v1/logbook/{entry_id}")
        assert del_again.status_code == 404

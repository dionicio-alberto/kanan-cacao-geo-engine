## Resumen del Pull Request — Geo-Engine (AI-DLC)

- **Bolt / Unidad de Trabajo:** `Bolt-X`
- **Historias de Usuario Relacionadas:** `HU-XX`
- **Rama Destino:** `develop` (o `main` para releases)

## Checklist de Compuerta de Calidad (AI-DLC Gate)
- [ ] **Linting Python (Flake8):** `flake8 app tests` pasa sin errores.
- [ ] **Pruebas Automatizadas (Pytest):** `pytest -v` ejecuta y aprueba todos los casos.
- [ ] **Escaneo de Secretos (Gitleaks):** Sin credenciales de Google Earth Engine, GCP o Supabase expuestas.
- [ ] **Estándares EUDR / EPCIS 2.0:** Respeta corte base 2020-12-31, descargo legal obligatorio y modo ciego en báscula.

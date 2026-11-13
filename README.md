# kanan-cacao-geo-engine — Motor Analítico Serverless (FastAPI / Cloud Run)

**Propiedad de:** ASTRA (Agrotecnología y Servicios de Transformación en Recursos Agrícolas de México S.A.S.)  
**Metodología:** AI-Driven Development Life Cycle (AI-DLC)

Microservicio analítico en **FastAPI (Python)** diseñado para ejecutarse bajo demanda en **Google Cloud Run** y validar matemáticamente los polígonos, capas satelitales y eventos de custodia de **Kanan Cacao**:

- **HU-01 & HU-02:** Linter topológico geoespacial en servidor (cierre de anillo RFC 7946, detección de auto-intersecciones con Shapely, obligatoriedad `> 4.0 ha` y límite `< 25 MB`).
- **HU-03:** Semáforo Automático de Deforestación (cruce contra capa **JRC Global Forest Cover al 31 de diciembre de 2020** con descargo legal obligatorio).
- **HU-04:** Capa de Salud Vegetal (**NDVI Multitemporal** con escenas de **Sentinel-2**) para detección temprana de estrés hídrico o moniliasis.
- **HU-05, HU-06 & HU-07:** Verificación de permiso ambiental **CUSTF (SEMARNAT)**, evaluación ciega de *Yield Caps* biológicos de Tabasco (**450 a 650 kg/ha**) y firma criptográfica **SHA-256** de eventos **GS1 EPCIS 2.0 en JSON-LD**.

## Comandos de Desarrollo y Verificación (CI/CD)

```bash
# Ejecutar Linter Flake8
flake8 app tests

# Ejecutar Suite de Pruebas Pytest
pytest -v

# Iniciar servidor local
uvicorn app.main:app --reload --port 8080
```

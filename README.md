# 🛰️ kanan-cacao-geo-engine — Backend Central Compartido y Motor Geoespacial (FastAPI / Railway)

**Propiedad de:** ASTRA (Agrotecnología y Servicios de Transformación en Recursos Agrícolas de México S.A.S.)  
**Metodología:** AI-Driven Development Life Cycle (AI-DLC)  
**Producción:** [https://kanan-cacao-geo-engine-production.up.railway.app](https://kanan-cacao-geo-engine-production.up.railway.app)

---

## 📚 Documentación Centralizada del Ecosistema

Para consultar toda la documentación humana, funcional y técnica en un solo lugar:
* 🧭 **[Centro Maestro de Documentación (`kanan-cacao/docs/00-INDICE-MAESTRO-DOCUMENTACION.md`)](../kanan-cacao/docs/00-INDICE-MAESTRO-DOCUMENTACION.md)**
* 🛰️ **[Manual Técnico Detallado de este Backend (`docs/MANUAL-TECNICO-GEO-ENGINE.md`)](./docs/MANUAL-TECNICO-GEO-ENGINE.md)**
* 🏛️ **[Arquitectura y Decisiones ADR (`kanan-cacao/docs/01-ARQUITECTURA-Y-DECISIONES-ADR.md`)](../kanan-cacao/docs/01-ARQUITECTURA-Y-DECISIONES-ADR.md)**
* 🧩 **[Diccionario de Funciones y Código (`kanan-cacao/docs/03-DICCIONARIO-DE-FUNCIONES-Y-CODIGO.md`)](../kanan-cacao/docs/03-DICCIONARIO-DE-FUNCIONES-Y-CODIGO.md)**

---

## ⚙️ Servicios que Comparte con la Web y la App Móvil

Este repositorio es el **cerebro central que comparten `kanan-cacao-web` y `kanan-cacao-mobile`**:

1. **Sincronización de Parcelas y Validación Topológica RFC 7946:**
   - Endpoints `/api/v1/parcels`, `/api/v1/parcels/sync` y `/api/v1/topology/validate` (detección de auto-intersecciones con Shapely y cálculo geodésico de hectáreas).
2. **Bóveda de Documentación Legal con Firma SHA-256:**
   - Endpoints `/api/v1/parcels/{parcel_id}/documents` para resguardar Certificados Parcelarios, Títulos de Propiedad y permisos CUSTF de SEMARNAT con huella criptográfica inmutable.
3. **Bitácora de Campo Configurable y Archivado Inteligente:**
   - Endpoints públicos `/api/v1/logbook/config`, `/api/v1/logbook`, `/api/v1/logbook/{id}` (`PATCH` / `DELETE`) y `/api/v1/logbook/sync`.
   - Endpoints administrativos protegidos por rol RBAC `/api/v1/admin/logbook/*` con **Archivado Inteligente** (`is_archived = true`) para preservar el historial cuando se desactiva o elimina una labor del catálogo.
4. **Inteligencia Satelital Multitemporal y Trazabilidad EUDR 2020:**
   - Ortofotos históricas Esri Wayback (2020–2026), cálculo de salud vegetal NDVI (Copernicus Sentinel-2), evaluación ciega de *Yield Caps* de Tabasco (450–650 kg/ha) y exportación TRACES-NT JSON-LD.

---

## 🚀 Comandos de Desarrollo, Docker y Pruebas (CI/CD)

```bash
# Ejecutar Linter Flake8
flake8 app tests

# Ejecutar Suite de 32 Pruebas Automatizadas (Pytest)
pytest -v

# Iniciar servidor local en http://localhost:8080
uvicorn app.main:app --reload --port 8080

# Construir y correr en contenedor Docker local
docker build -t kanan-geo-engine .
docker run -p 8080:8080 kanan-geo-engine
```

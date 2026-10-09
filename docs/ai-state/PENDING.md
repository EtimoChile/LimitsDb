---
status: active
authority: operational-state
scope: development/maintenance/release
last-reviewed: 2026-10-08
---

# Pendientes vigentes

Cola compacta. Cada pendiente debe identificar un artefacto propietario y una
próxima acción. Al resolverlo, retirarlo de esta tabla y conservar en
`DECISIONS.md` cualquier decisión duradera que haya resultado.

Estados permitidos: `OPEN`, `IN_PROGRESS`, `DEFERRED` y `FUTURE`.

| ID | Estado | Ámbito | Pendiente | Artefacto propietario | Próxima acción |
|---|---|---|---|---|---|
| `PEND-006` | `OPEN` | Publicación/CI | Completar la barrera de publicación sobre el CI de calidad vigente: publicar sólo desde una versión etiquetada y autorizada, con procedencia y entorno protegidos. | `.github/workflows/quality.yaml`; workflow futuro de release; `pyproject.toml`; `CHANGELOG.md` | Definir el canal y las credenciales protegidas, validar primero en TestPyPI o un registro privado y diseñar la autorización de release; no publicar ni crear tags sin solicitud explícita. Requiere `PEND-014`. |
| `PEND-014` | `IN_PROGRESS` | Seguridad/CI | Implementar control de branches y permisos como prerequisito de `PEND-006`: (1) crear `.github/CODEOWNERS` con `@etimochile` como reviewer requerido en rutas críticas; (2) agregar `permissions:` explícitos por job en `quality.yaml` y `oracle-integration.yaml`; (3) activar branch protection rules en GitHub UI — required checks (`quality`), require PR antes de merge a `main` y `development`, restrict direct push, require up-to-date branches. | `.github/CODEOWNERS`; `.github/workflows/quality.yaml`; `.github/workflows/oracle-integration.yaml`; configuración de repo en GitHub UI | Pasos (1) y (2) completados en código. Pendiente: configurar branch protection rules en GitHub → Settings → Branches (acción manual fuera del repositorio). |
| `PEND-008` | `OPEN` | Distribución | Definir los canales soportados para contenedor y eventual ejecutable autónomo sin asumir que un binario impide copia o reemplaza las obligaciones de licencia; el wheel ya cuenta con instalación limpia automatizada. | `pyproject.toml`; `README.md`; `.github/workflows/quality.yaml`; configuración futura de empaquetado y release | Evaluar contenedor, PyInstaller o Nuitka con el driver Oracle y documentar la matriz elegida antes de ofrecer binarios. |

---
status: active
authority: operational-state
scope: development/maintenance/release
last-reviewed: 2026-10-06
last-updated: 2026-10-06
---

# Estado vigente

Memoria compacta del proyecto. Es autoritativa para coordinación y estado
operativo, pero no reemplaza las fuentes definidas en `AGENTS.md`.

## Situación actual

- La rama de integración es `development`; `main` representa versiones
  estables según `README.md`.
- La versión declarada en `pyproject.toml` es `0.5.0` y requiere Python
  `>=3.12,<4.0`.
- El nombre vigente es `LimitsDb`, el paquete Python es `limitsdb` y el prefijo
  técnico público es `ldb` / `LDB`; el cambio desde el nombre anterior es
  incompatible y está documentado en `README.md` y `CHANGELOG.md`.
- El paquete implementa ILM dirigido por configuración. Oracle es el adaptador
  disponible; el contrato `DatabaseEngine` mantiene la extensión a otros
  motores.
- Las interfaces publicadas son `ldb-run`, `ldb-init`, `ldb-crypt` y
  `ldb-impl`.
- La configuración se resuelve por archivo, variables `LDB_*` y opciones
  `--set`; los secretos se cargan por separado y se exigen cifrados en la ruta
  normal.
- Las operaciones ILM pueden archivar, purgar, crear objetos y administrar
  privilegios. No se ejecutan contra una base real sin autorización explícita y
  un entorno identificado.
- La suite reside en `tests/`. La baseline unitaria verificada el 2026-10-06 es
  de 68 pruebas exitosas; Ruff valida formato y lint, y `mypy` estricto termina
  sin hallazgos sobre los 23 módulos del paquete. Dos pruebas de integración
  Oracle se recolectan por separado y requieren una base desechable explícita.
- La configuración pública conserva claves planas y precedencia sistema,
  usuario, archivo explícito, entorno, CLI y secretos. El núcleo consume vistas
  tipadas e inmutables de ejecución, conexiones, administración y contexto; las
  reglas ILM normalizadas y el estado derivado de tablas también tienen
  contratos tipados. `PEND-004` está resuelto.
- Los límites de configuración, secretos, validación, conexión y ejecución
  exponen errores de dominio encadenados. Los archivos de secretos inválidos
  detienen la operación sin sobrescribirse y los fallos de workers producen un
  resultado recuperable identificado por tabla. El coordinador reintenta en la
  misma corrida un fallo parcial sólo si aumentó el contador procesado; sin
  progreso lo convierte en `SKIPPED`.
- El workflow `.github/workflows/quality.yaml` ejecuta en Python 3.12 las
  barreras de formato, lint, tipado, pruebas, pre-commit, construcción,
  verificación de distribuciones e instalación limpia del wheel. La publicación
  autorizada desde versiones etiquetadas permanece pendiente.
- El workflow `.github/workflows/oracle-integration.yaml` levanta Oracle Free
  efímero en un runner Linux para cambios relevantes y ejecución manual, con
  imagen fijada por digest y credenciales locales al job; su primera ejecución
  real en GitHub permanece pendiente.
- Inversiones Etimo SpA mantiene el proyecto. Los términos aplicables se
  distribuyen en los archivos de licencia, notices y edición comercial de la
  raíz.

## Decisiones activas necesarias para continuar

- `DEC-001`: mantener una memoria persistente compacta en `docs/ai-state/`.
- `DEC-002`: preservar la frontera entre núcleo independiente y adaptadores de
  motor.
- `DEC-003`: tratar toda ejecución ILM o administrativa sobre bases reales como
  una acción explícitamente autorizada.
- `DEC-004`: `AGENTS.md` es la única constitución; los archivos específicos de
  herramientas son puentes mínimos.
- `DEC-005`: usar `LimitsDb`, `limitsdb` y `ldb` / `LDB` como identidad y
  prefijos públicos del proyecto.
- `DEC-006`: mantener Python 3.12 como implementación del ciclo actual y no
  iniciar una reescritura a Java sin evidencia nueva y una decisión sucesora.
- `DEC-007`: unificar formato y lint con Ruff a 120 columnas, mantener `mypy`
  estricto como autoridad de tipado y reproducir los controles en pre-commit y
  CI.
- `DEC-008`: traducir fallos en fronteras de subsistema a errores de dominio
  encadenados y permitir capturas amplias sólo en fronteras documentadas.
- `DEC-009`: mantener plana la configuración pública y migrar internamente por
  fases hacia contratos tipados e inmutables.
- `DEC-010`: ejecutar la integración Oracle en una base efímera dentro de un
  runner Linux de GitHub Actions, sin acceso externo a la base cloud persistente.

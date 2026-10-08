---
status: active
authority: operational-state
scope: development/maintenance/release
last-reviewed: 2026-10-07
last-updated: 2026-10-08T16:36
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
  disponible y el único motor aceptado por configuración; PostgreSQL permanece
  como implementación futura y se rechaza antes de conectar. El contrato
  `DatabaseEngine` mantiene la extensión a otros motores. `PEND-007` está
  resuelto.
- Las interfaces publicadas son `ldb-run`, `ldb-init`, `ldb-crypt` y
  `ldb-impl`.
- La configuración se resuelve por archivo, variables `LDB_*` y opciones
  `--set`; los secretos se cargan por separado y se exigen cifrados en la ruta
  normal.
- Las operaciones ILM pueden archivar, purgar, crear objetos y administrar
  privilegios. No se ejecutan contra una base real sin autorización explícita y
  un entorno identificado.
- La suite reside en `tests/`. La baseline unitaria verificada el 2026-10-07 es
  de 97 pruebas exitosas (ampliada desde 81 con pruebas locales para `ldb-init`,
  modos PLAN y VALIDATE, generación de script y métodos Oracle de PK y registro
  de errores); Ruff valida formato y lint, y `mypy` estricto termina
  sin hallazgos sobre los 23 módulos del paquete. Los tres casos vigentes de
  integración ejecutan métodos del adaptador `OracleEngine` y pasaron sobre la
  base efímera de CI; cubren idempotencia, recuperación tras DDL parcial y
  operaciones privilegiadas. No se conservan pruebas del comportamiento propio
  del proveedor sin intervención de LimitsDb. El cuarto caso E2E crea esquemas
  separados mediante `ldb-impl`, ejecuta `SOURCE_ILM` y `HISTORY_ILM` sobre
  filas controladas y verifica datos y auditoría. La ampliación vigente ejecuta
  los entry points instalados, incorpora tablas padre-detalle con clave foránea,
  una rama independiente con dos workers y reruns idempotentes de ambas acciones;
  los cuatro casos pasaron en GitHub sobre el commit `d8a77ea`. `PEND-005` está
  resuelto. La corrección de `PEND-012` conserva por separado el filtro
  histórico normalizado, materializa sus columnas relacionadas aunque no estén
  en el filtro de origen y delega al adaptador la reescritura hacia
  `<columna>_<alias>` durante `HISTORY_ILM`; las dos pruebas unitarias focales y
  las 74 pruebas locales pasan. Las seis pruebas Oracle también pasaron en el
  runner efímero de GitHub `37679891653` sobre el commit `d5f9c95`, incluidas
  las dos variantes que antes reproducían los defectos. `PEND-012` está
  resuelto. El run `37701931130` sobre `38ab114` verificó 97 pruebas locales y 9
  E2E exitosas (cobertura combinada 85 %; TOTAL 2582 sentencias, 298 no
  cubiertas, 818 ramas, 154 parciales); todavía no se impone un umbral mínimo.
  El run `37712040787` sobre `1181775` (PEND-009) verificó 108 pruebas locales
  y 9 E2E exitosas (cobertura combinada 87 %; TOTAL 2564 sentencias, 268 no
  cubiertas, 816 ramas, 146 parciales). El run `37809692118` sobre `4047abc`
  (corrección detección de índices no gestionados + 2 tests E2E nuevos) verificó
  108 pruebas locales y 11 E2E exitosas (cobertura combinada 87 %; TOTAL 2564
  sentencias, 250 no cubiertas, 816 ramas, 148 parciales).
- La configuración pública conserva claves planas y precedencia sistema,
  usuario, archivo explícito, entorno, CLI y secretos. El núcleo consume vistas
  tipadas e inmutables de ejecución, conexiones, administración y contexto; las
  reglas ILM normalizadas y el estado derivado de tablas también tienen
  contratos tipados. `PEND-004` está resuelto.
- Las variables de entorno se habilitan y mapean exclusivamente mediante los
  metadatos `Env(...)` de `Config`; todos sus nombres públicos usan el prefijo
  `LDB_`, incluido `LDB_ILM_CONFIG_FILE`. `use_added_columns` y
  `add_ldb_columns` se fijan en la configuración YAML persistente del ambiente
  y no admiten flags, variables de entorno ni `--set`, porque variar estas
  características puede desestabilizar el procesamiento histórico. `PEND-011`
  está resuelto.
- Los límites de configuración, secretos, validación, conexión y ejecución
  exponen errores de dominio encadenados. Los archivos de secretos inválidos
  detienen la operación sin sobrescribirse y los fallos de workers producen un
  resultado recuperable identificado por tabla. El coordinador reintenta en la
  misma corrida un fallo parcial sólo si aumentó el contador procesado; sin
  progreso lo convierte en `SKIPPED`.
- `source_orphan_purge` archiva las filas de origen cuyo `left outer join` no
  encuentra una relación, detectadas por cualquier `orphan_check_column IS
  NULL`. Requiere snapshots derivados y retención histórica positiva; persiste
  el estado en `LDB_IS_ORPHAN` y, si la fecha de una relación es nula, guarda la
  fecha de proceso para que `HISTORY_ILM` conserve la retención evaluable. La
  suite local vigente contiene 81 pruebas y existe un caso E2E Oracle separado
  pendiente de ejecución en el runner efímero. `PEND-010` está resuelto.
- El workflow `.github/workflows/quality.yaml` ejecuta en Python 3.12 las
  barreras de formato, lint, tipado, pruebas, pre-commit, construcción,
  verificación de distribuciones e instalación limpia del wheel. La publicación
  autorizada desde versiones etiquetadas permanece pendiente.
- El workflow `.github/workflows/oracle-integration.yaml` levanta Oracle Free
  efímero en un runner Linux para pull requests relevantes y ejecución manual;
  no se ejecuta en cada `push`. Usa una imagen fijada por digest y credenciales
  aleatorias por job, enmascaradas antes de exponerlas al entorno de pasos
  posteriores. Su primera ejecución real en GitHub terminó correctamente:
  descarga de imagen en 49 segundos, disponibilidad de Oracle unos 28 segundos
  después del arranque, 2.393 GiB de memoria y 5.4 GiB de disco libre al ejecutar
  las pruebas. La suite ampliada tardó 4.16 segundos y mantuvo 5.4 GiB libres.
  El workflow también mide cobertura combinada de líneas y ramas para la suite
  local y la E2E, incluidos subprocesses CLI y workers, y conserva informes de
  texto, XML y HTML durante 14 días. El run `37689648223` sobre `faa3209`
  verificó 80 pruebas locales y 6 E2E exitosas, con 81,42 % de líneas, 67,46 %
  de ramas y 78 % combinado; `PEND-013` está resuelto y todavía no se impone un
  umbral mínimo.
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
- `DEC-011`: mantener las políticas ILM exclusivamente en archivos de
  configuración y retirar el uso de `LDB_CNF` sin eliminar automáticamente
  objetos legados. `PEND-009` está resuelto: `_get_conf_rows` y
  `DatabaseEngine.load_config` eliminados; `LDB_CNF`/`LDB_CNF_ID` retirados
  del DDL de bootstrap; `_load_offline_rows` lanza `ConfigurationError` cuando
  no existe archivo ILM; 108 pruebas locales pasan.
- `DEC-012`: cada fila histórica conserva los valores necesarios para reevaluar
  todos sus predicados, incluidas fechas de corte y columnas adicionales de
  tablas relacionadas; `LDB_DATE_<suffix>` usa un diferenciador, no un alias SQL.
- `DEC-013`: fijar `use_added_columns` y `add_ldb_columns` exclusivamente en la
  configuración persistente del ambiente y excluirlos de CLI, entorno y `--set`.
- `DEC-014`: aceptar sólo Oracle hasta que exista otro adaptador y rechazar
  PostgreSQL tempranamente en configuración, CLI y carga de motor.
- `DEC-015`: medir conjuntamente cobertura local y Oracle E2E, incluidos los
  procesos hijos, y verificar la baseline antes de imponer un umbral.
- `DEC-016`: archivar los huérfanos detectados por outer join, usando la fecha
  de proceso como fallback de sus fechas de control relacionadas.

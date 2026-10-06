# Constitución operativa de LimitsDb

LimitsDb es un ejecutor de Information Lifecycle Management (ILM) dirigido
por configuración. Archiva y elimina datos operacionales, por lo que una
modificación aparentemente pequeña puede afectar datos, privilegios o esquemas
reales. Las reglas siguientes son obligatorias para cualquier agente.

## Precedencia y fuentes de verdad

En caso de conflicto, aplicar este orden:

1. `AGENTS.md`: proceso, límites y autoridad documental.
2. `README.md`: comportamiento público, configuración, CLI y flujo operativo.
3. `limitsdb/resources/ilm.example.yml`: contrato de ejemplo de las políticas
   ILM; los modelos y validadores bajo `limitsdb/core/` definen su
   interpretación ejecutable vigente.
4. `limitsdb/db/ldb_engines.py`: contrato independiente del motor; cada
   adaptador bajo `limitsdb/db/<motor>/` implementa ese contrato.
5. Código, configuración y pruebas vigentes, para hechos verificables de
   implementación.
6. `CHANGELOG.md`, para historia publicada; no sustituye el estado actual.

`CONTRIBUTING.md` rige las contribuciones externas. `LICENSE`,
`LICENSE-DUAL.md`, `COMMERCIAL-EDITION-NOTICE.md`, `NOTICE` y
`THIRD_PARTY_LICENSES.md` son autoritativos para licencias, atribuciones y
ediciones comerciales. No reinterpretar ni modificar esos términos como parte
de una tarea técnica ordinaria.

Si dos fuentes del mismo rango discrepan, no elegir silenciosamente. Registrar
el conflicto en `docs/ai-state/PENDING.md` y limitar el cambio a lo que siga
siendo inequívoco.

## Estado persistente y uso eficiente del contexto

Después de este archivo, toda tarea debe leer primero
`docs/ai-state/CURRENT.md` y `docs/ai-state/PENDING.md`. Consultar
`docs/ai-state/DECISIONS.md` sólo para los IDs o ámbitos relacionados con la
tarea; no releerlo indiscriminadamente cuando crezca.

Estos archivos son memoria operativa autoritativa, no una especificación nueva:

- `CURRENT.md` resume hechos vigentes y verificables;
- `PENDING.md` contiene únicamente trabajo abierto con una próxima acción;
- `DECISIONS.md` conserva decisiones duraderas y evita reabrir alternativas sin
  evidencia nueva.

Al cerrar cada interacción, revisar los tres archivos y modificarlos sólo si
cambió el estado, surgió o se resolvió un pendiente, o se adoptó, reemplazó o
descartó una decisión. No usarlos como diario de sesiones ni hacer ediciones
artificiales.

## Arquitectura y límites de cambio

- Mantener `limitsdb/core/` independiente del motor. No introducir SQL,
  tipos, excepciones ni supuestos exclusivos de Oracle en el núcleo.
- Mantener las particularidades de Oracle en `limitsdb/db/oracle/`. Una nueva
  capacidad de motor debe exponerse primero en `DatabaseEngine` y después
  implementarse y probarse en los adaptadores afectados.
- Las entradas de consola viven en `limitsdb/cli/`; deben delegar la lógica de
  negocio al núcleo y conservar códigos de salida útiles para automatización.
- Los recursos empaquetados viven en `limitsdb/resources/`. Si se agregan o
  renombran, verificar que la configuración de empaquetado en `pyproject.toml`
  los incluya.
- No cambiar a la vez comportamiento, formato de configuración y documentación
  sin pruebas que demuestren la migración o compatibilidad prevista.
- Preservar Python `>=3.12,<4.0`, tipado estricto y las convenciones declaradas
  en `pyproject.toml` y `.pre-commit-config.yaml`, salvo decisión
  explícita que actualice esos contratos.

## Seguridad, secretos y datos

- Nunca versionar credenciales, DSN privados, claves de cifrado, volcados de
  datos, identificadores personales ni logs con valores sensibles.
- No imprimir ni copiar secretos descifrados. Las rutas normales deben mantener
  `enforce_encrypted_secrets=True`; relajar esa protección requiere una tarea
  explícita y una justificación documentada.
- Tratar `SOURCE_ILM`, `HISTORY_ILM`, purgas, DDL, grants, usuarios, enlaces de
  base de datos y ejecución de bloques SQL/PLSQL como operaciones destructivas
  o privilegiadas.
- No ejecutar `ldb-run`, `ldb-impl` ni SQL contra una base real sin autorización
  explícita, entorno identificado y revisión previa de esquema, perfil y
  acción. Para pruebas automatizadas usar dobles, fixtures o conexiones que el
  usuario haya declarado desechables.
- No debilitar validaciones, controles de estado, transacciones o registro de
  errores para hacer pasar un caso. Un fallo parcial debe conservar evidencia
  suficiente para recuperación sin exponer datos sensibles.

## Pruebas y criterios de cierre

Aplicar una verificación proporcional al cambio. La secuencia completa es:

```bash
poetry run pytest
poetry run mypy limitsdb
poetry run ruff check .
poetry run ruff format --check .
poetry run pre-commit run --all-files
poetry build
poetry run twine check dist/*
```

No afirmar que una comprobación pasó si no fue ejecutada en la interacción.
Una prueba que necesita Oracle real no se improvisa ni se apunta a un entorno
desconocido; se documenta como limitación o pendiente.

Todo cambio de comportamiento debe incluir o actualizar pruebas y la
documentación pública correspondiente. Todo cambio publicable debe evaluar si
requiere actualizar versión y `CHANGELOG.md`; no efectuar una liberación, crear
tags ni publicar artefactos sin solicitud explícita.

## Gobierno y propiedad

Inversiones Etimo SpA mantiene la arquitectura, el roadmap y las liberaciones.
Las contribuciones comunitarias se reciben mediante issues y pull requests de
acuerdo con `CONTRIBUTING.md`.

Las decisiones técnicas nuevas se registran en
`docs/ai-state/DECISIONS.md` cuando sean duraderas, afecten más de una tarea o
descarten una alternativa razonable. Usar IDs consecutivos `DEC-NNN`, estados
`VIGENTE`, `REEMPLAZADA` o `DESCARTADA`, y enlazar los artefactos donde la
decisión queda implementada.

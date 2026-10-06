---
status: active
authority: decision-register
scope: development/maintenance/release
last-reviewed: 2026-10-05
---

# Registro compacto de decisiones

Este registro evita reabrir alternativas ya decididas. Resume decisiones
duraderas y apunta al artefacto que las publica o implementa. No duplica
especificaciones ni registra cada cambio de código.

| ID | Fecha | Estado | Ámbito | Decisión | Motivo | Reemplaza/Relacionada | Publicada en |
|---|---|---|---|---|---|---|---|
| `DEC-001` | 2026-10-05 | `VIGENTE` | Gestión | Mantener `CURRENT`, `PENDING` y `DECISIONS` como memoria persistente compacta y revisarlos al cierre de cada interacción. | Evitar reconstruir contexto desde conversaciones o historia y evitar documentación artificial. | Inicio del registro. | `AGENTS.md` |
| `DEC-002` | 2026-10-05 | `VIGENTE` | Arquitectura | Mantener el núcleo independiente del motor y encapsular Oracle bajo `limitsdb/db/oracle/`; toda capacidad común nace en `DatabaseEngine`. | Preservar la extensión declarada a otros motores sin filtrar detalles de Oracle al dominio. | Contrato arquitectónico vigente. | `AGENTS.md`; `limitsdb/db/ldb_engines.py` |
| `DEC-003` | 2026-10-05 | `VIGENTE` | Seguridad/operación | Exigir autorización explícita y un entorno identificado antes de ejecutar ILM, DDL, administración o SQL contra una base real. | El producto puede archivar o eliminar datos y modificar objetos o privilegios. | Regla constitucional. | `AGENTS.md` |
| `DEC-004` | 2026-10-05 | `VIGENTE` | Gestión/agentes | Usar `AGENTS.md` como única constitución y mantener `CLAUDE.md` como puente sin reglas duplicadas. | Evitar divergencia entre instrucciones específicas de herramientas. | Relacionada con `DEC-001`. | `AGENTS.md`; `CLAUDE.md` |
| `DEC-005` | 2026-10-05 | `VIGENTE` | Identidad/API | Renombrar el proyecto a `LimitsDb`, el paquete a `limitsdb` y el prefijo técnico a `ldb` / `LDB` en todas las interfaces públicas y objetos propios. | Evitar la colisión con el proyecto TerminusDB y expresar el control del crecimiento de las bases de datos. | Cambio incompatible de identidad y nombres públicos. | `README.md`; `pyproject.toml`; `CHANGELOG.md` |

Estados permitidos: `VIGENTE`, `REEMPLAZADA` y `DESCARTADA`. Una decisión
reemplazada conserva su fila y referencia el ID sucesor.

class Status:
    TABLE_START = "TSTART"
    TABLE_END = "TEND"
    TABLE_PREVIEW_END = "TPEND"  # a PREVIEW run finished the table; it does not block a later EXECUTE
    CHUNK_START = "CSTART"
    CHUNK_END = "CEND"
    GENERATED = "GENERATED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"

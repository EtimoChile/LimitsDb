class Status:
    TABLE_START = "TSTART"
    TABLE_END = "TEND"
    CHUNK_START = "CSTART"
    CHUNK_END = "CEND"
    LOB_START = "LSTART"  # a long single statement (LOB SOURCE_ILM path) holds the control row until TEND
    LOB_END = "LEND"
    # A PREVIEW uses the EXECUTE statuses prefixed with P; the two sets are disjoint
    PREVIEW_PREFIX = "P"
    TABLE_PREVIEW_START = "PTSTART"
    TABLE_PREVIEW_END = "PTEND"  # a PREVIEW run finished the table; it does not block a later EXECUTE
    CHUNK_PREVIEW_START = "PCSTART"
    CHUNK_PREVIEW_END = "PCEND"
    LOB_PREVIEW_START = "PLSTART"
    LOB_PREVIEW_END = "PLEND"
    PREVIEW_ERROR = "PERROR"
    GENERATED = "GENERATED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"

    PREVIEW_STATUSES = frozenset(
        {
            TABLE_PREVIEW_START,
            CHUNK_PREVIEW_START,
            CHUNK_PREVIEW_END,
            LOB_PREVIEW_START,
            LOB_PREVIEW_END,
            TABLE_PREVIEW_END,
            PREVIEW_ERROR,
        }
    )

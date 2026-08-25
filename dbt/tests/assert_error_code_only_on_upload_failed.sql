-- upload_failed carries an error_code; upload_started / upload_completed
-- carry JSON null, which staging reads as SQL NULL (reconciliation item 4).
select
    insert_id,
    event_type,
    error_code
from {{ ref('stg_events') }}
where (event_type = 'upload_failed' and error_code is null)
    or (event_type in ('upload_started', 'upload_completed') and error_code is not null)

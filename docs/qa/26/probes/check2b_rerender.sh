set -e
echo "--- before"
python -c "
import json; m=json.load(open('/tmp/qa/media/fixed/manifest.json'))
print('version:', m['version']); print('R_s_2000:', m['clips']['R_s_2000']['text'])"
python -c "
from ahbo.core.db import open_db
db = open_db('/tmp/qa/ahbo.sqlite')
db.execute(\"UPDATE med_schedule SET med_names='[\\\"bp\\\",\\\"dm\\\"]' WHERE slot_id='s_2000'\")
print('slot s_2000 meds -> bp+dm')"
echo "--- re-render"
python -m ahbo.ops.render_fixed
echo "--- after"
python -c "
import json; m=json.load(open('/tmp/qa/media/fixed/manifest.json'))
print('version:', m['version']); print('R_s_2000:', m['clips']['R_s_2000']['text'])
import os
print('R_s_2000.m4a mtime:', os.path.getmtime('/tmp/qa/media/fixed/R_s_2000.m4a'))"

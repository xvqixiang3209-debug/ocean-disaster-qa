"""One field vocabulary for product routing, corrections and follow-ups."""
import re

LABELS = {'wave': '海浪', 'wind': '海面风', 'pressure': '气压'}


def requested_fields(text):
    """Explicit fields only, in stable order; a correction target wins.

    An empty list means unspecified, not that every source provides all fields.
    Keep this independent of the map/report choice and of a dataset's fields.
    """
    q = str(text or '').split('本轮问题：')[-1]
    changes = list(re.finditer(r'换成|改成|切换到|切换为|改为', q))
    if changes:
        q = q[changes[-1].end():]
    wave = bool(re.search(r'海浪|波高|浪高|涌浪|浪场|风浪|风和浪|浪和风|浪.{0,3}(?:最高|最大|更高|更大)', q))
    wind = bool(re.search(r'风速|风场|海面风|海上风|10米风|风浪|风和浪|浪和风|^(?:风)(?:图|呢|[，,。？?]|$)', q))
    return [name for name, hit in (('wave', wave), ('wind', wind), ('pressure', '气压' in q)) if hit]

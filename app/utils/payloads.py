"""Request-payload validation that needs no database (unit-testable)."""
MAX_BULK = 100


def parse_bulk_review(data):
    """Returns (ids, action, error). Bulk review only supports accept/reject; edits and comments are per item."""
    data = data if isinstance(data, dict) else {}
    action = data.get("action")
    if action not in {"accept", "reject"}:
        return None, None, "action must be accept or reject."
    ids = data.get("item_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        return None, None, "item_ids must be a non-empty list of integers."
    ids = list(dict.fromkeys(ids))
    if len(ids) > MAX_BULK:
        return None, None, "At most %d items can be reviewed at once." % MAX_BULK
    return ids, action, None

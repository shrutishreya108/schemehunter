"""Status arrives as an SMS or email the user forwards (portals sit behind their OTP). LLD §14.

The checker only finds the right unread message; interpret_response reads it.
"""

from typing import Any

from app.status.base import CheckResult

LOOKBACK = 20


class UserForwardsChecker:
    def check(self, conn: Any, app: Any) -> CheckResult:
        """Latest unprocessed inbound message that mentions this application.

        A message "mentions" it if it contains the reference number (or its last digits) or the scheme id. If it
        names nothing but this is the person's only open application, the latest unread message is taken.
        """
        rows = conn.execute(
            "select id, body from messages where case_id = %s and direction = 'in' and processed = false "
            "order by created_at desc limit %s",
            (app.case_id, LOOKBACK),
        ).fetchall()
        if not rows:
            return CheckResult("nothing")
        needles = [app.scheme_id.casefold()]
        if app.ref_number:
            ref = app.ref_number.casefold()
            needles += [ref, ref.rsplit("/", 1)[-1]]
        for row in rows:
            body = (row["body"] or "").casefold()
            if any(n and n in body for n in needles):
                return CheckResult("text", row["body"], str(row["id"]))
        open_apps = conn.execute(
            "select count(*) as n from applications where case_id = %s "
            "and state not in ('CLOSED', 'MISSED', 'RETRY_NEXT_CYCLE', 'SANCTIONED')",
            (app.case_id,),
        ).fetchone()["n"]
        if open_apps == 1:
            return CheckResult("text", rows[0]["body"], str(rows[0]["id"]))
        return CheckResult("nothing")

"""Vercel entry point.

Vercel looks for `app` in a file under api/. Everything real lives in
`webapp.api`; this only re-exports it.

Read `DEPLOYMENT.md` before relying on a Vercel deployment: serverless gives
each request its own filesystem, so sessions and the corpus do not survive
between requests. The app detects this and says so in the UI rather than
appearing to save things it is discarding.
"""

from webapp.api import app

__all__ = ["app"]

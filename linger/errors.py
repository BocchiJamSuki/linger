class UserError(Exception):
    """Locally authored, safe-to-display error; never raw service or transport content.

    `fatal` errors (bad key, no quota, rejected request) stop the session; others are retried.
    """

    def __init__(self, message, *, fatal=False):
        super().__init__(message)
        self.fatal = fatal

class PrismError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, **details):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details

    def as_dict(self):
        return {"code": self.code, "message": self.message, "details": self.details}

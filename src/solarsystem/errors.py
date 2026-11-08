class SolarError(Exception):
    """调用者能直接展示给用户的失败。"""

    def __init__(self, message: str, exit_code: int = 1, outputs=None):
        super().__init__(message)
        self.exit_code = exit_code
        self.outputs = list(outputs or [])


class Cancelled(SolarError):
    def __init__(self, message: str):
        super().__init__(message, exit_code=1)
